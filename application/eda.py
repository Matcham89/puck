#!/usr/bin/env python3
"""event-driven-ai services in one file: intake (service catalog + queue), gate (validator + publisher), worker (see worker.py)."""
import lint, base64, glob, hashlib, io, json, os, re, shutil, subprocess, sys, tarfile, tempfile, time, urllib.request, uuid
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
STAGES = ["queued", "agent", "validate", "publish", "done"]
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
    v["css"] = {"queued": "queued", "running": "in_progress", "agent-done": "in_progress", "validated-dry-run": "finished", "published": "finished", "failed": "failed"}.get(st, "queued")
    v["label"] = {"validated-dry-run": "validated (dry run)", "agent-done": "checking"}.get(st, st)
    return v

def list_jobs(n=50):
    return [v for v in (job_view(j) for j in R.zrevrange("jobs:all", 0, n - 1)) if v]

def job_events(jid):
    return [json.loads(x) for x in R.lrange(f"events:{jid}", 0, -1)]

def job_subs(jid):
    return [json.loads(x) for x in R.lrange(f"subs:{jid}", 0, -1)]

def queue_status():
    jobs = list_jobs(200)
    count = lambda *st: sum(1 for j in jobs if j.get("status") in st)
    return {"depth": R.llen("jobs"), "running": count("running", "agent-done"), "failed": [j for j in jobs if j.get("status") == "failed"][:10]}

# ---------- intake: validate the event against the service catalog, make canonical JSON, enqueue ----------
def source_of(ev):
    src = ev.get("source") if isinstance(ev.get("source"), dict) else {"type": "api"}
    return {k: str(v)[:120] for k, v in src.items() if k in ("type", "key", "event", "reporter", "note")}

ACTOR = "agent:event-driven-ai"
UNATTRIBUTED = {"subject": "unattributed", "name": "", "via": "unauthenticated-intake"}

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
    branch = "learn/" + re.sub(r"[^a-z0-9-]", "-", (key or jid).lower())[:50]    # decided here, once; the agent never picks it
    return {"id": jid, "service": svc, "ticket_key": key, "repo": c["repo"], "base_branch": c["base_branch"], "branch": branch,
            "allowed_paths": c["allowed_paths"], "owner": c["owner"], "lint": c.get("lint", []), "autofix": c.get("autofix", []),
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
    tok = token()
    return str(t).replace(tok, "***") if tok else str(t)

def token(): return open("/run/secrets/git/token").read().strip() if os.path.exists("/run/secrets/git/token") else ""
def remote(job): return f"https://x-access-token:{token()}@github.com/{job['repo']}.git" if token() else f"https://github.com/{job['repo']}.git"
def jobof(jid): return json.loads(R.hget(f"job:{jid}", "job") or "null")

def fresh_clone(job, dest):
    sh(["git", "clone", "-q", "--depth", "1", "--branch", job["base_branch"], remote(job), dest])
    sh(["git", "fetch", "-q", "--depth", "1", "origin", job["base_branch"]], cwd=dest)

def allowed(path, job):
    return not path.startswith("/") and ".." not in path.split("/") and any(path == a or (a.endswith("/") and path.startswith(a)) for a in job["allowed_paths"])

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
DOC_CAPS = (("CLAUDE.md", 8000), ("AGENTS.md", 8000), ("CONTRIBUTING.md", 6000), ("README.md", 4000))   # the repo's own rules first

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

def submit(job, data):
    """Overlay the uploaded tree on a fresh clone of base, run the deterministic lint. Returns (ok, message)."""
    w = f"{DATA}/work/{job['id']}"; shutil.rmtree(w, ignore_errors=True); os.makedirs(w)
    open(f"{DATA}/work/{job['id']}.last.tgz", "wb").write(data)    # lets a retry restore its work if the sandbox was reset
    fresh_clone(job, f"{w}/r")
    pr = None; changed = []
    try: tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz"); members = tf.getmembers()
    except (EOFError, tarfile.TarError, OSError) as e:
        return False, f"upload is not a complete .tar.gz ({len(data)} bytes received: {e}). Rebuild the archive and upload it again."
    with tf:
        for m in members:
            name = m.name.removeprefix("./")
            if m.isdir() or name == "" or name.startswith(".eda/"): continue
            if not m.isfile(): return False, f"{name}: only regular files allowed"
            if name == "PR.md": pr = tf.extractfile(m).read().decode(); continue
            content = tf.extractfile(m).read(); dst = f"{w}/r/{name}"
            if os.path.exists(dst) and open(dst, "rb").read() == content: continue     # unchanged files are not changes
            if not allowed(name, job): return False, f"{name}: outside allowed paths {job['allowed_paths']}"
            os.makedirs(os.path.dirname(dst), exist_ok=True); open(dst, "wb").write(content); changed.append(name)
    if not pr: return False, "PR.md missing: line 1 is the PR title, the rest is the description (where it went and why)"
    root = f"{w}/r"
    # Deterministic phase, no LLM: formatters fix files in place, linters report. Only reports go back to the agent.
    errs, fixed = lint.run_all(root, changed, job.get("lint", []), job.get("autofix", []))
    if errs: return False, scrub("\n".join(errs[:40]) + (f"\n... and {len(errs) - 40} more" if len(errs) > 40 else ""))
    if "playbook" in job.get("lint", []):
        r = sh(["/usr/local/bin/playbook-check.sh"], cwd=root, env={"PLAYBOOK_DIR": root}, check=False)
        if r.returncode: return False, scrub((r.stdout + r.stderr).strip()[-1500:])
    R.hset(f"job:{job['id']}", mapping={"validated": hashlib.sha256(data).hexdigest(), "pr": pr, "title": pr.splitlines()[0].lstrip("# ").strip()[:120] or "(untitled)"})
    return True, "checks passed" + (f"; auto-formatted: {', '.join(fixed)}" if fixed else "")

def obo_line(job):
    o = job.get("on_behalf_of") or {}
    return f"{job.get('actor', ACTOR)} on behalf of {o.get('subject', 'unattributed')}" + (f" ({o['name']})" if o.get("name") else "") + f", via {o.get('via', '?')}"

def publish(job):
    pr = R.hget(f"job:{job['id']}", "pr")
    if not R.hget(f"job:{job['id']}", "validated") or not pr: raise RuntimeError("no validated submission for this job")
    w = f"{DATA}/work/{job['id']}/r"
    branch = job["branch"]
    title = pr.splitlines()[0].lstrip("# ").strip()[:200]; body = "\n".join(pr.splitlines()[1:]).strip()
    if job["ticket_key"] and job["ticket_key"] not in title: title = f"{job['ticket_key']}: {title}"
    sh(["git", "checkout", "-q", "-B", branch], cwd=w)
    sh(["git", "add", "-A"], cwd=w)
    sh(["git", "-c", "user.name=event-driven-agent", "-c", "user.email=agent@localhost", "commit", "-q", "-m", title, "-m", f"On-Behalf-Of: {obo_line(job)}"], cwd=w)
    sh(["git", "push", "-q", "origin", f"refs/heads/{branch}:refs/heads/{branch}"], cwd=w)   # explicit refspec, learn/* only
    req = urllib.request.Request(f"https://api.github.com/repos/{job['repo']}/pulls", method="POST", data=json.dumps(
        {"title": title, "head": branch, "base": job["base_branch"], "draft": True, "body": body + "\n\n---\n" f"Draft opened by {obo_line(job)}. The agent has its own identity and did not impersonate the requester. Merging needs a human review of this draft."}).encode(),
        headers={"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json", "User-Agent": "event-driven-ai"})
    url = json.load(urllib.request.urlopen(req, timeout=30))["html_url"]
    R.hset(f"job:{job['id']}", mapping={"status": "published", "pr_url": url, "branch": branch})
    return url

class Gate(BaseHTTPRequestHandler):
    def do_GET(self):
        m = re.fullmatch(r"/(snapshot|restore|last|brief|knowledge)/([a-f0-9]{12})", self.path); job = m and jobof(m.group(2))
        if not job: return reply(self, 404, {"error": "unknown job"})
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
