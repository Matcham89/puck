"""Run in the gate image (needs `tree` and git):  sh application/tests/run.sh
brief() must orient the agent on ANY repo (plugin layout or not), cap long docs, and be byte-identical for the same commit."""
import os, shutil, subprocess, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat
import eda

def make_repo(files):
    d = tempfile.mkdtemp()
    for path, text in files.items():
        os.makedirs(os.path.dirname(f"{d}/{path}") or d, exist_ok=True); open(f"{d}/{path}", "w").write(text)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for c in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "x"]): subprocess.run(["git", *c], cwd=d, env=env, check=True)
    return d

def brief_of(src, allowed):
    eda.fresh_clone = lambda job, dest: shutil.copytree(src, dest)
    return eda.brief({"repo": "o/r", "allowed_paths": allowed})

ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c

flux = make_repo({"README.md": "# Home\nA homelab.\n", "CONTRIBUTING.md": "Open a PR.\n", "flux/apps/a.yaml": "kind: X\n", "docs/x.md": "hi\n",
                  "node_modules/junk/index.js": "x", "terraform/main.tf": "x"})
b = brief_of(flux, ["docs/", "flux/apps/", "README.md"])
check(b.startswith("REPO BRIEF for o/r@"), "starts with the brief header (cache-friendly prefix)")
check("## You may change only these paths" in b and "- flux/apps/" in b and "- README.md" in b, "lists the allowed paths")
check("## Layout (depth 2)" in b and "flux" in b and "terraform" in b, "describes the layout of a non-plugin repo")
check("node_modules" not in b, "skips node_modules and friends")
check("## Inside flux/apps/ (depth 3)" in b and "a.yaml" in b and "## Inside docs/" in b, "looks inside the allowed directories")
check("## README.md\n# Home" in b and "## CONTRIBUTING.md\nOpen a PR." in b, "quotes the repo's own docs")
check(b.index("## CONTRIBUTING.md") < b.index("## README.md"), "rules come before the README")
check("## Plugins" not in b, "no plugin index when the repo has no marketplace")
check(b == brief_of(flux, ["docs/", "flux/apps/", "README.md"]), "byte-identical for the same commit")

big = make_repo({"README.md": "x" * 9000, "CLAUDE.md": "rules\n"})
bb = brief_of(big, ["README.md"])
check("[... truncated by the gate" in bb and len(bb) < 6000, "caps a long README")
check("Inside" not in bb, "a file in allowed_paths gets no directory listing")

plug = make_repo({".claude-plugin/marketplace.json": '{"plugins":[{"name":"demo","version":"1.0.0","category":"development","description":"a demo plugin"}]}', "plugins/demo/README.md": "d"})
bp = brief_of(plug, ["plugins/"])
check("## Plugins" in bp and "- demo 1.0.0 (development): a demo plugin" in bp, "adds the plugin index for plugin repos")
sys.exit(0 if ok else 1)
