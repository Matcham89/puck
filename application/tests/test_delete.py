"""Run in the gate image like the other tests. The gate applies an upload's deletions: only real, allowed, unprotected files; a deletion-only change still counts as a change."""
import io, os, sys, tarfile, tempfile, types, subprocess
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat
import eda
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
class FakeR:
    def __init__(s): s.h = {}
    def hset(s, k, mapping=None, **kw): s.h.update(mapping or {})
eda.R = FakeR(); eda.DATA = tempfile.mkdtemp()
origin = tempfile.mkdtemp()
for c in (["init", "-q", "-b", "main"], ["config", "user.email", "a@b"], ["config", "user.name", "a"]): subprocess.run(["git", "-C", origin] + c, check=True)
for f in ("a/keep.yaml", "a/gone.yaml", ".github/workflows/ci.yml"): os.makedirs(os.path.dirname(f"{origin}/{f}"), exist_ok=True); open(f"{origin}/{f}", "w").write("x: 1\n")
subprocess.run(["git", "-C", origin, "add", "-A"], check=True); subprocess.run(["git", "-C", origin, "commit", "-qm", "i"], check=True)
eda.fresh_clone = lambda job, dst: subprocess.run(["git", "clone", "-q", origin, dst], check=True)
def upload(files):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode="w:gz") as t:
        for n, c in files.items(): i = tarfile.TarInfo(n); i.size = len(c); t.addfile(i, io.BytesIO(c))
    return b.getvalue()
job = {"id": "abc123abc123", "allowed_paths": ["*"], "lint": [], "autofix": [], "repo": "o/r", "base_branch": "main"}
r1 = eda.submit(job, upload({"PR.md": b"T\n\nb", ".eda/deleted": b"a/gone.yaml\n../etc/passwd\nnope.yaml\n"}))
check(r1[0] and eda.R.h["files"] == '["a/gone.yaml"]', f"a deletion-only change is a change: {r1} {eda.R.h.get('files')}")
d = eda.diff_of(job)
check("FILES CHANGED" in d and "1 file changed" in d and "a/gone.yaml" in d and "deleted file mode" in d, "the reviewer's diff opens with git's own file count and shows the deletion")
r2 = eda.submit(job, upload({"PR.md": b"T\n\nb", ".eda/deleted": b".github/workflows/ci.yml\n"}))
check(not r2[0] and "protected" in r2[1], "deleting a protected file is refused")
sys.exit(0 if ok else 1)
