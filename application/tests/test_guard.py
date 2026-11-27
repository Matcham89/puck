"""Run in the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 -e PYTHONPATH=/app <image> /t/test_guard.py
The anti-cheating gates: protected CI/lint paths, check suppressions, weakened tests, committed secrets."""
import os, sys, tempfile, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"; open(cat, "w").write("{}"); os.environ["CATALOG"] = cat
import eda, lint
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
g = lambda name, old, new: lint.guard(name, old and old.encode(), new.encode())
job, opt = {}, {"allow_protected": True}

for p in (".github/workflows/ci.yml", ".github/CODEOWNERS", ".buildkite/pipeline.yml", "terraform/.tflint.hcl", ".pre-commit-config.yaml", "CODEOWNERS", "Jenkinsfile", ".yamllint"):
    check(eda.protected(p, job), f"{p} is protected")
for p in ("charts/a/templates/x.yaml", "docs/github/ci.md", "src/.githubish/x", "terraform/main.tf", "README.md"):
    check(not eda.protected(p, job), f"{p} is not protected")
check(not eda.protected(".github/workflows/ci.yml", opt), "a catalog can opt a repo out with allow_protected")

check(any("suppression" in e for e in g("a.sh", "echo hi\n", "# shellcheck disable=SC2164\ncd x\n")), "adding a shellcheck disable is refused")
check(any("suppression" in e for e in g("t/x.py", None, "@pytest.mark.skip\ndef test_a(): pass\n")), "adding a skipped test is refused")
check(any("suppression" in e for e in g("m.tf", "a = 1\n", "# tflint-ignore: x\na = 1\n")), "adding a tflint-ignore is refused")
check(not g("a.sh", "# shellcheck disable=SC2164\ncd x\n", "# shellcheck disable=SC2164\ncd x\necho y\n"), "a suppression that was already there is old debt, not a finding")
check(any("assertion" in e for e in g("tests/test_a.py", "def test():\n    assert x == 1\n    assert y\n", "def test():\n    assert y\n")), "removing an assertion from an existing test is refused")
check(not g("tests/test_a.py", "def test():\n    assert x == 1\n", "def test():\n    assert x == 1\n    assert y\n"), "adding an assertion is fine")
check(not g("src/a.py", "assert x\n", "x = 1\n"), "removing an assert from non-test code is not this gate's business")
check(not g("tests/test_new.py", None, "def test():\n    assert 1\n"), "a new test file is fine")
check(any("secret" in e for e in g("c.tf", None, 'key = "AKIAABCDEFGHIJKLMNOP"\n')), "an AWS access key is refused")
check(any("secret" in e for e in g("k.pem", None, "-----BEGIN RSA PRIVATE KEY-----\nabc\n")), "a private key is refused")
check(any("secret" in e for e in g("v.yaml", None, "api_key: \"abcd1234efgh5678ijkl\"\n")), "a hardcoded credential is refused")
check(not g("v.yaml", None, 'api_key: "${var.api_key}"\nsecret_arn: "arn:aws:secretsmanager:eu-west-1:1:secret:x"\ntoken: "<your-token-here-please>"\n'), "references and placeholders are not secrets")
check(any("plan/apply" in e for e in g("main.tf", None, 'data "external" "x" {\n  program = ["sh", "-c", "id"]\n}\n')), "a Terraform external data source is refused")
check(any("plan/apply" in e for e in g("main.tf", "a = 1\n", 'resource "null_resource" "x" {\n  provisioner "local-exec" {\n    command = "id"\n  }\n}\n')), "a local-exec provisioner is refused")
check(not g("main.tf", None, 'data "aws_caller_identity" "me" {}\n'), "ordinary data sources are fine")
check(not g("notes.md", None, 'data "external" "x" {}\n'), "the rule only applies to Terraform files")
check(not g("img.bin", None, "\0AKIAABCDEFGHIJKLMNOP"), "binary content is skipped")
sys.exit(0 if ok else 1)
