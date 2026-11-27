"""Run in the gate image:  sh application/tests/run.sh
allowed_paths: prefixes and exact files as before; "*" means the whole repo, never .git/, never escapes."""
import os, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat
import eda

star, docs = {"allowed_paths": ["*"]}, {"allowed_paths": ["docs/", "README.md"]}
def expect(c, w): print(("PASS " if c else "FAIL ") + w); return c
ok = all([
    expect(eda.allowed("charts/app/templates/_nginx_configmap.yaml", star), '"*" allows any repo path'),
    expect(not eda.allowed(".git/config", star) and not eda.allowed(".git", star), '"*" still blocks .git'),
    expect(not eda.allowed("/etc/passwd", star) and not eda.allowed("a/../b", star), '"*" still blocks absolute and ".." paths'),
    expect(eda.allowed("docs/x.md", docs) and eda.allowed("README.md", docs), "prefix and exact file still allowed"),
    expect(not eda.allowed("charts/x.yaml", docs), "paths outside a restricted list are still refused"),
])

b = eda.branch_name
ok2 = all([
    expect(b("SRE-13168", "nginx applies Content-Security-Policy header to far more requests", "a" * 12) == "SRE-13168/nginx-applies-content-security-policy-header", "ticket branch is TICKET/what-the-change-is"),
    expect(b("", "Fix the helm chart\nmore text", "abcdef123456") == "fix-the-helm-chart-abcdef", "no ticket: slug plus short id"),
    expect(b("", "main", "abcdef123456") != "main" and b("", "!!!", "abcdef123456") == "change-abcdef", "never a bare base-branch name; empty slug falls back"),
    expect(".." not in b("SRE-1", "a/../b ~ ^ :", "x" * 12) and " " not in b("SRE-1", "a b", "x" * 12), "slug is git-ref safe"),
])
sys.exit(0 if ok and ok2 else 1)
