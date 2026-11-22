#!/bin/sh
# Usage: deployment/kubernetes/job-cost.sh <job-id>   LLM calls, tokens and estimated cost for a job, from the in-cluster gateway's request log.
# Rates ($/M tokens, fitted exactly to the gateway's own cost figures for this model): uncached in 2.00, cache write 2.50, cache read 0.10, out 10.00.
set -eu
id=$1; CTX=kind-event-driven-ai
r() { kubectl --context $CTX -n eda exec deploy/redis -- redis-cli hget "job:$id" "$1"; }
t0=$(r t0); t1=$(r t1); [ -n "$t0" ] && [ -n "$t1" ] || { echo "job $id has no finished window (status: $(r status))" >&2; exit 1; }
kubectl --context $CTX -n agentgateway logs deploy/agentgateway --since=48h 2>/dev/null | python3 -c '
import re, sys, datetime as d
t0, t1 = [d.datetime.fromisoformat(x).replace(tzinfo=d.timezone.utc) for x in sys.argv[1:3]]
t1 += d.timedelta(seconds=3)
tot = dict(calls=0, uncached=0, cache_write=0, cache_read=0, out=0)
for l in sys.stdin:
    if "gen_ai.usage.input_tokens=" not in l: continue
    t = d.datetime.fromisoformat(l.split()[0].replace("Z", "+00:00"))
    if not t0 <= t <= t1: continue
    g = lambda k: int((re.search(k + r"=(\d+)", l) or [0, 0])[1])
    i, w, rd, o = g("gen_ai.usage.input_tokens"), g(r"gen_ai.usage.cache_creation.input_tokens"), g(r"gen_ai.usage.cache_read.input_tokens"), g("gen_ai.usage.output_tokens")
    tot["calls"] += 1; tot["uncached"] += i - w - rd; tot["cache_write"] += w; tot["cache_read"] += rd; tot["out"] += o
usd = (tot["uncached"] * 2 + tot["cache_write"] * 2.5 + tot["cache_read"] * 0.1 + tot["out"] * 10) / 1e6
print("calls=%(calls)d uncached_in=%(uncached)d cache_write=%(cache_write)d cache_read=%(cache_read)d out=%(out)d" % tot, "est_usd=%.3f" % usd)
' "$t0" "$t1"
