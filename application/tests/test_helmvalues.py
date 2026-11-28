"""Run in the gate image like the other tests. The Helm values type check: a value of a different type than the chart's own default is reported."""
import sys, lint
ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c
d = {"apiKey": "", "replicas": 1, "image": {"tag": "v1"}, "extraArgs": [], "config": {}, "unset": None, "tls": False}
check(lint.type_diffs({"apiKey": {"valueFrom": {"secretKeyRef": {}}}}, d) == ["apiKey: chart default is a string, the release sets a map"], "a map where the chart has a string (the qdrant apiKey case)")
check(lint.type_diffs({"replicas": 3, "image": {"tag": "v2"}, "extraArgs": ["a"], "config": {"x": {"y": 1}}, "unset": {"a": 1}, "new": 1}, d) == [], "same types, free-form maps, null defaults and unknown keys pass")
check(lint.type_diffs({"image": {"tag": 2}}, d) == ["image.tag: chart default is a string, the release sets a number"], "nested mismatch is reported with its path")
check(lint.type_diffs({"tls": "true"}, d) != [], "a quoted boolean is a mismatch")
sys.exit(0 if ok else 1)
