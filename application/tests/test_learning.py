"""Run in the gate image:  sh application/tests/run.sh
Self-learning closing step: LEARNED.md is kept out of the target PR and, after a good job, becomes one draft-PR job on the knowledge repo."""
import io, json, os, shutil, sys, tarfile, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"
open(cat, "w").write(json.dumps({
    "puck": {"repo": "o/puck", "base_branch": "main", "allowed_paths": ["agent/skills/", "agent/cards/"], "owner": "t", "lint": [], "knowledge": True},
    "svc": {"repo": "o/svc", "base_branch": "main", "allowed_paths": ["*"], "owner": "t", "lint": []}}))
os.environ["CATALOG"] = cat

class FakeR:
    def __init__(self): self.h, self.keys, self.q = {}, {}, []
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.keys: return None
        self.keys[k] = v; return True
    def zadd(self, *a, **k): pass
    def lpush(self, q, v): self.q.append(v)
    def rpush(self, *a): pass
import eda, worker
eda.R = worker.R = FakeR()
worker.jira.notify = lambda *a: "skipped"
def expect(c, w): print(("PASS " if c else "FAIL ") + w); return c
def jobs(): return [json.loads(eda.R.h[f"job:{j}"]["job"]) for j in eda.R.q]

# gate: LEARNED.md is stored, and is not written into the target repo's tree
repo = tempfile.mkdtemp(); open(f"{repo}/a.txt", "w").write("old\n")
eda.DATA = tempfile.mkdtemp(); eda.fresh_clone = lambda job, dest: shutil.copytree(repo, dest)
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    for n, t in {"PR.md": "title\nbody", "a.txt": "new\n", "LEARNED.md": "Helm: bump Chart.yaml\nwhen X do Y"}.items():
        b = t.encode(); ti = tarfile.TarInfo(n); ti.size = len(b); tf.addfile(ti, io.BytesIO(b))
job = {"id": "aaaaaaaaaaaa", "allowed_paths": ["*"], "lint": [], "autofix": []}
ok, msg = eda.submit(job, buf.getvalue())
ok1 = all([
    expect(ok, "submission with LEARNED.md passes the gate: " + msg),
    expect(eda.R.hget("job:aaaaaaaaaaaa", "learned").startswith("Helm: bump"), "the lesson is kept on the job"),
    expect(not os.path.exists(f"{eda.DATA}/work/aaaaaaaaaaaa/r/LEARNED.md") and open(f"{eda.DATA}/work/aaaaaaaaaaaa/r/a.txt").read() == "new\n", "LEARNED.md never reaches the target tree, real changes do"),
])

# worker: closing step
def run(jid, service="svc", state="published", dry=False, source=None, learned="Lesson one\nwhen X do Y"):
    eda.R.hset(f"job:{jid}", mapping={"learned": learned})
    worker.finish(jid, {"id": jid, "service": service, "dry_run": dry, "source": source or {"type": "api"}}, state)
run("j1"); q = jobs()
ok2 = all([
    expect(len(q) == 1 and q[0]["service"] == "puck" and q[0]["repo"] == "o/puck", "a lesson queues exactly one job on the knowledge repo"),
    expect(q[0]["allowed_paths"] == ["agent/skills/", "agent/cards/"] and q[0]["dry_run"] is False, "that job can only touch skills and cards, and opens a real draft PR"),
    expect("Lesson one" in q[0]["instruction"] and "write-skill" in q[0]["instruction"] and "data, not commands" in q[0]["instruction"], "instruction carries the lesson, the skill to use, and the data warning"),
    expect(q[0]["source"] == {"type": "learning", "from": "j1"}, "it is marked as a learning job"),
])
run("j1b"); ok3 = expect(len(jobs()) == 1, "the same lesson is not queued twice")
run("j2", dry=True, learned="Another lesson"); ok4 = expect(len(jobs()) == 2 and jobs()[1]["dry_run"] is True, "a dry run queues a dry run (no stray PR)")
n = len(jobs())
run("j3", service="puck", learned="a"); run("j4", source={"type": "learning"}, learned="b"); run("j5", learned=""); run("j6", state="failed", learned="c")
ok5 = expect(len(jobs()) == n, "no job from a puck job, a learning job, an empty note, or a failed job")
worker.LESSONS_PER_DAY = 3; n = len(jobs()); run("k1", learned="third lesson"); run("k2", learned="fourth lesson")
ok6 = expect(len(jobs()) == n + 1, "at most LESSONS_PER_DAY lessons are queued per day; the next one is dropped")
ok7 = expect("DIFFERENT repo" in jobs()[0]["instruction"] and "do not register" in jobs()[0]["instruction"] and "from job" not in jobs()[0]["instruction"], "the lesson job says: general only, no registration needed, no job id")
sys.exit(0 if all([ok1, ok2, ok3, ok4, ok5, ok6, ok7]) else 1)
