"""Run in the gate image:  sh application/tests/run.sh
One git token per repo owner, with the plain "token" as fallback; scrub hides every token."""
import os, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat
import eda

d = tempfile.mkdtemp(); eda.GIT_SECRETS = d
for n, v in {"acme-org": "T1\n", "matcham89": "M1", "token": "FALL"}.items(): open(f"{d}/{n}", "w").write(v)
os.makedirs(f"{d}/..data")                                     # kubernetes adds dirs and symlinks; they must be ignored

def expect(c, w): print(("PASS " if c else "FAIL ") + w); return c
ok = all([
    expect(eda.token("acme-org/web-charts") == "T1", "owner file picked and trimmed"),
    expect(eda.token("Matcham89/puck") == "M1", "owner match is case-insensitive"),
    expect(eda.token("other/x") == "FALL", "unknown owner falls back to token"),
    expect("T1@github.com/acme-org/a.git" in eda.remote({"repo": "acme-org/a"}), "clone URL carries that owner's token"),
    expect(eda.scrub("T1 M1 FALL ok") == "*** *** *** ok", "scrub hides every token"),
])
sys.exit(0 if ok else 1)
