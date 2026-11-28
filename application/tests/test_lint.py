"""Run inside the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 -e PYTHONPATH=/app <image> /t/test_lint.py
Copies the fixtures (some are deliberately broken) and checks what the deterministic lint reports and fixes."""
import os, shutil, sys, tempfile
import lint

src = os.path.join(os.path.dirname(__file__), "fixtures")
root = tempfile.mkdtemp(); shutil.copytree(src, root, dirs_exist_ok=True)
changed = sorted(os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs)
errs, fixed = lint.run_all(root, changed, ["json", "yaml", "shell", "helm", "kubernetes", "terraform", "tflint"], ["terraform-fmt"])
text = "\n".join(errs)
print("auto-fixed:", fixed); print(text)

def expect(cond, what):
    print(("PASS " if cond else "FAIL ") + what); return cond

ok = all([
    expect(any("good/main.tf" in f for f in fixed), "terraform fmt auto-fixed the unformatted file (no LLM)"),
    expect('triggers = {' in open(os.path.join(root, "tf/good/main.tf")).read(), "the formatted content was kept in place"),
    expect("[terraform-fmt]" in text and "broken/main.tf" in text, "unparseable terraform is reported, not silently fixed"),
    expect("[yaml]" in text and "bad.yaml" in text, "yamllint catches tabs/duplicate keys"),
    expect("[shell]" in text and "run.sh" in text, "shellcheck catches the risky script"),
    expect("[json]" in text and "bad.json" in text, "invalid JSON is reported"),
    expect("kubeconform" in text or "[kubernetes]" in text, "kubeconform catches the schema/type errors (chart and plain manifest)"),
    expect("libchart" not in text, "a valid library chart is not rejected (no helm template)"),
    expect("[yaml] chart/templates" not in text, "Go-templated chart files are not run through yamllint"),
    expect("new-line-at-end-of-file" not in text, "a missing final newline is not reported (it would force an unrelated diff line)"),
    expect("\x1b[" not in text, "no ANSI colour codes reach the agent"),
    expect("[terraform-fmt] tf/good" not in text, "a fixable file next to a broken one is not reported as an error"),
    expect("replicas" in text or "want integer" in text, "the schema error keeps its useful part (path and expected type)"),
])
sys.exit(0 if ok else 1)
