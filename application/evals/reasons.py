#!/usr/bin/env python3
"""Why did jobs retry or fail? Tabulates every gate rejection, reviewer concern and CI failure in the job event logs by kind, so a repeated reason
becomes a deterministic check instead of more prompt text (the loop in docs/personal-agent-plan.md).
  python3 application/evals/reasons.py          # all jobs still in Redis
Reads Redis through the gate pod (it already has the client and REDIS_URL); needs kubectl access to kind-event-driven-ai."""
import subprocess, sys
CODE = r'''
import collections, json, os, re, redis
R = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
kinds = collections.Counter(); jobs = collections.Counter(); examples = {}
def kind(e):
    m = e["message"]
    if m.startswith("REJECTED:"):
        t = re.findall(r"\[([a-z0-9 -]+?)(?: [^\]]*)?\]", m); return "gate: " + (t[0] if t else "other")
    if "independent review" in m and "concerns" in m: return "reviewer: concerns"
    if "independent review" in m and "block" in m: return "reviewer: block"
    if e["stage"] == "ci" and e["level"] in ("warn", "error"): return "ci: " + re.sub(r"[\d.]+", "N", m)[:40]
    if e["level"] == "error": return "error: " + re.sub(r"[0-9a-f]{12}", "ID", m)[:50]
for jid in R.zrevrange("jobs:all", 0, 499):
    seen = set()
    for l in R.lrange(f"events:{jid}", 0, -1):
        try: e = json.loads(l)
        except ValueError: continue
        k = kind(e)
        if k: kinds[k] += 1; examples.setdefault(k, e["message"][:150]); seen.add(k)
    for k in seen: jobs[k] += 1
total = R.zcard("jobs:all")
print(f"{total} jobs. reason: events (jobs affected)")
for k, n in kinds.most_common(): print(f"  {k}: {n} ({jobs[k]})  e.g. {examples[k]}")
'''
r = subprocess.run(["kubectl", "--context", "kind-event-driven-ai", "-n", "eda", "exec", "-i", "deploy/gate", "--", "python3", "-"], input=CODE, capture_output=True, text=True)
sys.stdout.write(r.stdout); sys.stderr.write(r.stderr); sys.exit(r.returncode)
