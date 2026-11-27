"""Run in the gate image:  sh application/tests/run.sh
Limits on a self-learning job's output: general, short, bounded."""
import json, os, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"
open(cat, "w").write(json.dumps({
    "puck": {"repo": "o/puck", "base_branch": "main", "allowed_paths": ["agent/skills/", "agent/cards/"], "owner": "t", "lint": [], "knowledge": True},
    "web-charts": {"repo": "acme-org/web-charts", "base_branch": "main", "allowed_paths": ["*"], "owner": "t", "lint": []}}))
os.environ["CATALOG"] = cat
import eda
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
def run(files, extra=None):
    root = tempfile.mkdtemp()
    for n, t in {**(extra or {}), **files}.items(): os.makedirs(os.path.dirname(f"{root}/{n}"), exist_ok=True); open(f"{root}/{n}", "w").write(t)
    return eda.lesson_check({}, root, list(files))
good = "# Title\nWhen X happens, do Y.\nBecause Z failed.\n"
check(run({"agent/cards/a.md": good}) == [], "a short general card passes")
check(any("at most 15" in e for e in run({"agent/cards/a.md": "# T\n" + "line\n" * 20})), "a card over 15 lines is refused")
check(run({"agent/skills/s/SKILL.md": "---\nname: s\n---\n" + "step\n" * 30}) == [], "a skill under 40 lines is fine")
check(any("at most 40" in e for e in run({"agent/skills/s/SKILL.md": "step\n" * 50})), "a skill over 40 lines is refused")
check(any("8000" in e for e in run({"agent/skills/s/SKILL.md": "x" * 9000 + "\n"})), "a skill over 8000 characters is refused")
check(any("'SRE-9001'" in e for e in run({"agent/cards/a.md": "# T\nSeen on SRE-9001.\n"})), "a ticket key is refused")
check(any("job id" in e for e in run({"agent/cards/a.md": "# T\nFrom job 21ad1a128bb9.\n"})), "a 12-hex job id is refused")
check(any("web-charts" in e for e in run({"agent/cards/a.md": "# T\nIn web-charts do Y.\n"})), "naming another catalog repo is refused")
check(run({"agent/cards/a.md": "# T\nSee acme-org/web-charts.\n"}) != [], "a target repo slug is refused")
check(run({"agent/cards/a.md": "# T\nThe puck repo keeps cards here.\n"}) == [], "the knowledge repo's own name is fine")
check(run({"agent/cards/a.md": "# T\nUse a helm-charts style layout.\n"}) == [], "a similar word that is not a catalog name is fine")
check(any("consolidate" in e for e in run({"agent/cards/a.md": good}, {"agent/skills/big/SKILL.md": "x" * 50000})), "the whole agent/ knowledge over 80% of the cap refuses more")
sys.exit(0 if ok else 1)
