"""Run in the gate image (needs git):  sh application/tests/run.sh
CI after the draft PR: gate status and logs, the worker's wait / fix-round loop, and a fix round that fast-forwards the PR branch."""
import io, json, os, subprocess, sys, tarfile, tempfile, types, urllib.error
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat

class FakeR:
    def __init__(self): self.h, self.keys = {}, {}
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.keys: return None
        self.keys[k] = v; return True
    def rpush(self, *a): pass
    def lpush(self, *a): pass
    def hincrby(self, k, f, n): d = self.h.setdefault(k, {}); d[f] = int(d.get(f, 0)) + n; return d[f]
    def zrevrange(self, k, a, b): return ["aaaaaaaaaaaa"]
import eda, worker
eda.R = worker.R = FakeR(); EV = []
worker.emit = lambda jid, st, msg, lvl="info": EV.append((st, msg))
worker.jira.notify = lambda *a: "skipped"
worker.CI_POLL = 0; worker.PUBLISH_KEY = lambda: "k"
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c

# ---- gate: ci_status over a fake GitHub
class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
def fake_gh(runs, statuses=()):
    def gh(job, path, method="GET", data=None):
        if path.startswith("git/ref/heads/"): return Resp(json.dumps({"object": {"sha": "abc123"}}).encode())
        if "check-runs" in path: return Resp(json.dumps({"check_runs": runs}).encode())
        if path.endswith("/status"): return Resp(json.dumps({"statuses": list(statuses)}).encode())
        raise AssertionError(path)
    return gh
def run_(i, app, status, concl, title=""): return {"id": i, "name": f"check{i}", "app": {"slug": app}, "status": status, "conclusion": concl, "output": {"title": title}}
J = {"repo": "o/r", "branch": "SRE-1/x"}
raw = "﻿2026-11-24T18:08:05.4Z \x1b[31mstep ok\x1b[0m\n2026-11-24T18:08:06.0Z ##[error]Process completed with exit code 2.\n" + "\n".join(f"2026-11-24T18:08:07.0Z line {n}" for n in range(100))
eda.job_log = lambda job, jid: raw
eda.gh = fake_gh([run_(1, "github-actions", "completed", "success"), run_(2, "github-actions", "in_progress", None)])
check(eda.ci_status(J)["state"] == "pending" and eda.ci_status(J)["pending"] == ["check2"], "a check still running means pending")
eda.gh = fake_gh([run_(1, "github-actions", "completed", "success"), run_(2, "github-actions", "completed", "skipped")])
check(eda.ci_status(J)["state"] == "success" and eda.ci_status(J)["total"] == 2, "all done, none bad (skipped is fine) means success")
eda.gh = fake_gh([])
check(eda.ci_status(J)["state"] == "none", "no checks at all means none")
eda.gh = fake_gh([run_(1, "github-actions", "completed", "failure"), run_(2, "cycode", "completed", "failure", "2 secrets found"), run_(3, "github-actions", "completed", "success")],
                 [{"context": "legacy/ci", "state": "success", "description": ""}])
st = eda.ci_status(J); f = {x["name"]: x for x in st["failed"]}
check(st["state"] == "failure" and set(f) == {"check1", "check2"}, "failed checks are reported, passing ones are not")
check(f["check2"]["log"] == "2 secrets found", "an app check (no Actions log) reports its own summary")
lg = f["check1"]["log"]
check(lg.startswith("##[error]Process completed") and "line 99" in lg and "\x1b" not in lg and "2026-11-24T" not in lg and len(lg) <= 4000, "Actions log: error lines first, then the tail, timestamps and colour stripped, capped")

# without Checks: read the gate falls back to the Actions jobs
def gh_no_checks(job, path, method="GET", data=None):
    if path.startswith("git/ref/heads/"): return Resp(json.dumps({"object": {"sha": "abc123"}}).encode())
    if "check-runs" in path: raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    if path.startswith("actions/runs?"): return Resp(json.dumps({"workflow_runs": [{"id": 5}]}).encode())
    if path.startswith("actions/runs/5/jobs"): return Resp(json.dumps({"jobs": [{"id": 11, "name": "Render (auth-ui)", "status": "completed", "conclusion": "failure"}, {"id": 12, "name": "Publish", "status": "completed", "conclusion": "success"}]}).encode())
    if path.endswith("/status"): return Resp(json.dumps({"statuses": []}).encode())
    raise AssertionError(path)
eda.rollup = lambda job: None; eda.gh = gh_no_checks; st = eda.ci_status(J)
check(st["state"] == "failure" and st["total"] == 2 and st["failed"][0]["name"] == "Render (auth-ui)" and "##[error]" in st["failed"][0]["log"], "no Checks permission: Actions jobs and their logs still drive CI status")

# without Checks: read, GitHub's rollup still says what the hidden checks (CodeQL, Cycode) did
def gh_readable_ok(job, path, method="GET", data=None):
    if "check-runs" in path: raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    if path.startswith("actions/runs/5/jobs"): return Resp(json.dumps({"jobs": [{"id": 12, "name": "Publish", "status": "completed", "conclusion": "success"}]}).encode())
    return gh_no_checks(job, path, method, data)
eda.gh = gh_readable_ok
eda.rollup = lambda job: {"state": "SUCCESS", "total": 4}; st = eda.ci_status(J)
check(st["state"] == "success" and st["total"] == 4, "readable checks pass and the rollup is SUCCESS: success, and the true total (4, not 1) is reported")
eda.rollup = lambda job: {"state": "FAILURE", "total": 4}; st = eda.ci_status(J)
check(st["state"] == "blind", "readable checks pass but the rollup says FAILURE (CodeQL/Cycode): never reported green")
eda.rollup = lambda job: {"state": "PENDING", "total": 4}; st = eda.ci_status(J)
check(st["state"] == "pending" and st["pending"], "the rollup says PENDING: keep waiting")
eda.rollup = lambda job: None; st = eda.ci_status(J)
check(st["state"] == "success", "no rollup available: the readable checks decide, as before")

# ---- worker: wait_ci
seq = []
def urlopen(url, timeout=0):
    r = seq.pop(0)
    if isinstance(r, Exception): raise r
    return Resp(json.dumps(r).encode())
worker.urllib.request.urlopen = urlopen
seq[:] = [{"state": "pending"}, OSError("blip"), {"state": "success", "total": 3}]
check(worker.wait_ci("j")["state"] == "success", "wait_ci polls through pending and a network blip until success")
seq[:] = [urllib.error.HTTPError("u", 403, "Forbidden", {}, None)]
w = worker.wait_ci("j"); check(w["state"] == "unavailable" and "Checks" in w["error"], "a 403 from the gate means the token cannot read CI: say so, do not wait")
worker.CI_WAIT = 0; seq[:] = [{"state": "pending", "pending": ["a"]}]
check(worker.wait_ci("j")["state"] == "timeout", "no result in time means timeout"); worker.CI_WAIT = 1200

# ---- worker: ci_loop
def setup(waits, attempts_results):
    eda.R.h.clear(); EV.clear(); calls.clear()
    eda.R.hset("job:aaaaaaaaaaaa", "job", json.dumps(JOB))
    worker.wait_ci = lambda jid: waits.pop(0)
    worker.attempts = lambda jid, job, fb=None, fix=False: (calls.append((job.get("work_branch"), fb, fix)), attempts_results.pop(0))[1]
    worker.post = lambda url, payload, headers=None, timeout=0: calls.append(("publish", url)) or {"url": "u"}
calls = []
JOB = {"id": "aaaaaaaaaaaa", "service": "svc", "ticket_key": "SRE-1", "branch": "SRE-1/x", "dry_run": False, "source": {"type": "api"}}
FAIL = {"state": "failure", "failed": [{"name": "Render (auth-ui)", "conclusion": "failure", "log": "##[error]boom"}]}
def status(): return eda.R.hget("job:aaaaaaaaaaaa", "status")

setup([{"state": "success", "total": 16}], []); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "published" and "16 checks" in eda.R.hget("job:aaaaaaaaaaaa", "ci") and not calls, "green CI: published, no agent work")
setup([FAIL, {"state": "success", "total": 16}], [("validated", "")]); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
wb, fb, fix = calls[0]
check(wb == "SRE-1/x" and fix and "##[error]boom" in fb and "Render (auth-ui)" in fb and "CI-UNRELATED" in fb and "WHOLE pull request" in fb, "red CI: a fresh session on the same branch gets the failing log and the rules")
check(calls[1] == ("publish", "http://gate:8080/publish/aaaaaaaaaaaa") and status() == "published", "the fix is pushed, CI is watched again, then published")
setup([FAIL, FAIL, FAIL], [("validated", ""), ("validated", "")]); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(sum(1 for c in calls if c[0] == "SRE-1/x") == worker.CI_ROUNDS and status() == "ci-failed" and "after 2 fix rounds" in eda.R.hget("job:aaaaaaaaaaaa", "ci"), "still red after the last round: stop with ci-failed, PR left as is")
setup([FAIL], [("unrelated", "no code touched.\nCI-UNRELATED: artifact download flake")]); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "ci-failed" and "unrelated" in eda.R.hget("job:aaaaaaaaaaaa", "ci") and ("publish", "http://gate:8080/publish/aaaaaaaaaaaa") not in calls, "an unrelated failure is not 'fixed' with code")
setup([FAIL], [("failed", "[yaml] bad indent")]); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "ci-failed" and "rejected the fix" in eda.R.hget("job:aaaaaaaaaaaa", "ci"), "a fix the gate rejects ends the loop")
setup([{"state": "timeout", "pending": ["Cycode"]}], []); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "ci-timeout", "CI that never finishes: ci-timeout")
setup([{"state": "unavailable", "error": "no read"}], []); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "published" and "could not be read" in eda.R.hget("job:aaaaaaaaaaaa", "ci"), "unreadable CI does not fail the job")
setup([{"state": "blind", "total": 16}], []); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "ci-failed" and "cannot read" in eda.R.hget("job:aaaaaaaaaaaa", "ci") and not calls, "red only in checks the token cannot read: ci-failed with a pointer to the PR, no blind fix round")
setup([{"state": "none"}], []); worker.ci_loop("aaaaaaaaaaaa", JOB, "https://gh/pull/9")
check(status() == "published", "no CI at all: published")
eda.R.h.clear(); eda.R.hset("job:aaaaaaaaaaaa", mapping={"status": "ci-waiting", "recoveries": worker.RECOVERIES}); worker.recover()
check(status() == "published" and "restarted" in eda.R.hget("job:aaaaaaaaaaaa", "ci"), "a worker that died while waiting never reruns the job (no second PR)")

# ---- git: a fix round fast-forwards the existing branch and updates the existing PR
def git(d, *a): return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=d, check=True, capture_output=True, text=True).stdout
remote = tempfile.mkdtemp(); git(remote, "init", "-q", "--bare", "-b", "main")
seed = tempfile.mkdtemp(); git(seed, "init", "-q", "-b", "main"); open(f"{seed}/a.txt", "w").write("base\n"); git(seed, "add", "-A"); git(seed, "commit", "-qm", "base")
git(seed, "remote", "add", "origin", f"file://{remote}"); git(seed, "push", "-q", "origin", "main")
git(seed, "checkout", "-qb", "SRE-1/x"); open(f"{seed}/a.txt", "w").write("first\n"); git(seed, "commit", "-qam", "first change"); git(seed, "push", "-q", "origin", "SRE-1/x")
eda.DATA = tempfile.mkdtemp(); eda.remote = lambda job: f"file://{remote}"
patched = []
eda.gh = lambda job, path, method="GET", data=None: patched.append((path, method, data)) or Resp(b"{}")
fj = {"id": "bbbbbbbbbbbb", "repo": "o/r", "branch": "SRE-1/x", "base_branch": "main", "work_branch": "SRE-1/x", "ticket_key": "SRE-1", "allowed_paths": ["*"], "lint": [], "autofix": []}
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for n, t in {"PR.md": "fix(x): whole change (SRE-1)\nbody", "a.txt": "second\n"}.items():
        b = t.encode(); ti = tarfile.TarInfo(n); ti.size = len(b); tf.addfile(ti, io.BytesIO(b))
good, msg = eda.submit(fj, buf.getvalue()); eda.R.hset("job:bbbbbbbbbbbb", "pr_url", "https://github.com/o/r/pull/7")
url = eda.publish(fj)
log = git(remote, "log", "--format=%s", "SRE-1/x").split("\n")
check(good and log[:2] == ["Address CI failures", "first change"], "the fix is a new commit on top of the PR branch (fast-forward, never forced)")
check(patched == [("pulls/7", "PATCH", {"title": "SRE-1" and "fix(x): whole change (SRE-1)", "body": "body"})] and url.endswith("/pull/7"), "the existing PR's title and body are updated, no second PR")
sys.exit(0 if ok else 1)
