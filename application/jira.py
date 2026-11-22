"""Jira in, Jira out. Parsing is pure (no Redis, no network) so it can be tested without a cluster.

  parse(payload, rules)  -> Decision: either an event for canonical_job() or a reason to ignore the delivery
  verify(secret, body, header) -> bool   (Jira's X-Hub-Signature: sha256=<hmac of the raw body>)
  handle_post(h)         -> the intake route POST /webhooks/jira (signature, dedupe, enqueue)
  notify(job, ...)       -> write the outcome back to the ticket as a comment (best effort, never fails a job)

Ticket text is written by people, so it is UNTRUSTED input to a model: it is length-capped, stripped of control characters,
and passed on as a description of the change to make, never as instructions. The containment is elsewhere (no credentials in the
sandbox, an egress allowlist, a deterministic gate, draft PRs, human review).
"""
import hashlib, hmac, json, os, re, urllib.request

SECRET_FILE = os.environ.get("JIRA_SECRET_FILE", "/run/secrets/jira/secret")
AUTH_FILE = os.environ.get("JIRA_AUTH_FILE", "/run/secrets/jira-api/auth")

MAX_INSTRUCTION = 4000
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def adf_to_text(node):
    """Atlassian Document Format (what Jira Cloud sends for rich text) to plain text. Plain strings pass through."""
    if node is None: return ""
    if isinstance(node, str): return node
    t, kids = node.get("type"), node.get("content") or []
    inner = "".join(adf_to_text(k) for k in kids)
    if t == "text": return node.get("text", "")
    if t == "hardBreak": return "\n"
    if t in ("mention", "emoji", "status"): return (node.get("attrs") or {}).get("text") or (node.get("attrs") or {}).get("shortName", "")
    if t == "inlineCard": return (node.get("attrs") or {}).get("url", "")
    if t == "codeBlock": return "```\n" + inner.strip("\n") + "\n```\n\n"
    if t == "listItem": return "- " + inner.strip("\n").replace("\n\n", "\n") + "\n"
    if t in ("bulletList", "orderedList"): return inner + "\n"
    if t in ("paragraph", "heading", "blockquote", "panel"): return inner.rstrip() + "\n\n"
    if t == "tableCell" or t == "tableHeader": return inner.strip() + " | "
    if t == "tableRow": return inner.rstrip(" |") + "\n"
    return inner


def clean(text):
    text = CONTROL.sub("", text).replace("\r\n", "\n")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def verify(secret, body, header):
    if not secret: return False
    if not header or "=" not in header: return False
    algo, _, sig = header.partition("=")
    if algo.lower() != "sha256": return False
    return hmac.compare_digest(sig.strip().lower(), hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())


def labels_of(fields):
    return [l if isinstance(l, str) else l.get("name", "") for l in (fields.get("labels") or [])]


def label_added(payload, label):
    """For an update event: was this label added by the change (and not already there)?"""
    for it in (payload.get("changelog") or {}).get("items") or []:
        if it.get("field") == "labels":
            before = (it.get("fromString") or "").split(); after = (it.get("toString") or "").split()
            if label in after and label not in before: return True
    return False


def parse(payload, rules):
    """rules: {service: {project, components?, trigger_labels?}}. Returns {"event": {...}} or {"ignore": reason}."""
    if not isinstance(payload, dict): return {"ignore": "payload is not a JSON object"}
    kind = payload.get("webhookEvent", "")
    if kind not in ("jira:issue_created", "jira:issue_updated"): return {"ignore": f"event {kind or '(none)'} is not handled"}
    issue = payload.get("issue") or {}; f = issue.get("fields") or {}; key = issue.get("key", "")
    if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", key): return {"ignore": f"issue key {key!r} is not valid"}
    project = (f.get("project") or {}).get("key") or key.split("-")[0]
    comps = [c.get("name", "") for c in (f.get("components") or [])]
    service = next((s for s, r in rules.items() if r.get("project") == project and (not r.get("components") or set(r["components"]) & set(comps))), None)
    if not service: return {"ignore": f"no service in the catalog handles project {project} with components {comps or '[]'}"}
    labels = labels_of(f); triggers = rules[service].get("trigger_labels") or ["ai-agent"]
    hit = next((l for l in triggers if l in labels), None)
    if not hit: return {"ignore": f"none of the trigger labels {triggers} is on {key}"}
    if kind == "jira:issue_updated" and not label_added(payload, hit): return {"ignore": f"{key} was updated, but {hit!r} was not just added"}
    summary = clean(adf_to_text(f.get("summary"))); body = clean(adf_to_text(f.get("description")))
    instruction = (summary + ("\n\n" + body if body else "")).strip()
    note = ""
    if len(instruction) > MAX_INSTRUCTION: instruction, note = instruction[:MAX_INSTRUCTION].rstrip(), "truncated"
    if len(instruction) < 10: return {"ignore": f"{key} has no usable summary/description (under 10 characters)"}
    who = ((f.get("reporter") or {}).get("displayName") or (payload.get("user") or {}).get("displayName") or "")
    u = payload.get("user") or f.get("reporter") or {}      # the person whose action triggered the agent: the accountable subject
    subj = "jira:" + (u.get("accountId") or u.get("displayName") or "unknown")
    return {"event": {"service": service, "on_behalf_of": {"subject": subj[:120], "name": u.get("displayName", "")[:80], "via": "jira-webhook"}, "ticket_key": key, "instruction": instruction, "dry_run": "ai-dry-run" in labels,
                      "source": {"type": "jira", "key": key, "event": kind, "reporter": who[:80], "note": note}}}


# ----------------------------------------------------------------------------------------- intake route
def handle_post(h):
    if h.path.partition("?")[0] != "/webhooks/jira": return False
    import eda
    body = eda.body_of(h)
    secret = open(SECRET_FILE).read().strip() if os.path.exists(SECRET_FILE) else ""
    if not secret:                                  # fail closed: a webhook endpoint without a secret is an open door
        eda.reply(h, 503, {"error": "webhook secret is not configured"}); return True
    if not verify(secret, body, h.headers.get("X-Hub-Signature", "")):
        eda.reply(h, 401, {"error": "bad or missing signature"}); return True
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        eda.reply(h, 400, {"error": "body is not JSON"}); return True
    rules = {k: v["jira"] for k, v in eda.CATALOG.items() if v.get("jira")}
    d = parse(payload, rules)
    if "ignore" in d:                               # a 2xx so Jira does not retry something we deliberately skip
        eda.reply(h, 200, {"ignored": d["ignore"]}); return True
    delivery = h.headers.get("X-Atlassian-Webhook-Identifier") or hashlib.sha256(body).hexdigest()
    if not eda.R.set(f"jira:seen:{delivery}", "1", nx=True, ex=86400):
        eda.reply(h, 200, {"ignored": "duplicate delivery"}); return True
    key = d["event"]["ticket_key"]; active = eda.R.get(f"jira:active:{key}")
    if active and (eda.job_view(active) or {}).get("status") in ("queued", "running", "agent-done"):
        eda.reply(h, 200, {"ignored": f"{key} already has a job in flight", "job": active}); return True
    try:
        d["event"]["on_behalf_of"]["via"] = f"jira-webhook:{delivery}"
        job = eda.canonical_job(d["event"], trusted=True)
    except ValueError as e:
        eda.reply(h, 400, {"error": str(e)}); return True
    eda.enqueue(job); eda.R.set(f"jira:active:{key}", job["id"], ex=6 * 3600)
    eda.reply(h, 202, {"id": job["id"], "status": "queued", "service": job["service"], "ticket": key}); return True


# ----------------------------------------------------------------------------------------- write-back
def comment_body(job, status, detail):
    """ADF comment: what happened and where to look."""
    lines = {"published": f"The agent opened a draft PR for {job['ticket_key']}: {detail}",
             "validated-dry-run": f"Dry run for {job['ticket_key']}: the change passed the deterministic checks; nothing was pushed and no PR was opened.",
             "failed": f"The agent could not complete {job['ticket_key']} after retries. Last report: {detail}"}
    text = lines.get(status, f"{job['ticket_key']}: {status}")
    o = job.get("on_behalf_of") or {}
    text += f" (Acted as {job.get('actor', 'the agent')} on behalf of {o.get('name') or o.get('subject', 'unattributed')}; it has its own identity and did not use their credentials.)"
    return {"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text[:1500]}]}]}}


def notify(job, status, detail=""):
    """Comment on the originating ticket. Best effort: a Jira outage must never fail or hide a finished job."""
    src = job.get("source") or {}; base = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    if src.get("type") != "jira" or not base: return "skipped"
    headers = {"Content-Type": "application/json"}
    if os.path.exists(AUTH_FILE): headers["Authorization"] = open(AUTH_FILE).read().strip()
    req = urllib.request.Request(f"{base}/rest/api/3/issue/{src['key']}/comment", data=json.dumps(comment_body(job, status, detail)).encode(), headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read(); return "commented"
    except Exception as e:
        return f"comment failed: {str(e)[:120]}"
