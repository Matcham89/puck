#!/usr/bin/env python3
"""End-to-end tests against the live kind cluster (context kind-event-driven-ai).

    python3 tests/e2e.py            # all tests (about 5 minutes, two small LLM jobs)
    python3 tests/e2e.py intake isolation

Every job is a dry run: it stops after validation, so no branch is pushed and no PR is opened.
Needs: kubectl, the cluster up (deployment/kubernetes/up.sh), sqlite3 + the host gateway log only for the cost line.
"""
import contextlib, json, os, subprocess, sys, tarfile, io, time, urllib.error, urllib.request, uuid

CTX = "kind-event-driven-ai"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def kubectl(*args, check=True):
    r = subprocess.run(["kubectl", "--context", CTX, *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout


@contextlib.contextmanager
def forward(svc, ns, local, remote):
    p = subprocess.Popen(["kubectl", "--context", CTX, "-n", ns, "port-forward", f"svc/{svc}", f"{local}:{remote}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try:
                urllib.request.urlopen(f"http://localhost:{local}/", timeout=1)
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.5)
            else:
                break
        yield f"http://localhost:{local}"
    finally:
        p.terminate()


def call(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(cond, what, detail=""):
    results.append((bool(cond), what))
    print(("  PASS " if cond else "  FAIL ") + what + (f"  [{detail}]" if detail and not cond else ""))
    return bool(cond)


def run_job(intake, event, timeout=600):
    status, body = call("POST", f"{intake}/events", event)
    assert status == 202, f"intake said {status}: {body}"
    jid = json.loads(body)["id"]
    end = time.time() + timeout
    while time.time() < end:
        _, b = call("GET", f"{intake}/jobs/{jid}")
        job = json.loads(b)
        if job["status"] in ("validated-dry-run", "published", "failed"):
            return jid, job
        time.sleep(8)
    return jid, {"status": "timeout"}


def subs(jid):
    out = kubectl("-n", "eda", "exec", "deploy/redis", "--", "redis-cli", "lrange", f"subs:{jid}", "0", "-1")
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def ask_agent(text):
    with forward("kagent-controller", "kagent", 18083, 8083) as base:
        s, b = call("POST", f"{base}/agents/kagent/sre-agent",
                    {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
                     "params": {"message": {"messageId": f"e2e-{time.time()}", "role": "ROLE_USER", "parts": [{"text": text}]}}},
                    {"X-User-Id": "event-driven-ai", "A2A-Version": "1.0"}, timeout=240)
        t = json.loads(b)["result"]["task"]
        return t["status"]["state"], " ".join(p.get("text", "") for a in t.get("artifacts", []) for p in a["parts"])


def cost(jid):
    r = subprocess.run(["sh", os.path.join(ROOT, "deployment", "kubernetes", "job-cost.sh"), jid], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else "n/a"


# ------------------------------------------------------------------------------------------------ tests
def test_intake(intake):
    print("intake: validation happens before anything is queued")
    good = {"service": "my-service", "instruction": "A long enough instruction to pass."}
    for name, ev in [("unknown service", {**good, "service": "nope"}),
                     ("too-short instruction", {**good, "instruction": "hi"}),
                     ("malformed ticket key", {**good, "ticket_key": "lol"}),
                     ("not an object", [1, 2])]:
        s, b = call("POST", f"{intake}/events", ev)
        check(s == 400, f"rejects {name} with 400", f"{s} {b[:80]}")
    s, _ = call("GET", f"{intake}/jobs/aaaaaaaaaaaa")
    check(s == 404, "unknown job id is a 404")
    s, b = call("GET", f"{intake}/")
    check(s == 200 and "Event-Driven AI" in b and 'action="/ui/jobs"' not in b, "the UI loads and has no submit form (jobs and logs only)")


def test_isolation(intake):
    print("isolation: who can reach what")
    out = subprocess.run(["kubectl", "--context", CTX, "-n", "eda", "exec", "deploy/gate", "--", "curl", "-s", "-m", "6", "-o", "/dev/null",
                          "-w", "%{http_code}", "http://agentgateway.agentgateway.svc.cluster.local/v1/models"], capture_output=True, text=True)
    check(out.stdout.strip() in ("", "000") or out.returncode != 0, "a non-sandbox pod (gate, which holds the git token) cannot reach the LLM gateway", out.stdout)
    with forward("agentgateway", "agentgateway", 14001, 80) as gw:
        s, b = call("POST", f"{gw}/v1/messages", {"model": "claude-haiku-5-5", "max_tokens": 1, "messages": [{"role": "user", "content": "x"}]},
                    {"anthropic-version": "2023-06-01"})
        check(s == 404, "the gateway refuses any model outside the event-driven-ai/ Anthropic route", f"{s}")
    # The agent (rightly) refuses to probe hosts outside its rules, so the live "no internet" probe was done by hand once
    # (wget example.com -> "bad address"). Here we assert the allowlist itself, which is deterministic and free.
    pol = json.loads(kubectl("-n", "kagent", "get", "egresspolicy", "eda-gate", "-o", "json"))
    origins = [d["origin"] for d in pol["spec"]["destinations"]]
    check(origins == ["http://gate.eda.svc.cluster.local:8080"], "the agent's only extra egress grant is the gate", str(origins))
    check(not any("*" in o or "authentication" in d for o, d in zip(origins, pol["spec"]["destinations"])), "no wildcard origins and no injected credentials in that grant")
    agent = json.loads(kubectl("-n", "kagent", "get", "agent", "sre-agent", "-o", "json"))
    check([r["name"] for r in agent["spec"].get("egressPolicyRefs", [])] == ["eda-gate"], "the agent references no other egress policy")
    conds = {c["type"]: c["status"] for c in agent["status"]["conditions"]}
    check(conds.get("EgressPoliciesApplied") == "True" and conds.get("Ready") == "True", "kagent admitted the grants and the agent is Ready", str(conds))
    mc = json.loads(kubectl("-n", "kagent", "get", "modelconfig", "gateway-sonnet", "-o", "json"))
    check(mc["spec"]["anthropic"]["baseUrl"].startswith("http://agentgateway.agentgateway.svc"), "the model goes through the in-cluster gateway, not the internet or the host", mc["spec"]["anthropic"]["baseUrl"])
    state, text = ask_agent("Reply with the single word pong. Do not use any tools.")
    check(state == "TASK_STATE_COMPLETED" and "pong" in text.lower(), "model round-trip works through the in-cluster gateway", text[:60])


def test_skill_job(intake):
    print("skill job: agent creates a valid skill, gate validates it, nothing is published")
    ev = {"service": "my-service", "ticket_key": "SRE-901", "dry_run": True,
          "instruction": "Record a reusable skill about undoing a bad Kubernetes rollout. Content: when a deployment rollout goes wrong, "
                         "run `kubectl rollout status deploy/NAME`, then `kubectl rollout undo deploy/NAME` to return to the previous revision, "
                         "then `kubectl rollout history deploy/NAME` to confirm. Mention `--to-revision=N` to pick an exact revision. "
                         "Decide which plugin it belongs in, following the repo's own rules, and explain it in the PR description."}
    jid, job = run_job(intake, ev)
    check(job["status"] == "validated-dry-run", "job reaches validated-dry-run", json.dumps(job)[:200])
    check(int(job.get("attempts", 9)) <= 3, "within the 3-attempt budget", job.get("attempts", "?"))
    check("pr_url" not in job and job["status"] != "published", "dry run published nothing")
    new = kubectl("-n", "eda", "exec", "deploy/gate", "--", "git", "-C", f"/data/work/{jid}/r", "status", "--porcelain", "--untracked-files=all", check=False)
    skills = [l.split()[-1] for l in new.splitlines() if l.endswith("SKILL.md")]
    check(skills, "the change adds a new SKILL.md", new[:160])
    if skills:
        body = kubectl("-n", "eda", "exec", "deploy/gate", "--", "cat", f"/data/work/{jid}/r/{skills[0]}")
        fm = body.split("---")[1] if body.startswith("---") else ""
        check("name:" in fm and "description:" in fm, "SKILL.md has name + description frontmatter")
        check("rollout undo" in body, "skill content carries the instruction's key fact (rollout undo)")
    print("    cost:", cost(jid))
    return jid


def test_lint_retry(intake):
    print("lint retry: deterministic lint rejects, the agent fixes, the gate passes")
    ev = {"service": "my-service", "ticket_key": "SRE-902", "dry_run": True,
          "instruction": "Add a small skill called dir-walker to the playbook; you decide which plugin it belongs in. It explains how to walk the "
                         "subdirectories of a directory in shell and ships a helper script at scripts/walk.sh. Write the script the quick beginner way on "
                         "your first try: loop over the output of ls with a for loop and cd into each entry, without worrying about quoting or error handling. "
                         "Deterministic linters run on what you submit; if they report problems you will be told exactly what to fix."}
    jid, job = run_job(intake, ev)
    log = subs(jid)
    check(job["status"] == "validated-dry-run", "job ends validated-dry-run", json.dumps(job)[:200])
    check(any(not s["ok"] and "[shell]" in s["msg"] for s in log),
          "shellcheck rejected the first script (otherwise the retry path was not exercised)", str([s["msg"][:60] for s in log]))
    check(log and log[-1]["ok"], "the last validator result is a pass (the agent fixed what the linter reported)")
    print("    validator results:", [("PASS" if s["ok"] else "FAIL") for s in log], "| cost:", cost(jid))


def test_jira(intake):
    print("jira: signed webhook -> parse -> queue -> agent -> comment back on the ticket")
    sys.path.insert(0, os.path.join(ROOT, "application", "sim"))
    import jira as sim
    desc = ("Record a skill about undoing a bad Kubernetes rollout.\n\nSteps to include:\n- check progress with kubectl rollout status\n- undo with kubectl rollout undo\n"
            "- confirm with kubectl rollout history\n\n```\nkubectl rollout undo deploy/NAME --to-revision=N\n```\nPut it wherever the repo's own rules say it belongs.")
    ok_body = sim.payload("SRE-950", "Skill: undo a bad rollout", desc, ["ai-agent", "ai-dry-run"])
    st, r = sim.deliver(intake, ok_body, sign_with="not-the-secret"); check(st == 401, "a webhook signed with the wrong secret is refused (401)", f"{st} {r}")
    st, r = sim.deliver(intake, None, raw=b"{ not json"); check(st == 400, "a signed but malformed body is a 400", f"{st} {r}")
    st, r = sim.deliver(intake, sim.payload("SRE-951", "No trigger here, just a ticket", "A perfectly normal ticket without the agent label.", ["bug"]))
    check(st == 200 and "ignored" in r, "a ticket without the trigger label is acknowledged and ignored (no retries from Jira)", f"{st} {r}")
    st, r = sim.deliver(intake, sim.payload("OPS-5", "Wrong project entirely", "This belongs to another team entirely.", ["ai-agent"], project="OPS"))
    check(st == 200 and "ignored" in r, "a ticket from a project the catalog does not serve is ignored", f"{st} {r}")
    st, r = sim.deliver(intake, sim.payload("SRE-952", "Edited ticket", "Someone edited the description of a ticket.", ["ai-agent"], event="jira:issue_updated"))
    check(st == 200 and "ignored" in r, "an edit that did not just add the label does not re-trigger", f"{st} {r}")
    run = uuid.uuid4().hex[:8]                       # delivery ids are remembered for 24h, so each run needs its own
    st, r = sim.deliver(intake, ok_body, delivery_id=f"e2e-{run}-1")
    if not check(st == 202 and r.get("ticket") == "SRE-950", "the real ticket is accepted and queued (202)", f"{st} {r}"): return
    jid = r["id"]
    st2, r2 = sim.deliver(intake, ok_body, delivery_id=f"e2e-{run}-1")
    check(st2 == 200 and r2.get("ignored") == "duplicate delivery", "Jira redelivering the same webhook does not create a second job", f"{st2} {r2}")
    st3, r3 = sim.deliver(intake, ok_body, delivery_id=f"e2e-{run}-2")
    check(st3 == 200 and "in flight" in r3.get("ignored", ""), "a second trigger for the same ticket while its job runs is ignored", f"{st3} {r3}")
    end = time.time() + 600; job = {}
    while time.time() < end:
        _, b = call("GET", f"{intake}/jobs/{jid}"); job = json.loads(b)
        if job["status"] in ("validated-dry-run", "published", "failed"): break
        time.sleep(8)
    check(job.get("status") == "validated-dry-run", "the label 'ai-dry-run' made it a dry run and it validated", json.dumps(job)[:200])
    with forward("jira-sim", "eda", 18090, 8080) as base:
        cm = []
        for _ in range(10):
            cm = sim.comments("SRE-950", base)
            if cm: break
            time.sleep(2)
    check(cm and "Dry run for SRE-950" in cm[-1]["text"], "the outcome was written back to the ticket as a comment", str(cm)[:160])
    _, b = call("GET", f"{intake}/jobs/{jid}")
    print("    cost:", cost(jid))


TESTS = {"intake": test_intake, "isolation": test_isolation, "skill": test_skill_job, "lint": test_lint_retry, "jira": test_jira}

if __name__ == "__main__":
    wanted = sys.argv[1:] or list(TESTS)
    with forward("intake", "eda", 18080, 8080) as intake:
        for name in wanted:
            try:
                TESTS[name](intake)
            except Exception as e:                      # a crash is a failure, not a skip
                check(False, f"{name}: crashed", repr(e)[:200])
    ok = sum(1 for c, _ in results if c)
    print(f"\n{ok}/{len(results)} checks passed")
    sys.exit(0 if ok == len(results) else 1)
