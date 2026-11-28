"""Run in the gate image:  sh application/tests/run.sh
A worker that dies mid-job must not strand the job: leases, recovery, and a graceful SIGTERM."""
import sys, types, os, tempfile
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat

class FakeR:
    def __init__(self): self.h, self.keys, self.z, self.q = {}, {}, [], []
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
    def hincrby(self, k, f, n): d = self.h.setdefault(k, {}); d[f] = int(d.get(f, 0)) + n; return d[f]
    def zrevrange(self, k, a, b): return list(self.z)
    def exists(self, k): return 1 if k in self.keys else 0
    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.keys: return None
        self.keys[k] = v; return True
    def delete(self, k): self.keys.pop(k, None)
    def lpush(self, k, v): self.q.append(v)
    def brpop(self, k, timeout=0): return ("jobs", self.q.pop()) if self.q else None
import eda; R = eda.R = FakeR()
import worker; worker.R = R
EVENTS = []; worker.emit = lambda jid, st, msg, lvl="info": EVENTS.append((jid, lvl, msg))

ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
def job(jid, status, lease=False):
    R.hset(f"job:{jid}", mapping={"status": status}); R.z.append(jid)
    if lease: R.set(worker.LEASE(jid), "1")

job("orphan", "running"); job("alive", "running", lease=True); job("agentdone", "agent-done"); job("fine", "published"); job("queued", "queued")
worker.recover()
check(R.hget("job:orphan", "status") == "queued" and "orphan" in R.q, "a running job with no lease is requeued")
check(R.hget("job:agentdone", "status") == "queued" and "agentdone" in R.q, "an agent-done job with no lease is requeued too")
check(R.hget("job:alive", "status") == "running" and "alive" not in R.q, "a job with a live lease is left alone")
check(R.hget("job:fine", "status") == "published" and R.hget("job:queued", "status") == "queued" and "queued" not in R.q, "finished and queued jobs are untouched")
check(any(l == "warn" and "requeued" in m for _, l, m in EVENTS), "recovery is visible in the job log")
# two workers recovering at once (a rollout): the second one still reads the stale "running" status, but the lease claim stops it
job("racer", "running"); worker.recover(); n1 = R.q.count("racer"); R.hset("job:racer", "status", "running"); worker.recover()
check(n1 == 1 and R.q.count("racer") == 1 and R.hget("job:racer", "recoveries") == 1, "two workers recovering the same orphan requeue it exactly once")
R.delete(worker.LEASE("orphan"))
for _ in range(2): R.hset("job:orphan", "status", "running"); R.delete(worker.LEASE("orphan")); worker.recover()
check(R.hget("job:orphan", "status") == "failed" and "retry" in R.hget("job:orphan", "error"), f"after {worker.RECOVERIES} recoveries the job fails instead of looping")

# a worker that stopped while waiting for CI: the wait is resumed (not reported as a timeout, not silently dropped), then falls back to "check the PR"
R.q.clear(); R.keys.clear(); R.z.clear(); job("cijob", "ci-waiting"); worker.recover()
check("cijob" in R.q and R.hget("job:cijob", "status") == "ci-waiting", "a ci-waiting job with no lease is queued to resume its CI wait")
for _ in range(2): R.delete(worker.LEASE("cijob")); worker.recover()
check(R.hget("job:cijob", "status") == "published" and "check the PR" in R.hget("job:cijob", "ci"), "after repeated restarts it falls back to telling the human to check the PR")
worker.stop.set(); worker.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("blip")); worker.CI_POLL = 0
check(worker.wait_ci("x") == {"state": "interrupted"}, "a stopping worker reports 'interrupted', not a CI timeout"); worker.stop.clear()

# main loop: lease held while a job runs, released after; SIGTERM stops taking jobs
R.q.clear(); R.keys.clear(); R.z.clear(); seen = []
R.q.append("j1"); worker.run = lambda jid: seen.append((jid, R.exists(worker.LEASE(jid))))
orig = R.brpop
def brpop_then_term(k, timeout=0):
    r = orig(k, timeout)
    if r is None: worker.stop.set()
    return r
R.brpop = brpop_then_term
worker.main()
check(seen == [("j1", 1)], "the lease exists while the job runs")
check(R.exists(worker.LEASE("j1")) == 0, "the lease is released when the job ends")
# a job that dies with an exception still tells its ticket (it used to stay In Progress, silently)
R.q.clear(); R.keys.clear(); told = []; R.hset("job:boom", mapping={"status": "running", "job": "{}"}); R.q.append("boom")
worker.stop.clear(); worker.run = lambda jid: (_ for _ in ()).throw(RuntimeError("branch exists")); worker.jobof = lambda jid: {"id": jid}
worker.finish = lambda jid, job, state, detail="": told.append((jid, state, detail)); worker.main()
check(R.hget("job:boom", "status") == "failed" and told == [("boom", "failed", "branch exists")], f"a worker exception is reported to the ticket: {told}")
worker.stop.set()
R.q.append("late"); seen.clear(); worker.main()
check(seen == [], "after SIGTERM (stop set) the worker takes no new job")
sys.exit(0 if ok else 1)
