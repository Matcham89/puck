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
    def set(self, k, v, ex=None): self.keys[k] = v
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
for _ in range(2): R.hset("job:orphan", "status", "running"); worker.recover()
check(R.hget("job:orphan", "status") == "failed" and "retry" in R.hget("job:orphan", "error"), f"after {worker.RECOVERIES} recoveries the job fails instead of looping")

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
R.q.append("late"); seen.clear(); worker.main()
check(seen == [], "after SIGTERM (stop set) the worker takes no new job")
sys.exit(0 if ok else 1)
