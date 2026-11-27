"""Run in the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 -e PYTHONPATH=/app <image> /t/test_publish.py
Publish-side behaviour: the real error reaches the job, an existing branch is caught before the push, a change-less answer is its own outcome."""
import io, os, subprocess, sys, tempfile, types, urllib.error
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat

class FakeR:
    def __init__(self): self.h = {}
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
import eda; eda.R = FakeR(); EV = []
import worker; worker.R = eda.R
worker.emit = lambda jid, st, msg, lvl="info": EV.append(msg)
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c

def boom(req, timeout=0): raise urllib.error.HTTPError("http://gate:8080/publish/abc", 500, "ISE", {}, io.BytesIO(b'{"ok": false, "message": "! [rejected] b -> b (fetch first)"}'))
worker.urllib.request.urlopen = boom
try: worker.post("http://gate:8080/publish/abc123abc123", {}); err = ""
except RuntimeError as e: err = str(e)
check("500" in err and "fetch first" in err, "a gate error body reaches the job instead of a bare 'HTTP Error 500'")

def boom2(url, timeout=0): raise urllib.error.HTTPError(url, 500, "ISE", {}, io.BytesIO(b'{"error": "remote: Write access to repository not granted."}'))
worker.urllib.request.urlopen = boom2
try: worker.fetch("http://gate:8080/brief/abc123abc123"); err = ""
except RuntimeError as e: err = str(e)
check("Write access" in err and "brief" in err, "a failed clone behind /brief reaches the job with its reason")

d = tempfile.mkdtemp(); g = lambda *a, cwd=d: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
g("init", "-q", "--bare", "remote.git"); g("clone", "-q", "remote.git", "w"); w = f"{d}/w"
g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x", cwd=w); g("push", "-q", "origin", "HEAD:refs/heads/taken", cwd=w)
check(eda.branch_taken(w, "taken") and not eda.branch_taken(w, "free"), "an existing remote branch is detected before the push, a free one is not")

JOB = {"id": "abc123abc123", "branch": "b", "ticket_key": "SRE-1", "instruction": "x", "allowed_paths": ["*"], "service": "s", "dry_run": False, "ci": False, "actor": "a"}
worker.jobof = lambda jid: JOB; worker.REVIEW = None; posted = []
class Resp:
    def read(self): return b"X"
    def __enter__(self): return self
    def __exit__(self, *a): pass
worker.urllib.request.urlopen = lambda url, timeout=0: Resp()
worker.post = lambda *a, **k: posted.append(a[0]) or {"url": "u"}
def ask(text, context_id=None, url=None):
    eda.R.hset("job:abc123abc123", mapping={"validated": "sha", "pr": "Title\n\nI changed nothing: the file does not exist.", "files": "[]"}); return "c", "done"
worker.ask = ask
worker.run("abc123abc123")
check(eda.R.hget("job:abc123abc123", "status") == "no-change" and not posted, "a validated submission with no files ends as no-change and nothing is published")
check(any("does not exist" in e for e in EV), "the agent's explanation is shown on the job")
# review blocks the first change, the author withdraws it: that is a no-change, not a validated change
import json
worker.REVIEW = "http://reviewer"; posted.clear(); eda.R.h.clear(); replies = iter([{"verdict": "block", "findings": ["no such module"]}, {"verdict": "approve", "findings": []}]); n = {"author": 0}
def ask2(text, context_id=None, url=None):
    if url == worker.REVIEW: return "c", json.dumps(next(replies))
    n["author"] += 1; files = '["a.tf"]' if n["author"] == 1 else "[]"
    eda.R.hset("job:abc123abc123", mapping={"validated": "sha", "pr": "Title\n\nwithdrawn after review", "files": files}); return "c", "done"
worker.ask = ask2
worker.run("abc123abc123")
check(n["author"] == 2 and eda.R.hget("job:abc123abc123", "status") == "no-change" and not posted, "a change withdrawn after a review block ends as no-change, not as a validated change")
sys.exit(0 if ok else 1)
