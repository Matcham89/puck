"""Run in the gate image:  sh application/tests/run.sh
Linear adapter against a stub of the API: parse, poll_once (claim, move, comment, warn once), notify (outcome, In Review, claim released)."""
import json, os, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"
open(cat, "w").write(json.dumps({"svc": {"repo": "o/svc", "base_branch": "main", "allowed_paths": ["*"], "owner": "t", "lint": []}}))
os.environ["CATALOG"] = cat
key = tempfile.mkdtemp() + "/key"; open(key, "w").write("lin_api_x"); os.environ["LINEAR_KEY_FILE"] = key

class FakeR:
    def __init__(self): self.h, self.keys, self.q = {}, {}, []
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.keys: return None
        self.keys[k] = v; return True
    def delete(self, k): self.keys.pop(k, None)
    def zadd(self, *a, **k): pass
    def lpush(self, q, v): self.q.append(v)
    def rpush(self, *a): pass
import eda, linear
eda.R = FakeR()
def expect(c, w): print(("PASS " if c else "FAIL ") + w); return c

expect(linear.unlink("Add [example.com/tier:](<http://example.com/tier:>) infra, see [the docs](https://example.com/x) and [a.io](http://a.io/)") == "Add example.com/tier: infra, see [the docs](https://example.com/x) and a.io", "autolinked text is restored, real links are kept")
ME = "u1"; SVC = {"svc"}
def issue(**kw):
    i = {"id": "uuid-1", "identifier": "ENG-7", "title": "Bump the nginx image", "description": "Move it to 1.27 in the chart.", "creator": {"id": ME},
         "labels": {"nodes": [{"name": "agent"}, {"name": "repo:svc"}]}}
    i.update(kw); return i
def labels(*n): return {"nodes": [{"name": x} for x in n]}
P = linear.parse
r = P(issue(), ME, SVC)["event"]
ok1 = all([
    expect(r["service"] == "svc" and r["ticket_key"] == "ENG-7" and r["dry_run"] is False and r["source"] == {"type": "linear", "key": "uuid-1", "note": ""}, "parse: valid issue becomes an event, UUID in source.key"),
    expect("ignore" in P(issue(creator={"id": "other"}), ME, SVC), "parse: an issue by another creator is ignored"),
    expect(all("warn" in P(issue(labels=labels("agent", *l)), ME, SVC) for l in ([], ["repo:nope"], ["repo:svc", "repo:other"])), "parse: no, unknown or two repo labels warn"),
    expect(P(issue(labels=labels("agent", "repo:svc", "dry-run")), ME, SVC)["event"]["dry_run"] is True, "parse: dry-run label"),
    expect(len(P(issue(description="x" * 9000), ME, SVC)["event"]["instruction"]) <= 4000 and P(issue(description="x" * 9000), ME, SVC)["event"]["source"]["note"] == "truncated", "parse: truncated to 4000 characters and noted"),
    expect("\x07" not in P(issue(description="a\x07b\x00c and more text"), ME, SVC)["event"]["instruction"], "parse: control characters stripped"),
])

# stub API: records calls, serves one Todo issue
calls = []; todo = [issue()]
def gql(q, v=None):
    calls.append((q.split("{")[0].split("(")[0].strip(), v))
    if q is linear.ISSUES: return {"viewer": {"id": ME}, "issues": {"nodes": list(todo)}}
    if q is linear.STATES: return {"issue": {"team": {"states": {"nodes": [{"id": "s-todo", "name": "Todo", "type": "unstarted"}, {"id": "s-rev", "name": "In Review", "type": "started"}, {"id": "s-prog", "name": "In Progress", "type": "started"}, {"id": "s-park", "name": "Backlog", "type": "backlog"}]}}}}
    return {}
linear.gql = gql
def muts(): return [(n, v) for n, v in calls if n.startswith("mutation")]
linear.poll_once(); m = muts()
job = json.loads(eda.R.h[f"job:{eda.R.q[0]}"]["job"]) if eda.R.q else {}
ok2 = all([
    expect(len(eda.R.q) == 1 and job.get("service") == "svc" and job["source"]["key"] == "uuid-1", "poll: one job for the eligible issue"),
    expect(("mutation", {"id": "uuid-1", "state": "s-prog"}) in m, "poll: issue moved to In Progress, not In Review (which is listed first and is also type started)"),
    expect(any(v and "Picked up" in v.get("body", "") for _, v in m), "poll: a Picked up comment"),
])
n = len(m); linear.poll_once()
ok3 = expect(len(eda.R.q) == 1 and len(muts()) == n, "poll: a second poll queues nothing and writes nothing")

todo[:] = [issue(id="uuid-2", identifier="ENG-8", labels=labels("agent"))]; calls.clear()
linear.poll_once(); linear.poll_once()
ok4 = expect(len(eda.R.q) == 1 and len([1 for _, v in muts() if "repo:" in v["body"]]) == 1, "poll: the warn comment is posted once across two polls, nothing queued")

# notify
calls.clear(); eda.R.keys["linear:seen:uuid-1"] = "1"
res = linear.notify(job, "published", "https://x/pr/1"); m = muts()
ok5 = all([
    expect(res == "commented" and any(v.get("body") == linear.jira.outcome_text(job, "published", "https://x/pr/1") for _, v in m if "body" in v), "notify: comment carries outcome_text"),
    expect(("mutation", {"id": "uuid-1", "state": "s-rev"}) in m, "notify: moved to In Review"),
    expect("linear:seen:uuid-1" not in eda.R.keys, "notify: seen claim released so a re-Todo'd issue runs again"),
])
calls.clear(); eda.R.keys["linear:seen:uuid-1"] = "1"
linear.notify(job, "failed", "boom"); m = muts()
ok6 = expect(("mutation", {"id": "uuid-1", "state": "s-park"}) in m and not any(v.get("state") == "s-rev" for _, v in m if "state" in v), "notify: a failed job is commented and parked in Backlog, not In Review or left In Progress")
calls.clear(); linear.notify(job, "no-change", "x"); linear.notify(job, "validated-dry-run", "")
ok6 = expect(len([1 for _, v in muts() if v.get("state") == "s-park"]) == 2, "notify: no-change and a dry run are parked too") and ok6
ok7 = expect(linear.notify({"source": {"type": "jira", "key": "X-1"}, "ticket_key": "X-1"}, "published", "") == "skipped", "notify: skipped for non-linear jobs")
linear.KEY_FILE = key + ".missing"
ok8 = expect(linear.notify(job, "published", "") == "skipped", "notify: skipped without a key file")
import time
calls = []; linear.poll_once = lambda: calls.append(1); linear.POLL = 0.01; linear.halt.clear()
os.path.exists = (lambda orig: lambda p: True if p == linear.KEY_FILE else orig(p))(os.path.exists)
linear.start(); time.sleep(0.1); linear.halt.set(); time.sleep(0.1); n = len(calls); time.sleep(0.1)
ok9 = expect(n > 0 and len(calls) == n, "the poller stops polling once the worker is told to stop")
sys.exit(0 if all([ok1, ok2, ok3, ok4, ok5, ok6, ok7, ok8, ok9]) else 1)
