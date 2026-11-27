"""Deterministic autofix + lint for a changed tree. No LLM, no network state, no providers or backends needed.

run_all(root, changed, checks, autofix) -> (errors, fixed)
  autofix: formatters whose output is simply kept (terraform-fmt). They only touch the files that changed.
  checks:  linters that report; anything they report goes back to the agent as text.
Everything operates on the CHANGED files only, so a repo's old debt never blocks a small change.
"""
import os, re, subprocess

YAMLLINT = "{extends: default, rules: {line-length: disable, document-start: disable, indentation: {spaces: 2, indent-sequences: consistent}, comments: {min-spaces-from-content: 1}, truthy: {check-keys: false}}}"
MAX_LINE = 240


def run(cmd, cwd, stdin=None, timeout=180):
    try:
        r = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: tool is not installed in the gate image"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out after {timeout}s"
    return r.returncode, (r.stdout + r.stderr).strip()


ANSI = re.compile(r"\x1b\[[0-9;]*m")
SCHEMA = re.compile(r"problem validating schema\. Check JSON formatting: jsonschema validation failed with '[^']*' - ")


def tidy(l):
    return SCHEMA.sub("schema error: ", ANSI.sub("", l)).replace("\u2577", "").replace("\u2575", "").replace("\u2502", "").strip()


def lines(tag, out):
    return [f"[{tag}] {tidy(l)[:MAX_LINE]}" for l in out.splitlines() if tidy(l)]


def ext(changed, *exts):
    return [f for f in changed if f.endswith(exts)]


def is_template(f):          # Go-templated chart files are not plain YAML
    return "/templates/" in "/" + f


def chart_dirs(root, changed):
    out = set()
    for f in changed:
        d = os.path.dirname(f)
        while True:
            if os.path.exists(os.path.join(root, d, "Chart.yaml")):
                out.add(d or "."); break
            if not d:
                break
            d = os.path.dirname(d)
    return sorted(out)


def k8s_manifests(root, changed):
    out = []
    for f in ext(changed, ".yaml", ".yml"):
        if is_template(f) or os.path.basename(f) in ("Chart.yaml", "values.yaml") or f.startswith(".github/"):
            continue
        try:
            text = open(os.path.join(root, f), errors="ignore").read()
        except OSError:
            continue
        if "apiVersion:" in text and "kind:" in text:
            out.append(f)
    return out


# ---------------------------------------------------------------- autofix
def fix_terraform_fmt(root, changed):
    """terraform fmt, one changed file at a time (a recursive run from the root could rewrite files nobody touched)."""
    fixed, errs = [], []
    for f in ext(changed, ".tf", ".tfvars"):
        rc, out = run(["terraform", "fmt", "-no-color", f], root)
        if rc:                # cannot be formatted: a syntax error, which only the agent can fix
            errs += lines("terraform-fmt", out)
        elif out.strip():     # fmt prints the name of each file it rewrote
            fixed.append(f)
    return fixed, errs


# ---------------------------------------------------------------- checks
def chk_json(root, changed):
    errs = []
    for f in ext(changed, ".json"):
        rc, out = run(["jq", "empty", f], root)
        if rc:
            errs += lines("json", f"{f}: {out}")
    return errs


def chk_yaml(root, changed):
    files = [f for f in ext(changed, ".yaml", ".yml") if not is_template(f)]
    if not files:
        return []
    rc, out = run(["yamllint", "-d", YAMLLINT, "-f", "parsable", *files], root)
    return lines("yaml", out) if rc else []


def chk_shell(root, changed):
    files = ext(changed, ".sh")
    if not files:
        return []
    rc, out = run(["shellcheck", "-S", "warning", "-f", "gcc", *files], root)
    return lines("shell", out) if rc else []


def chk_helm(root, changed):
    """helm lint only. No `helm template` (it cannot render library charts, and rendering is not linting) and no schema downloads."""
    errs = []
    for d in chart_dirs(root, changed):
        rc, out = run(["helm", "lint", "--strict", d], root)
        if rc:
            errs += lines(f"helm lint {d}", "\n".join(l for l in out.splitlines() if "ERROR" in l or "WARNING" in l or "failed" in l))
    return errs


def chk_kubernetes(root, changed):
    files = k8s_manifests(root, changed)
    if not files:
        return []
    rc, out = run(["kubeconform", "-strict", "-ignore-missing-schemas", "-summary", *files], root)
    return lines("kubernetes", out) if rc else []


def chk_terraform(root, changed):
    """fmt is already applied by autofix; this catches files it could not format and anything left unformatted."""
    files = ext(changed, ".tf", ".tfvars")
    if not files:
        return []
    rc, out = run(["terraform", "fmt", "-check", "-diff", "-no-color", *files], root)
    return lines("terraform", out) if rc else []


def chk_tflint(root, changed):
    errs = []
    for d in sorted({os.path.dirname(f) or "." for f in ext(changed, ".tf")}):
        rc, out = run(["tflint", "--chdir", d, "--minimum-failure-severity=error", "--format=compact", "--no-color"], root)
        if rc:
            errs += lines(f"tflint {d}", out)
    return errs


AUTOFIX = {"terraform-fmt": fix_terraform_fmt}
CHECKS = {"json": chk_json, "yaml": chk_yaml, "shell": chk_shell, "helm": chk_helm,
          "kubernetes": chk_kubernetes, "terraform": chk_terraform, "tflint": chk_tflint}


# Anti-cheating gates, deterministic like the linters: the agent may not silence a check, weaken a test or commit a credential to get to green.
SUPPRESS = re.compile(r"#\s*(noqa|nosec|shellcheck\s+disable|yamllint\s+disable|tflint-ignore)|//\s*(nolint|eslint-disable)|@pytest\.mark\.(skip|xfail)|\.skip\(|\bt\.Skip\(|@Disabled|@Ignore|\bxit\(|\bxdescribe\(", re.I)
ASSERTION = re.compile(r"\bassert|\bexpect\(|\.should|\brequire\.|\bt\.(Error|Fatal)", re.I)
TESTFILE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec)\.[a-z]+$|(^|/)test_[^/]*$")
SECRETS = (("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")), ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
           ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}")), ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
           ("hardcoded credential", re.compile(r"(password|passwd|secret|token|api[_-]?key)[\"']?\s*[:=]\s*[\"'](?![^\"']*(\$\{|var\.|<|\{\{|example|changeme|xxx))[^\"'\s]{16,}[\"']", re.I)))


TFEXEC = re.compile(r'^\s*data\s+"external"|^\s*provisioner\s+"(local|remote)-exec"|source\s*=\s*"hashicorp/external"')   # code that runs during plan/apply with the pipeline's credentials


def guard(name, old, new):
    """Checks on one changed file; old is None for a new file. Only lines the agent added or removed count, so old debt never blocks.
    shortcut: line-set diff, not a real diff, so a moved line is invisible; upgrade if that ever hides a tampering."""
    if b"\0" in new:
        return []
    n = new.decode(errors="replace").splitlines(); o = old.decode(errors="replace").splitlines() if old is not None else []
    added = [l for l in n if l not in set(o)]; removed = [l for l in o if l not in set(n)]
    out = [f"[guard] {name}: adds a check suppression ({l.strip()[:80]}); fix the finding instead of silencing it" for l in added if SUPPRESS.search(l)][:3]
    if name.endswith((".tf", ".tf.json")):
        out += [f"[guard] {name}: adds code that runs during plan/apply ({l.strip()[:60]}): `external` data sources and local/remote-exec provisioners are not allowed" for l in added if TFEXEC.search(l)][:3]
    out += [f"[guard] {name}: looks like a committed secret ({k}); credentials never go in a repo" for l in added for k, rx in SECRETS if rx.search(l)][:3]
    if old is not None and TESTFILE.search(name):
        out += [f"[guard] {name}: removes an assertion ({l.strip()[:80]}); an existing test may not be weakened" for l in removed if ASSERTION.search(l)][:3]
    return out


def run_all(root, changed, checks, autofix):
    errs, fixed = [], []
    for name in autofix:
        if name not in AUTOFIX:
            errs.append(f"[config] unknown autofix {name!r}"); continue
        f, e = AUTOFIX[name](root, changed); fixed += f; errs += e
    for name in checks:
        if name == "playbook":                     # repo-specific, handled by the gate (playbook-check.sh)
            continue
        if name not in CHECKS:
            errs.append(f"[config] unknown check {name!r}"); continue
        errs += CHECKS[name](root, changed)
    seen, uniq = set(), []
    for e in errs:                      # the same finding from fmt, validate-style and tflint should appear once
        k = e.split("] ", 1)[-1]
        if k not in seen:
            seen.add(k); uniq.append(e)
    return uniq, fixed
