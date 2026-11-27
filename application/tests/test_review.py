"""Run in the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 -e PYTHONPATH=/app <image> /t/test_review.py
The independent review step: approve publishes with the verdict in the PR body; block or concerns goes back to the author ONCE with the findings and the
revised change is reviewed again; a second block fails the job, a second concerns publishes with both rounds in the PR body; a broken reviewer never blocks but says so."""
import json, os, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat

class FakeR:
    def __init__(self): self.h = {}
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
import eda; eda.R = FakeR(); EV = []
import worker; worker.R = eda.R
worker.emit = lambda jid, st, msg, lvl="info": EV.append((lvl, msg))
JOB = {"id": "abc123abc123", "branch": "b", "ticket_key": "SRE-1", "instruction": "bump x", "allowed_paths": ["*"], "service": "s", "dry_run": False, "ci": False}
worker.jobof = lambda jid: JOB
class Resp:
    def __init__(self, b): self.b = b
    def read(self): return self.b
    def __enter__(self): return self
    def __exit__(self, *a): pass
worker.urllib.request.urlopen = lambda url, timeout=0: Resp(b"DIFF TEXT" if "/diff/" in url else b"X")
worker.REVIEW = "http://reviewer"
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
def setup(replies):
    eda.R.h.clear(); EV.clear(); calls.clear(); eda.R.hset("job:abc123abc123", mapping={"validated": "sha", "pr": "Title\n\nbody"})
    it = iter(replies)
    def ask(text, context_id=None, url=None):
        calls.append((url, text))
        if url == worker.REVIEW: r = next(it); return "c", (r if isinstance(r, str) else json.dumps(r))
        eda.R.hset("job:abc123abc123", mapping={"validated": "sha2", "pr": "Title\n\nrevised"}); return "c", "fixed"   # the author resubmits
    worker.ask = ask
calls = []
pr = lambda: eda.R.hget("job:abc123abc123", "pr")

setup([{"verdict": "approve", "findings": []}])
check(worker.reviewed("abc123abc123", JOB) == (True, "") and "approved" in pr() and calls[0][0] == "http://reviewer", "approve: goes on, PR body says approved, the REVIEWER agent was called")
check("DIFF TEXT" in calls[0][1] and "bump x" in calls[0][1] and "Title" in calls[0][1], "the reviewer gets the ticket, PR.md and the diff")
setup([{"verdict": "concerns", "findings": ["touches the shared library chart"]}, {"verdict": "approve", "findings": []}])
r = worker.reviewed("abc123abc123", JOB); author = [c for c in calls if c[0] != worker.REVIEW]
check(r[0] and len(author) == 1 and "touches the shared library chart" in author[0][1], "concerns: the author is sent back ONCE with the findings (a fresh session, not a rubber stamp)")
check("do not invent one" in author[0][1] and "RAISED FINDINGS" in author[0][1], "the handoff says to fix what the repo allows and to say so, not guess, when a finding needs a value or a decision")
check(len([c for c in calls if c[0] == worker.REVIEW]) == 2 and "approved after one revision round" in pr() and "Round 1 findings" in pr() and "touches the shared library chart" in pr(), "the revised change is reviewed again; the PR records the round-1 findings for the human")
setup([{"verdict": "concerns", "findings": ["placeholder id"]}, {"verdict": "concerns", "findings": ["placeholder id still needs a real value"]}])
r = worker.reviewed("abc123abc123", JOB); author = [c for c in calls if c[0] != worker.REVIEW]
check(r[0] and len(author) == 1 and "with concerns after one revision round" in pr() and "placeholder id still needs a real value" in pr() and "Round 1 findings" in pr(), "concerns twice: ONE loop only, then publish with both rounds' findings in the PR body")
setup([{"verdict": "concerns", "findings": ["a"]}, {"verdict": "block", "findings": ["the revision broke it"]}])
r = worker.reviewed("abc123abc123", JOB)
check(not r[0] and "twice" in r[1] and "the revision broke it" in r[1], "concerns, then block: the job fails, nothing is published")
setup([{"verdict": "concerns", "findings": ["a"]}, "not json"])
check(worker.reviewed("abc123abc123", JOB)[0] and "NOT run" in pr() and "Round 1 findings" in pr(), "reviewer unusable on the second look: never blocks, PR says so and keeps round 1")
setup([{"verdict": "block", "findings": ["invented module foo/bar"]}, {"verdict": "approve", "findings": []}])
r = worker.reviewed("abc123abc123", JOB)
author = [c for c in calls if c[0] != worker.REVIEW]
check(r[0] and len(author) == 1 and "invented module foo/bar" in author[0][1] and "revised" in pr(), "block: the author is sent back ONCE with the findings, then the second review passes")
check(len([c for c in calls if c[0] == worker.REVIEW]) == 2, "the revised change is reviewed again")
setup([{"verdict": "block", "findings": ["a"]}, {"verdict": "block", "findings": ["still wrong"]}])
r = worker.reviewed("abc123abc123", JOB)
check(not r[0] and "twice" in r[1] and "still wrong" in r[1], "block twice: the job fails, nothing is published")
setup(["not json at all"])
check(worker.reviewed("abc123abc123", JOB)[0] and "NOT run" in pr(), "an unusable reviewer reply never blocks, but the PR says it was not reviewed")
setup([{"verdict": "maybe", "findings": []}])
check(worker.reviewed("abc123abc123", JOB)[0] and "NOT run" in pr(), "an unknown verdict counts as not reviewed")
sys.exit(0 if ok else 1)
