"""Linear in, Linear out, by polling (no inbound endpoint, so it works behind NAT on a laptop).

  parse(issue, viewer, services) -> {"event": ...} to queue, {"warn": ...} to tell the author what to fix, or {"ignore": reason}
  poll_once()                    -> one pass: find eligible issues, queue a job each, move the issue to In Progress, comment
  start()                        -> a background poller thread (the worker runs it; two replicas are safe, see poll_once)
  notify(job, status, detail)    -> write the outcome back as a comment and move a finished issue to In Review (best effort)

An issue is picked up only when ALL hold: it carries the trigger label (default `agent`), it is in an unstarted (Todo) state, it was created by the
owner of the API key (so in a shared workspace nobody else can steer the agent), and it carries exactly one `repo:<catalog service>` label.
Ticket text is untrusted input to a model: capped, stripped of control characters and passed on as a description of the change, never as instructions.
Not verified against the live API yet: the commentCreate and issueUpdate mutations (written from Linear's schema, exercised only against a stub).
"""
import json, os, re, threading, time, urllib.request
import jira

KEY_FILE = os.environ.get("LINEAR_KEY_FILE", "/run/secrets/linear/key")
API = os.environ.get("LINEAR_API_URL", "https://api.linear.app/graphql")
TRIGGER = os.environ.get("LINEAR_LABEL", "agent")
REVIEW_STATE = os.environ.get("LINEAR_REVIEW_STATE", "In Review")
PARKED_STATE = os.environ.get("LINEAR_PARKED_STATE", "Backlog")   # failed, no-change and dry-run tickets wait here for a human; Backlog is not "unstarted", so the poller never re-takes them
START_STATE = os.environ.get("LINEAR_START_STATE", "In Progress")   # by name: a team can have several "started" states (In Review is one)
POLL = int(os.environ.get("LINEAR_POLL", "60"))
halt = threading.Event()      # set by the worker on SIGTERM

ISSUES = """query($label: String!) { viewer { id }
  issues(first: 20, filter: {labels: {name: {eq: $label}}, state: {type: {eq: "unstarted"}}}) {
    nodes { id identifier title description creator { id } labels { nodes { name } } team { id states { nodes { id name type } } } } } }"""
STATES = "query($id: String!) { issue(id: $id) { team { states { nodes { id name type } } } } }"
SET_STATE = "mutation($id: String!, $state: String!) { issueUpdate(id: $id, input: {stateId: $state}) { success } }"
COMMENT = "mutation($id: String!, $body: String!) { commentCreate(input: {issueId: $id, body: $body}) { success } }"


def gql(query, variables=None):
    key = open(KEY_FILE).read().strip()      # a personal API key goes in the header as is (OAuth tokens would need "Bearer ")
    req = urllib.request.Request(API, data=json.dumps({"query": query, "variables": variables or {}}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": key, "User-Agent": "puck"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    if d.get("errors"): raise RuntimeError(str(d["errors"])[:300])
    return d["data"]


AUTOLINK = re.compile(r"\[([^\]\n]+)\]\(<?https?://([^>)\s]+)>?\)")


def unlink(text):
    """Linear turns anything domain-like (`example.com/tier:`, `config.yaml`-style names) into `[text](<http://text>)`: put the plain text back so the agent reads what was typed."""
    return AUTOLINK.sub(lambda m: m.group(1) if m.group(1).rstrip("/") == m.group(2).rstrip("/") else m.group(0), text)


def parse(issue, viewer, services):
    key = issue.get("identifier", "")
    if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", key): return {"ignore": f"issue identifier {key!r} is not valid"}
    if (issue.get("creator") or {}).get("id") != viewer: return {"ignore": f"{key} was not created by the API key's owner"}
    labels = [l.get("name", "") for l in (issue.get("labels") or {}).get("nodes", [])]
    repo = [l[5:] for l in labels if l.startswith("repo:")]
    if len(repo) != 1 or repo[0] not in services:
        return {"warn": f"I need exactly one label `repo:<name>` naming a configured repo (found {repo or 'none'}; known: {', '.join(sorted(services))}). Fix the label and move the issue back to Todo."}
    text = jira.clean(unlink(f"{issue.get('title') or ''}\n\n{issue.get('description') or ''}")); note = ""
    if len(text) > jira.MAX_INSTRUCTION: text, note = text[:jira.MAX_INSTRUCTION].rstrip(), "truncated"
    if len(text) < 10: return {"ignore": f"{key} has no usable title/description (under 10 characters)"}
    # source.key is the issue's UUID: that is what the write-back needs
    return {"event": {"service": repo[0], "ticket_key": key, "instruction": text, "dry_run": "dry-run" in labels, "source": {"type": "linear", "key": issue["id"], "note": note}}}


def comment(issue_id, body): gql(COMMENT, {"id": issue_id, "body": body[:4000]})


def move(issue_id, pick):
    """Move an issue to the first of its team's workflow states for which pick(state) is true; no such state means no move."""
    states = gql(STATES, {"id": issue_id})["issue"]["team"]["states"]["nodes"]
    s = next((s for s in states if pick(s)), None)
    if s: gql(SET_STATE, {"id": issue_id, "state": s["id"]})
    return bool(s)


def poll_once():
    """Two workers may poll at once: `linear:seen:<id>` is claimed with SET NX before anything is queued, so an issue yields one job.
    The claim is released when the job ends (notify), so moving an issue back to Todo runs it again."""
    import eda
    d = gql(ISSUES, {"label": TRIGGER}); viewer = d["viewer"]["id"]
    for issue in d["issues"]["nodes"]:
        r = parse(issue, viewer, set(eda.CATALOG)); iid = issue["id"]
        if "warn" in r:
            if eda.R.set(f"linear:warned:{iid}", "1", nx=True, ex=86400): comment(iid, r["warn"])     # one nudge a day, not one per poll
            continue
        if "event" not in r or not eda.R.set(f"linear:seen:{iid}", "1", nx=True, ex=7 * 86400): continue
        try: job = eda.canonical_job(r["event"], trusted=True)
        except ValueError as e: comment(iid, f"I could not queue this: {e}"); continue
        eda.enqueue(job)
        try: move(iid, lambda s: s["name"] == START_STATE); comment(iid, f"Picked up as job {job['id']} on {job['service']}. I will comment here with the result.")
        except Exception as e: eda.emit(job["id"], "queued", f"picked up, but could not update the Linear issue: {str(e)[:150]}", "warn")


def start():
    if not os.path.exists(KEY_FILE): return
    def loop():
        while not halt.is_set():     # a draining worker must not take new tickets: during a rollout it would run them with the old code
            try: poll_once()
            except Exception as e: print("linear poll failed:", str(e)[:200], flush=True)
            halt.wait(POLL)
    threading.Thread(target=loop, daemon=True).start()


def notify(job, status, detail=""):
    src = job.get("source") or {}
    if src.get("type") != "linear" or not os.path.exists(KEY_FILE): return "skipped"
    import eda
    try:
        comment(src["key"], jira.outcome_text(job, status, detail))
        if status in ("published", "ci-failed", "ci-timeout"): move(src["key"], lambda s: s["name"] == REVIEW_STATE)
        elif status in ("failed", "no-change", "validated-dry-run"): move(src["key"], lambda s: s["name"] == PARKED_STATE)
        eda.R.delete(f"linear:seen:{src['key']}")
        return "commented"
    except Exception as e:
        return f"comment failed: {str(e)[:120]}"
