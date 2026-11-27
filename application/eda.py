#!/usr/bin/env python3
"""event-driven-ai services in one file: intake (service catalog + queue), gate (validator + publisher), worker (see worker.py)."""
import lint, base64, glob, hashlib, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time, urllib.error, urllib.parse, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import redis

R = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True, socket_timeout=30)
CATALOG = json.load(open(os.environ.get("CATALOG", "/etc/eda/catalog.json")))
MAX_BODY = 20 * 1024 * 1024

def reply(h, code, obj, raw=None, ctype="application/json"):
    body = raw if raw is not None else json.dumps(obj).encode()
    h.send_response(code); h.send_header("Content-Type", ctype); h.send_header("Content-Length", str(len(body))); h.end_headers(); h.wfile.write(body)

def body_of(h):
    """Request body: Content-Length, or chunked (the Substrate egress proxy forwards uploads chunked)."""
    if "chunked" in h.headers.get("Transfer-Encoding", "").lower():
        out = b""
        while True:
            size = int(h.rfile.readline().split(b";")[0].strip() or b"0", 16)
            if size == 0:
                while h.rfile.readline().strip(): pass     # trailers up to the final blank line
                return out
            out += h.rfile.read(size); h.rfile.readline()
            if len(out) > MAX_BODY: raise ValueError("body too large")
    n = int(h.headers.get("Content-Length", 0))
    if n > MAX_BODY: raise ValueError("body too large")
    return h.rfile.read(n)

# ---------- job record: what the UI shows (status/stage/title/timestamps) and an event log ----------
STAGES = ["queued", "agent", "validate", "publish", "ci", "done"]
def now_iso(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def setf(jid, **fields):
    R.hset(f"job:{jid}", mapping={**{k: str(v) for k, v in fields.items()}, "updated_at": now_iso()})

def emit(jid, stage, message, level="info"):
    """Append one line to the job's event log (shown live in the UI). Never raises: logging must not break a job."""
    try:
        R.rpush(f"events:{jid}", json.dumps({"ts": now_iso(), "stage": stage, "level": level, "message": str(message)[:600]}))
    except Exception: pass

def enqueue(job):
    jid = job["id"]; t = now_iso()
    title = job["instruction"].split(". ")[0].split("\n")[0][:80]
    R.hset(f"job:{jid}", mapping={"status": "queued", "stage": "queued", "title": title, "created_at": t, "updated_at": t, "job": json.dumps(job)})
    R.zadd("jobs:all", {jid: time.time()}); R.lpush("jobs", jid)
    emit(jid, "queued", f"accepted for {job['service']} ({job['repo']}), branch {job['branch']}" + (", dry run" if job["dry_run"] else ""))

def job_view(jid):
    d = R.hgetall(f"job:{jid}")
    if not d: return None
    job = json.loads(d.pop("job", "null") or "null") or {}
    v = {**job, **d, "id": jid}
    st = d.get("status", "queued")
    v["css"] = {"queued": "queued", "running": "in_progress", "agent-done": "in_progress", "ci-waiting": "in_progress", "validated-dry-run": "finished", "no-change": "finished", "published": "finished", "failed": "failed", "ci-failed": "failed", "ci-timeout": "failed"}.get(st, "queued")
    v["label"] = {"validated-dry-run": "validated (dry run)", "no-change": "no change made", "agent-done": "checking", "ci-waiting": "waiting for CI"}.get(st, st)
    return v

def list_jobs(n=50):
    return [v for v in (job_view(j) for j in R.zrevrange("jobs:all", 0, n - 1)) if v]

def job_events(jid):
    return [json.loads(x) for x in R.lrange(f"events:{jid}", 0, -1)]

def job_subs(jid):
    return [json.loads(x) for x in R.lrange(f"subs:{jid}", 0, -1)]

# ---------- intake: validate the event against the service catalog, make canonical JSON, enqueue ----------
def source_of(ev):
    src = ev.get("source") if isinstance(ev.get("source"), dict) else {"type": "api"}
    return {k: str(v)[:120] for k, v in src.items() if k in ("type", "key", "event", "reporter", "note")}

ACTOR = "agent:event-driven-ai"
UNATTRIBUTED = {"subject": "unattributed", "name": "", "via": "unauthenticated-intake"}

def branch_name(key, ins, jid):
    """<TICKET>/<what-the-change-is>, e.g. SRE-13168/nginx-applies-content-security-policy-header. No ticket: <what>-<id6>, which can never equal a base branch."""
    slug = ""
    for w in re.findall(r"[a-z0-9]+", ins.strip().splitlines()[0].lower())[:6]:   # whole words only, at most 48 chars
        if len(slug) + len(w) + 1 > 48: break
        slug = f"{slug}-{w}" if slug else w
    slug = slug or "change"
    return f"{key}/{slug}" if key else f"{slug}-{jid[:6]}"

def canonical_job(ev, trusted=False):
    """trusted=True only for callers that authenticated the requester themselves (the HMAC-checked Jira webhook).
    Anyone else cannot name the user the agent acts for, or claim a Jira source: both come from the caller, not the request body."""
    if not isinstance(ev, dict): raise ValueError("event must be a JSON object")
    svc = ev.get("service"); ins = ev.get("instruction")
    if svc not in CATALOG: raise ValueError(f"unknown service {svc!r}; catalog has {sorted(CATALOG)}")
    if not isinstance(ins, str) or not 10 <= len(ins) <= 4000: raise ValueError("instruction must be a string of 10-4000 chars")
    key = ev.get("ticket_key", "")
    if key and not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", key): raise ValueError("ticket_key must look like SRE-123")
    c = CATALOG[svc]; jid = uuid.uuid4().hex[:12]
    branch = branch_name(key, ins, jid)    # decided here, once; the agent never picks it
    return {"id": jid, "service": svc, "ticket_key": key, "repo": c["repo"], "base_branch": c["base_branch"], "branch": branch,
            "allowed_paths": c["allowed_paths"], "owner": c["owner"], "ci": c.get("ci", True), "lint": c.get("lint", []), "autofix": c.get("autofix", []),
            "instruction": ins.strip(), "dry_run": bool(ev.get("dry_run", False)), "source": source_of(ev) if trusted else {"type": "api"},
            "actor": ACTOR, "on_behalf_of": {k: str(v)[:120] for k, v in ev["on_behalf_of"].items()} if trusted and isinstance(ev.get("on_behalf_of"), dict) else UNATTRIBUTED}

class Intake(BaseHTTPRequestHandler):
    def do_POST(self):
        import ui, jira
        if jira.handle_post(self) or ui.handle_post(self): return
        if self.path != "/events": return reply(self, 404, {"error": "not found"})
        try:
            job = canonical_job(json.loads(body_of(self)))
        except (ValueError, json.JSONDecodeError) as e:
            return reply(self, 400, {"error": str(e)})
        enqueue(job)
        reply(self, 202, {"id": job["id"], "status": "queued"})
    def do_GET(self):
        import ui
        if ui.handle_get(self): return
        m = re.fullmatch(r"/jobs/([a-f0-9]{12})", self.path)
        if not m: return reply(self, 404, {"error": "not found"})
        d = R.hgetall(f"job:{m.group(1)}"); d.pop("job", None)
        reply(self, 200 if d else 404, d or {"error": "unknown job"})

# ---------- gate: Box B (deterministic validator, no LLM) and Box C (publisher, only the worker may call) ----------
DATA = "/data"
def sh(cmd, cwd=None, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    if check and r.returncode: raise RuntimeError(scrub((r.stderr or r.stdout).strip()[-1500:]))
    return r

def scrub(t):
    t = str(t)
    for tok in all_tokens(): t = t.replace(tok, "***")
    return t

GIT_SECRETS = "/run/secrets/git"
def _read(name):
    p = f"{GIT_SECRETS}/{name}"
    return open(p).read().strip() if os.path.isfile(p) else ""
def token(repo=""): return _read(repo.split("/")[0].lower()) or _read("token")   # file named after the owner, else the shared "token"
def all_tokens(): return [t for n in (os.listdir(GIT_SECRETS) if os.path.isdir(GIT_SECRETS) else []) if (t := _read(n))]
def remote(job):
    tok = token(job["repo"])
    return f"https://x-access-token:{tok}@github.com/{job['repo']}.git" if tok else f"https://github.com/{job['repo']}.git"
def jobof(jid): return json.loads(R.hget(f"job:{jid}", "job") or "null")

def fresh_clone(job, dest):
    br = job.get("work_branch") or job["base_branch"]      # a CI fix round continues from the PR branch, not from base
    sh(["git", "clone", "-q", "--depth", "1", "--branch", br, remote(job), dest])
    sh(["git", "fetch", "-q", "--depth", "1", "origin", br], cwd=dest)

def allowed(path, job):   # "*" in allowed_paths means the whole repo, but never git internals
    return (not path.startswith("/") and ".." not in path.split("/") and path.split("/")[0] != ".git"
            and any(a == "*" or path == a or (a.endswith("/") and path.startswith(a)) for a in job["allowed_paths"]))

PROTECTED = re.compile(r"^(\.github/|\.buildkite/|\.circleci/|Jenkinsfile$|\.gitlab-ci\.yml$)|(^|/)(CODEOWNERS|\.pre-commit-config\.yaml|\.tflint\.hcl|\.yamllint(\.ya?ml)?|\.shellcheckrc)$")
def protected(path, job):   # CI and lint config run with the repo's secrets or judge the agent's own work: the agent never edits them (catalog `allow_protected` opts a repo out)
    return not job.get("allow_protected") and bool(PROTECTED.search(path))

def diff_of(job, cap=60000):
    """What the agent's change looks like against base (the gate's overlay on a fresh clone), for the independent reviewer."""
    r = f"{DATA}/work/{job['id']}/r"
    sh(["git", "add", "-N", "."], cwd=r); d = sh(["git", "diff", "HEAD", "--no-color"], cwd=r).stdout
    return d[:cap] + (f"\n... diff truncated at {cap} chars ({len(d)} total)" if len(d) > cap else "")

SUBMIT_SH = r"""#!/bin/sh
# Uploads ONLY what changed since the snapshot (plus PR.md), retries transport failures, prints the gate's JSON reply.
cd "$(dirname "$0")/.." || exit 1
[ -f PR.md ] || { echo '{"ok":false,"message":"write PR.md first: line 1 is the PR title, the rest says where you put the change and why"}'; exit 1; }
find . -type f ! -path './.eda/*' ! -path './.git/*' | sed 's|^\./||' | sort > /tmp/eda-all
: > /tmp/eda-changed
while IFS= read -r f; do
  h=$(sha256sum -- "$f" | cut -d' ' -f1)
  grep -qxF -- "$h  $f" .eda/manifest || printf '%s\n' "$f" >> /tmp/eda-changed
done < /tmp/eda-all
tar czf /tmp/eda-submit.tgz -T /tmp/eda-changed || exit 1
base64 /tmp/eda-submit.tgz > /tmp/eda-submit.b64 || exit 1   # wget --post-file stops at the first NUL byte, so send text, not binary
for i in 1 2 3; do
  out=$(wget -qO- -T 120 --header='Content-Type: application/gzip' --post-file=/tmp/eda-submit.b64 "@GATE@/submit/@JOB@?enc=b64" 2>/dev/null)
  case "$out" in '{'*) echo "$out"; exit 0;; esac
  sleep 2
done
echo '{"ok":false,"message":"the upload failed three times at the transport level; run this script again"}'; exit 1
"""

def snapshot(job, restore=False):
    """Repo at base (no .git) + .eda/ helper. restore=True overlays the job's last upload so a fresh sandbox can continue the work."""
    gate = os.environ.get("GATE_PUBLIC_URL", "http://gate.eda.svc.cluster.local:8080")
    with tempfile.TemporaryDirectory() as t:
        r = f"{t}/r"; fresh_clone(job, r); shutil.rmtree(f"{r}/.git")
        manifest = []
        for d, _, fs in os.walk(r):
            for f in fs:
                path = os.path.join(d, f); manifest.append(f"{hashlib.sha256(open(path, 'rb').read()).hexdigest()}  {os.path.relpath(path, r)}")
        os.makedirs(f"{r}/.eda"); open(f"{r}/.eda/manifest", "w").write("\n".join(sorted(manifest)) + "\n")
        open(f"{r}/.eda/submit.sh", "w").write(SUBMIT_SH.replace("@GATE@", gate).replace("@JOB@", job["id"]))
        last = f"{DATA}/work/{job['id']}.last.tgz"
        if restore and os.path.exists(last):
            with tarfile.open(last, "r:gz") as tf:
                for m in tf.getmembers():
                    name = m.name.removeprefix("./")
                    if m.isfile() and (name == "PR.md" or allowed(name, job)):
                        os.makedirs(os.path.dirname(f"{r}/{name}"), exist_ok=True); open(f"{r}/{name}", "wb").write(tf.extractfile(m).read())
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf: tf.add(r, arcname=".")
        return buf.getvalue()

SKIP_DIRS = ".git|node_modules|vendor|.terraform|__pycache__|.venv|dist|build"
DOC_CAPS = (("CLAUDE.md", 16000), ("AGENTS.md", 8000), ("CONTRIBUTING.md", 6000), ("README.md", 4000))   # the repo's own rules first

def clip(text, n):
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + "\n[... truncated by the gate: open the file for the rest]"

def brief(job):
    """Deterministic, LLM-free orientation for ANY repo: what may change, the layout, and the repo's own rules (CLAUDE.md, AGENTS.md,
    CONTRIBUTING.md, README.md, each capped). A plugin index is added when the repo has one. Same bytes for every job on the same
    commit, so it sits in the cached prefix instead of costing exploration calls."""
    with tempfile.TemporaryDirectory() as t:
        r = f"{t}/r"; fresh_clone(job, r)
        sha = sh(["git", "rev-parse", "--short", "HEAD"], cwd=r).stdout.strip()
        tree = lambda depth, path=".": sh(["tree", "-L", str(depth), "--noreport", "-I", SKIP_DIRS, "--dirsfirst", path], cwd=r, check=False).stdout.rstrip()
        out = [f"REPO BRIEF for {job['repo']}@{sha} (generated by the gate; do not re-read the files quoted here)",
               "## You may change only these paths\n" + "\n".join(f"- {p}" for p in job["allowed_paths"]),
               "## Layout (depth 2)\n" + clip(tree(2), 3000)]
        for p in job["allowed_paths"]:                                       # a deeper look where the agent is allowed to work
            if p.endswith("/") and os.path.isdir(f"{r}/{p}"): out.append(f"## Inside {p} (depth 3)\n" + clip(tree(3, p), 3000))
        mp = f"{r}/.claude-plugin/marketplace.json"
        if os.path.exists(mp):
            out.append("## Plugins (name version category: description)")
            out += [f"- {p['name']} {p['version']} ({p.get('category','')}): {p.get('description','')[:90]}" for p in json.load(open(mp))["plugins"]]
        for name, cap in DOC_CAPS:
            f = f"{r}/{name}"
            if os.path.isfile(f): out.append(f"## {name}\n" + clip(open(f, errors="replace").read(), cap))
        return "\n\n".join(out)

KNOWLEDGE_CAP, FILE_CAP = 60000, 8000

def knowledge():
    """The agent's rules, cards and skills (agent/AGENTS.md, agent/cards/*.md, agent/skills/*/SKILL.md) from the repo the catalog marks
    `knowledge: true`, read from its base branch on EVERY job, so a merged skill is live on the next run. A cheap ls-remote finds the
    current commit and the text is cached per commit. Returns "" when no repo is marked."""
    c = next((v for v in CATALOG.values() if v.get("knowledge")), None)
    if not c: return ""
    src = {"repo": c["repo"], "base_branch": c["base_branch"]}
    sha = sh(["git", "ls-remote", remote(src), f"refs/heads/{c['base_branch']}"]).stdout.split()[0]
    cache = f"{DATA}/knowledge/{sha}.txt"
    if os.path.exists(cache): return open(cache).read()
    with tempfile.TemporaryDirectory() as t:
        r = f"{t}/r"; fresh_clone(src, r)
        part = lambda path: clip(open(path, errors="replace").read(), FILE_CAP)
        out = [f"AGENT KNOWLEDGE from {c['repo']}@{sha[:7]}: your rules, cards and skills, already loaded. Follow the card or skill that matches the task."]
        if os.path.isfile(f"{r}/agent/AGENTS.md"): out.append(part(f"{r}/agent/AGENTS.md"))
        for kind, pattern in (("Card", "agent/cards/*.md"), ("Skill", "agent/skills/*/SKILL.md")):
            for f in sorted(glob.glob(f"{r}/{pattern}")):
                name = os.path.basename(f)[:-3] if kind == "Card" else os.path.basename(os.path.dirname(f))
                out.append(f"## {kind}: {name}\n" + part(f))
        text = clip("\n\n".join(out), KNOWLEDGE_CAP)
    os.makedirs(os.path.dirname(cache), exist_ok=True); open(cache, "w").write(text)
    return text

LESSON_ID = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|\b[a-f0-9]{12}\b")     # ticket keys and job ids: one run's story, not a lesson
def lesson_check(job, root, changed):
    """Deterministic limits for a self-learning job (it may only write skills and cards). A lesson is general, short and bounded:
    no ticket keys, job ids or other target repos by name; cards at most 15 lines, skills 40 lines and 8000 chars (what the gate loads);
    and the whole agent/ knowledge stays under 80% of what the gate can load, so lessons never silently push a skill past the cut."""
    errs = []
    own = next((k for k, v in CATALOG.items() if v.get("knowledge")), None)
    names = {n for k, v in CATALOG.items() if k != own for n in (k, v["repo"])}
    for f in changed:
        text = open(f"{root}/{f}", errors="replace").read(); n = len(text.splitlines())
        card = f.startswith("agent/cards/"); limit = 15 if card else 40
        if n > limit: errs.append(f"[lesson] {f}: {n} lines; a {'card' if card else 'skill'} is at most {limit}. Say it shorter, or split the idea.")
        if not card and len(text) > 8000: errs.append(f"[lesson] {f}: {len(text)} characters; the gate cuts a skill at 8000.")
        if m := LESSON_ID.search(text): errs.append(f"[lesson] {f}: mentions {m.group()!r}; a lesson is general, with no ticket key or job id.")
        for name in sorted(names):
            if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text, re.I): errs.append(f"[lesson] {f}: names the target repo {name!r}; a lesson must help on other repos too."); break
    total = sum(len(open(p, errors="replace").read()) for p in glob.glob(f"{root}/agent/**/*", recursive=True) if os.path.isfile(p))
    if total > KNOWLEDGE_CAP * 0.8: errs.append(f"[lesson] agent/ holds {total} characters, over 80% of the {KNOWLEDGE_CAP} the gate loads: consolidate existing cards and skills before adding more.")
    return errs

def submit(job, data):
    """Overlay the uploaded tree on a fresh clone of base, run the deterministic lint. Returns (ok, message)."""
    w = f"{DATA}/work/{job['id']}"; shutil.rmtree(w, ignore_errors=True); os.makedirs(w)
    open(f"{DATA}/work/{job['id']}.last.tgz", "wb").write(data)    # lets a retry restore its work if the sandbox was reset
    fresh_clone(job, f"{w}/r")
    pr = None; learned = ""; changed = []; bad = []
    try: tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz"); members = tf.getmembers()
    except (EOFError, tarfile.TarError, OSError) as e:
        return False, f"upload is not a complete .tar.gz ({len(data)} bytes received: {e}). Rebuild the archive and upload it again."
    with tf:
        for m in members:
            name = m.name.removeprefix("./")
            if m.isdir() or name == "" or name.startswith(".eda/"): continue
            if not m.isfile(): return False, f"{name}: only regular files allowed"
            if name == "PR.md": pr = tf.extractfile(m).read().decode(); continue
            if name == "LEARNED.md": learned = tf.extractfile(m).read().decode(errors="replace").strip()[:2000]; continue   # a lesson for puck, never part of the target PR
            content = tf.extractfile(m).read(); dst = f"{w}/r/{name}"
            if os.path.exists(dst) and open(dst, "rb").read() == content: continue     # unchanged files are not changes
            if not allowed(name, job): return False, f"{name}: outside allowed paths {job['allowed_paths']}"
            if protected(name, job): return False, f"{name}: protected path (CI and lint config): the agent may not change it. Leave it as it is and describe the change you would make in PR.md"
            bad += lint.guard(name, open(dst, "rb").read() if os.path.exists(dst) else None, content)
            os.makedirs(os.path.dirname(dst), exist_ok=True); open(dst, "wb").write(content); changed.append(name)
    if (job.get("source") or {}).get("type") == "learning": bad += lesson_check(job, f"{w}/r", changed)
    if bad: return False, scrub("\n".join(bad))
    if not pr: return False, "PR.md missing: line 1 is the PR title, the rest is the description (where it went and why)"
    root = f"{w}/r"
    # Deterministic phase, no LLM: formatters fix files in place, linters report. Only reports go back to the agent.
    errs, fixed = lint.run_all(root, changed, job.get("lint", []), job.get("autofix", []))
    if errs: return False, scrub("\n".join(errs[:40]) + (f"\n... and {len(errs) - 40} more" if len(errs) > 40 else ""))
    if "playbook" in job.get("lint", []):
        r = sh(["/usr/local/bin/playbook-check.sh"], cwd=root, env={"PLAYBOOK_DIR": root}, check=False)
        if r.returncode: return False, scrub((r.stdout + r.stderr).strip()[-1500:])
    R.hset(f"job:{job['id']}", mapping={"validated": hashlib.sha256(data).hexdigest(), "pr": pr, "files": json.dumps(changed), **({"learned": learned} if learned else {}), "title": pr.splitlines()[0].lstrip("# ").strip()[:120] or "(untitled)"})
    return True, "checks passed" + (f"; auto-formatted: {', '.join(fixed)}" if fixed else "")

def obo_line(job):
    o = job.get("on_behalf_of") or {}
    return f"{job.get('actor', ACTOR)} on behalf of {o.get('subject', 'unattributed')}" + (f" ({o['name']})" if o.get("name") else "") + f", via {o.get('via', '?')}"

def gh(job, path, method="GET", data=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{job['repo']}/{path}", method=method, data=None if data is None else json.dumps(data).encode(),
        headers={"Authorization": f"Bearer {token(job['repo'])}", "Accept": "application/vnd.github+json", "User-Agent": "event-driven-ai"})
    return urllib.request.urlopen(req, timeout=30)

def branch_taken(w, branch):
    """True if the remote already has this branch. A rerun of a ticket reuses the branch name, and a push to it is rejected with a bare 'fetch first'."""
    return bool(sh(["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"], cwd=w).stdout.strip())

def publish(job):
    pr = R.hget(f"job:{job['id']}", "pr")
    if not R.hget(f"job:{job['id']}", "validated") or not pr: raise RuntimeError("no validated submission for this job")
    w = f"{DATA}/work/{job['id']}/r"
    branch = job["branch"]; fix = bool(job.get("work_branch"))
    title = pr.splitlines()[0].lstrip("# ").strip()[:200]; body = "\n".join(pr.splitlines()[1:]).strip()
    if job["ticket_key"] and job["ticket_key"] not in title: title = f"{job['ticket_key']}: {title}"
    if not fix and branch_taken(w, branch):
        raise RuntimeError(f"branch {branch} already exists on {job['repo']} (an earlier run of this ticket, or a closed PR whose branch was not deleted). Delete the branch on GitHub, then retry.")
    sh(["git", "checkout", "-q", "-B", branch], cwd=w)
    sh(["git", "add", "-A"], cwd=w)
    sh(["git", "-c", "user.name=event-driven-agent", "-c", "user.email=agent@localhost", "commit", "-q", "-m", "Address CI failures" if fix else title], cwd=w)
    sh(["git", "push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}"], cwd=w)   # explicit refspec, never forced: a fix round fast-forwards its own branch
    if fix:   # the PR exists: its title and body now describe the whole change as the agent last wrote it
        url = R.hget(f"job:{job['id']}", "pr_url")
        gh(job, f"pulls/{url.rsplit('/', 1)[1]}", "PATCH", {"title": title, "body": body}).read()
    else:
        url = json.load(gh(job, "pulls", "POST", {"title": title, "head": branch, "base": job["base_branch"], "draft": True, "body": body}))["html_url"]
    R.hset(f"job:{job['id']}", mapping={"status": "published", "pr_url": url, "branch": branch})
    return url

# ---------- CI: what GitHub says about the pushed branch ----------
CI_BAD = ("failure", "timed_out", "cancelled", "action_required", "error")

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k): return None

def job_log(job, job_id):
    """An Actions job log. GitHub answers 302 to a signed storage URL: follow it WITHOUT our token."""
    req = urllib.request.Request(f"https://api.github.com/repos/{job['repo']}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"Bearer {token(job['repo'])}", "Accept": "application/vnd.github+json", "User-Agent": "event-driven-ai"})
    try: return urllib.request.build_opener(_NoRedirect).open(req, timeout=30).read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"): return urllib.request.urlopen(e.headers["Location"], timeout=60).read().decode(errors="replace")
        raise

def log_tail(log, lines=40, cap=4000):
    ls = [re.sub(r"^\ufeff?\d{4}-\d\d-\d\dT[\d:.]+Z ", "", lint.ANSI.sub("", l)) for l in log.splitlines()]
    errs = [l for l in ls if "##[error]" in l][:15]
    return scrub("\n".join(errs + (["--- last lines ---"] if errs else []) + ls[-lines:]))[:cap]

def rollup(job):
    """GitHub's overall verdict for the branch head. It includes checks this token cannot list (CodeQL, Cycode), so it is the only way to see them without Checks: read.
    Returns {state, total} or None if GitHub will not say."""
    o, n = job["repo"].split("/")
    q = {"query": "query($o:String!,$n:String!,$r:String!){repository(owner:$o,name:$n){ref(qualifiedName:$r){target{... on Commit{statusCheckRollup{state contexts(first:1){totalCount}}}}}}}",
         "variables": {"o": o, "n": n, "r": "refs/heads/" + job["branch"]}}
    req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps(q).encode(), headers={"Authorization": f"Bearer {token(job['repo'])}", "User-Agent": "event-driven-ai"})
    try: d = json.load(urllib.request.urlopen(req, timeout=30))["data"]["repository"]["ref"]["target"]["statusCheckRollup"]
    except Exception: return None
    return {"state": d["state"], "total": d["contexts"]["totalCount"]} if d else None

def ci_status(job):
    """state: pending | none | success | failure. On failure, the tail of each failed check's log (at most 5): this is what a fix round is given."""
    sha = json.load(gh(job, "git/ref/heads/" + urllib.parse.quote(job["branch"], safe="/")))["object"]["sha"]
    hidden = None
    try:   # Checks: read also shows app checks (Cycode, CodeQL). Without it, fall back to the Actions jobs (they carry the logs) plus GitHub's overall rollup for the rest.
        runs = [{"name": c["name"], "id": c["id"], "app": c["app"]["slug"], "status": c["status"], "conclusion": c["conclusion"], "output": c.get("output")}
                for c in json.load(gh(job, f"commits/{sha}/check-runs?per_page=100"))["check_runs"]]
    except urllib.error.HTTPError as e:
        if e.code != 403: raise
        hidden = rollup(job)
        runs = [{"name": j["name"], "id": j["id"], "app": "github-actions", "status": j["status"], "conclusion": j["conclusion"], "output": None}
                for r in json.load(gh(job, f"actions/runs?head_sha={sha}&per_page=50"))["workflow_runs"] for j in json.load(gh(job, f"actions/runs/{r['id']}/jobs?per_page=100"))["jobs"]]
    stat = json.load(gh(job, f"commits/{sha}/status"))["statuses"]
    items = [{"name": c["name"], "id": c["id"], "app": c["app"], "done": c["status"] == "completed", "conclusion": c["conclusion"], "note": (c.get("output") or {}).get("title") or ""} for c in runs]
    items += [{"name": x["context"], "id": None, "app": "status", "done": x["state"] != "pending", "conclusion": x["state"], "note": x.get("description") or ""} for x in stat]
    pending = [i["name"] for i in items if not i["done"]]; bad = [i for i in items if i["done"] and i["conclusion"] in CI_BAD]
    state = "pending" if pending else "none" if not items else "failure" if bad else "success"
    total = len(items)
    if hidden:   # the rollup counts every check, including the ones this token cannot list
        total = max(total, hidden["total"])
        if hidden["state"] in ("PENDING", "EXPECTED") and state != "pending": state = "pending"; pending = ["checks this token cannot list"]
        elif hidden["state"] in ("FAILURE", "ERROR") and state != "failure": state = "blind"
        elif hidden["state"] == "SUCCESS" and state == "none": state = "success"
    out = {"sha": sha, "state": state, "total": total, "pending": pending, "failed": []}
    for i in bad[:5] if state == "failure" else []:
        try: log = log_tail(job_log(job, i["id"])) if i["app"] == "github-actions" else i["note"]
        except Exception as e: log = f"(log unavailable: {str(e)[:120]})"
        out["failed"].append({"name": i["name"], "conclusion": i["conclusion"], "log": log or "(no output)"})
    return out

class Gate(BaseHTTPRequestHandler):
    def do_GET(self):
        m = re.fullmatch(r"/(snapshot|restore|last|brief|knowledge|ci|diff)/([a-f0-9]{12})", self.path); job = m and jobof(m.group(2))
        if not job: return reply(self, 404, {"error": "unknown job"})
        if m.group(1) == "ci":
            try: return reply(self, 200, ci_status(job))
            except urllib.error.HTTPError as e: return reply(self, e.code if e.code in (401, 403, 404) else 502, {"error": f"GitHub answered {e.code} {scrub(e.reason)}"})
            except Exception as e: return reply(self, 502, {"error": scrub(e)})
        if m.group(1) == "diff":
            try: return reply(self, 200, None, raw=scrub(diff_of(job)).encode(), ctype="text/plain")
            except Exception as e: return reply(self, 500, {"error": scrub(e)})
        if m.group(1) == "knowledge":
            try: return reply(self, 200, None, raw=scrub(knowledge()).encode(), ctype="text/plain")
            except Exception as e: return reply(self, 500, {"error": scrub(e)})
        if m.group(1) == "brief":
            try: return reply(self, 200, None, raw=scrub(brief(job)).encode(), ctype="text/plain")
            except Exception as e: return reply(self, 500, {"error": scrub(e)})
        if m.group(1) == "last":
            f = f"{DATA}/work/{job['id']}.last.tgz"
            return reply(self, 200, None, raw=open(f, "rb").read(), ctype="application/gzip") if os.path.exists(f) else reply(self, 404, {"error": "no previous submission"})
        try: reply(self, 200, None, raw=snapshot(job, restore=(m.group(1) == "restore")), ctype="application/gzip")
        except Exception as e: reply(self, 500, {"error": str(e)})
    def do_POST(self):
        path, _, query = self.path.partition("?")
        m = re.fullmatch(r"/(submit|publish)/([a-f0-9]{12})", path); job = m and jobof(m.group(2))
        if not job: return reply(self, 404, {"error": "unknown job"})
        try:
            if m.group(1) == "submit":
                data = body_of(self)
                if "enc=b64" in query:
                    try: data = base64.b64decode(data, validate=False)
                    except Exception as e: return reply(self, 200, {"ok": False, "message": f"upload is not valid base64: {e}"})
                print("upload", job["id"], "content-length=%s" % self.headers.get("Content-Length"), "transfer-encoding=%s" % self.headers.get("Transfer-Encoding"), "got=%d bytes" % len(data), flush=True)
                ok, msg = submit(job, data); R.hset(f"job:{job['id']}", "last_error", "" if ok else msg)
                emit(job["id"], "validate", ("PASSED: " if ok else "REJECTED: ") + " | ".join(msg.splitlines()[:3])[:300], "info" if ok else "warn")
                R.rpush(f"subs:{job['id']}", json.dumps({"t": time.strftime("%H:%M:%S"), "ok": ok, "msg": msg[:400]})); print("submit", job["id"], ok, msg[:200].replace("\n", " | "), flush=True)
                if not ok: R.hdel(f"job:{job['id']}", "validated", "pr")
                return reply(self, 200, {"ok": ok, "message": msg})
            # publish is the worker's alone: it holds the key, the sandboxed agent does not
            if self.headers.get("X-Publish-Key") != open("/run/secrets/publish/key").read().strip(): return reply(self, 403, {"error": "forbidden"})
            body_of(self); reply(self, 200, {"url": publish(job)})
        except Exception as e:
            import traceback; print(scrub(traceback.format_exc()), flush=True)
            # submit errors come back as readable JSON the agent can act on; publish errors stay 500
            reply(self, 200 if m.group(1) == "submit" else 500, {"ok": False, "message": scrub(e)})

class JiraSim(BaseHTTPRequestHandler):
    """Stand-in for the part of Jira's REST API we write to: POST/GET /rest/api/3/issue/<KEY>/comment. Comments live in Redis."""
    def do_POST(self):
        m = re.fullmatch(r"/rest/api/3/issue/([A-Z][A-Z0-9]+-\d+)/comment", self.path)
        if not m: return reply(self, 404, {"error": "not found"})
        try: doc = json.loads(body_of(self))
        except json.JSONDecodeError: return reply(self, 400, {"error": "not JSON"})
        import jira
        text = jira.clean(jira.adf_to_text(doc.get("body")))
        R.rpush(f"jirasim:{m.group(1)}", json.dumps({"ts": now_iso(), "text": text}))
        reply(self, 201, {"id": str(R.llen(f"jirasim:{m.group(1)}")), "body": doc.get("body")})
    def do_GET(self):
        m = re.fullmatch(r"/rest/api/3/issue/([A-Z][A-Z0-9]+-\d+)/comment", self.path)
        if not m: return reply(self, 404, {"error": "not found"})
        reply(self, 200, {"comments": [json.loads(x) for x in R.lrange(f"jirasim:{m.group(1)}", 0, -1)]})

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "worker":
        import worker; worker.main()
    else:
        ThreadingHTTPServer(("", 8080), {"intake": Intake, "gate": Gate, "jira-sim": JiraSim}[mode]).serve_forever()
