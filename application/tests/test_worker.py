"""Run in the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 -e PYTHONPATH=/app <image> /t/test_worker.py
Exercises worker.run with a stubbed agent and gate: every attempt must be a FRESH session and carry the error + branch."""
import json, sys, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
import os, tempfile
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat

class FakeR:                                     # just enough redis for the worker
    def __init__(self): self.h = {}
    def hset(self, k, key=None, value=None, mapping=None):
        d = self.h.setdefault(k, {}); d.update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
import eda; eda.R = FakeR(); EVENTS = []
import worker; worker.R = eda.R
worker.emit = lambda jid, st, msg, lvl="info": EVENTS.append(msg)
worker.jobof = lambda jid: JOB
JOB = {"actor": "agent:event-driven-ai", "on_behalf_of": {"subject": "jira:acc-9", "name": "Pat Lee", "via": "jira-webhook:d1"}, "id": "abc123abc123", "branch": "learn/sre-7", "dry_run": False, "instruction": "x"}
sessions, prompts, published = [], [], []

class Resp:
    def __init__(self, b): self.b = b
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): pass
worker.urllib.request.urlopen = lambda url, timeout=0: Resp(b"AGENT KNOWLEDGE (shared prefix)" if "/knowledge/" in url else b"REPO BRIEF (static prefix)")
worker.PUBLISH_KEY = lambda: "k"

def fake_ask(text, context_id=None):
    prompts.append(text); sessions.append(context_id)
    n = len(prompts)
    if n == 1:      # attempt 1: the gate rejected the upload
        eda.R.hset("job:abc123abc123", mapping={"last_error": "[shell] walk.sh:4:1: SC2164 use cd || exit"})
    else:           # attempt 2: passes
        eda.R.hset("job:abc123abc123", mapping={"validated": "sha", "pr": "T\n\nb"})
    return "ctx-%d" % n, "done"
worker.ask = fake_ask
worker.post = lambda url, payload, headers=None, timeout=0: published.append(url) or {"url": "https://example/pr/1"}

worker.run("abc123abc123")
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
check(len(prompts) == 2, "two attempts: fail, then pass")
check(sessions == [None, None], "every attempt is a FRESH session (no context id reused)")
check(prompts[0].startswith("AGENT KNOWLEDGE") and prompts[0].index("REPO BRIEF") > 0, "attempt 1 starts with the knowledge, then the static brief (cache-friendly prefix)")
check(prompts[1].startswith("AGENT KNOWLEDGE") and "REPO BRIEF" in prompts[1] and "learn/sre-7" in prompts[1], "attempt 2 starts with the same knowledge and brief and names the branch")
check("SC2164" in prompts[1] and "authoritative" in prompts[1], "attempt 2 carries the exact failure and the 'checks win' rule")
check("/restore/abc123abc123" in prompts[1] and ".eda/submit.sh" in prompts[1], "attempt 2 tells the fresh sandbox how to restore its work and resubmit")
check(all(f in prompts[1] for f in ("Objective:", "Artifact:", "Evidence", "Status:", "Blockers:", "Next Action:")), "retry is a structured handoff with all six fields")
check("on behalf of jira:acc-9" in prompts[1], "handoff names who the agent acts for")
check(any("on behalf of jira:acc-9" in e for e in EVENTS), "audit event records actor and subject")
check(published == ["http://gate:8080/publish/abc123abc123"], "publish is called once, only after a validated submission")
check(eda.R.h["job:abc123abc123"]["status"] == "published", "job ends published")
# exhaustion: never validated -> failed after MAX attempts, nothing published
eda.R.h.clear(); prompts.clear(); sessions.clear(); published.clear()
worker.ask = lambda text, context_id=None: (prompts.append(text), eda.R.hset("job:abc123abc123", mapping={"last_error": "boom"}), ("c", "x"))[2]
worker.run("abc123abc123")
check(len(prompts) == worker.MAX and not published and eda.R.h["job:abc123abc123"]["status"] == "failed", f"after {worker.MAX} failed attempts the job fails and nothing is published")
sys.exit(0 if ok else 1)
