"""Run in the gate image (needs git):  sh application/tests/run.sh
The agent's rules, cards and skills come from the knowledge repo's base branch on EVERY job, so a merged skill is live on the next run."""
import glob, os, shutil, subprocess, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat
import eda

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
def git(d, *a): subprocess.run(["git", *a], cwd=d, env=ENV, check=True, capture_output=True)
def write(d, files):
    for path, text in files.items():
        os.makedirs(os.path.dirname(f"{d}/{path}"), exist_ok=True); open(f"{d}/{path}", "w").write(text)
def commit(d, files): write(d, files); git(d, "add", "-A"); git(d, "commit", "-qm", "x")

repo = tempfile.mkdtemp(); git(repo, "init", "-q", "-b", "main")
commit(repo, {"agent/AGENTS.md": "# Rules\nstay small\n", "agent/cards/stuck.md": "# Stuck\nstop after two failures\n", "agent/skills/first-skill/SKILL.md": "---\nname: first-skill\n---\nstep one\n"})
clones = []
eda.DATA = tempfile.mkdtemp()
eda.remote = lambda job: repo                                              # ls-remote and clone hit the local repo
eda.fresh_clone = lambda job, dest: (clones.append(dest), shutil.copytree(repo, dest))
eda.CATALOG = {"svc": {"repo": "o/svc", "base_branch": "main"}, "puck": {"repo": "o/puck", "base_branch": "main", "knowledge": True}}

ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c

k1 = eda.knowledge()
check(k1.startswith("AGENT KNOWLEDGE from o/puck@"), "starts with a header naming the repo and commit")
check("stay small" in k1 and "## Card: stuck" in k1 and "stop after two failures" in k1, "includes AGENTS.md and the cards")
check("## Skill: first-skill" in k1 and "step one" in k1, "includes the skills")
check(eda.knowledge() == k1 and len(clones) == 1, "the same commit is served from the cache (one clone)")

commit(repo, {"agent/skills/learned-later/SKILL.md": "---\nname: learned-later\n---\nmerged after the first run\n"})   # a learning PR gets merged
k2 = eda.knowledge()
check("## Skill: learned-later" in k2 and "merged after the first run" in k2, "a skill merged after the last run is in the very next run's knowledge")
check("## Skill: first-skill" in k2 and len(clones) == 2, "earlier skills stay, and only the new commit was cloned")

commit(repo, {"agent/skills/huge/SKILL.md": "x" * 200000})
k3 = eda.knowledge()
check(len(k3) < eda.KNOWLEDGE_CAP + 200 and "truncated by the gate" in k3, "the total is capped")

eda.CATALOG = {"svc": {"repo": "o/svc", "base_branch": "main"}}
check(eda.knowledge() == "", "no repo marked knowledge: empty (the agent still works)")
sys.exit(0 if ok else 1)
