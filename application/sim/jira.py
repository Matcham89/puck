#!/usr/bin/env python3
"""Pretend to be Jira Cloud, to test the real path (webhook -> parse -> queue -> agent -> write-back) before a real Jira is plugged in.

    python3 application/sim/jira.py create  SRE-201 "Add a rollout-undo skill" "Steps: ..." [--labels ai-agent,ai-dry-run] [--component my-component]
    python3 application/sim/jira.py label   SRE-201 ai-agent "Summary" "Description"     # an *update* event that adds a label
    python3 application/sim/jira.py comments SRE-201                                      # what the agent wrote back (from the mock Jira)

Payloads follow what Jira Cloud sends: webhookEvent, issue.key, issue.fields.{summary, description (ADF), labels, components,
project, reporter}, changelog on updates. Deliveries are signed with X-Hub-Signature (HMAC-SHA256 of the raw body) using
secrets/jira_webhook_secret, which is the same secret the cluster mounts on the intake pod.
"""
import argparse, contextlib, hashlib, hmac, json, os, subprocess, sys, time, urllib.error, urllib.request, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CTX = "kind-event-driven-ai"


def adf(text):
    """Small markdown-ish text to Atlassian Document Format: paragraphs, '- ' bullets, ``` code fences."""
    blocks, lines, i = [], text.splitlines(), 0
    para = lambda t: {"type": "paragraph", "content": [{"type": "text", "text": t}]}
    while i < len(lines):
        l = lines[i]
        if l.startswith("```"):
            code = []; i += 1
            while i < len(lines) and not lines[i].startswith("```"): code.append(lines[i]); i += 1
            blocks.append({"type": "codeBlock", "content": [{"type": "text", "text": "\n".join(code)}]})
        elif l.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "): items.append({"type": "listItem", "content": [para(lines[i][2:])]}); i += 1
            blocks.append({"type": "bulletList", "content": items}); continue
        elif l.strip(): blocks.append(para(l))
        i += 1
    return {"type": "doc", "version": 1, "content": blocks}


def payload(key, summary, description, labels=("ai-agent",), component="my-component", project=None, event="jira:issue_created", added_label=None, reporter="Pat Lee"):
    p = {"timestamp": int(time.time() * 1000), "webhookEvent": event, "user": {"displayName": reporter},
         "issue": {"id": "10001", "key": key, "fields": {
             "summary": summary, "description": adf(description), "labels": list(labels), "issuetype": {"name": "Task"},
             "project": {"key": project or key.split("-")[0]}, "components": [{"name": component}] if component else [],
             "reporter": {"displayName": reporter}, "status": {"name": "To Do"}}}}
    if event == "jira:issue_updated":
        p["changelog"] = {"items": [{"field": "labels", "fromString": " ".join(l for l in labels if l != added_label), "toString": " ".join(labels)}]} if added_label else {"items": []}
    return p


def secret():
    return open(os.path.join(ROOT, "secrets", "jira_webhook_secret")).read().strip()


def deliver(intake, body, delivery_id=None, sign_with=None, raw=None):
    """POST a signed webhook. Returns (http_status, parsed_json)."""
    data = raw if raw is not None else json.dumps(body).encode()
    sig = "sha256=" + hmac.new((sign_with if sign_with is not None else secret()).encode(), data, hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{intake}/webhooks/jira", data=data, method="POST", headers={
        "Content-Type": "application/json", "X-Hub-Signature": sig, "X-Atlassian-Webhook-Identifier": delivery_id or uuid.uuid4().hex})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b"{}")
        except Exception: return e.code, {}


@contextlib.contextmanager
def forward(svc, local, remote, ns="eda"):
    p = subprocess.Popen(["kubectl", "--context", CTX, "-n", ns, "port-forward", f"svc/{svc}", f"{local}:{remote}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            try: urllib.request.urlopen(f"http://localhost:{local}/healthz", timeout=1); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        yield f"http://localhost:{local}"
    finally:
        p.terminate()


def comments(key, base):
    with urllib.request.urlopen(f"{base}/rest/api/3/issue/{key}/comment", timeout=10) as r:
        return json.loads(r.read())["comments"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("key"); c.add_argument("summary"); c.add_argument("description", nargs="?", default="")
    c.add_argument("--labels", default="ai-agent"); c.add_argument("--component", default="my-component"); c.add_argument("--project")
    u = sub.add_parser("label"); u.add_argument("key"); u.add_argument("label"); u.add_argument("summary"); u.add_argument("description", nargs="?", default="")
    u.add_argument("--component", default="my-component")
    k = sub.add_parser("comments"); k.add_argument("key")
    a = ap.parse_args()
    if a.cmd == "comments":
        with forward("jira-sim", 18090, 8080) as base:
            for cm in comments(a.key, base): print(cm["ts"], "|", cm["text"])
        sys.exit(0)
    with forward("intake", 18080, 8080) as intake:
        if a.cmd == "create":
            body = payload(a.key, a.summary, a.description, a.labels.split(","), a.component, a.project)
        else:
            body = payload(a.key, a.summary, a.description, ["ai-agent", a.label] if a.label != "ai-agent" else ["ai-agent"], a.component, event="jira:issue_updated", added_label=a.label)
        status, resp = deliver(intake, body)
        print(status, json.dumps(resp))
