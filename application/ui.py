"""Small server-rendered UI (Jinja + htmx), served by the intake pod: a live queue, a job list, and a job page with
stage chips, validator results and an event log. Read-only except retrying a failed job. Tickets live in Linear/Jira; jobs are created by /events, the Jira webhook and the Linear poller."""
import os, re
import jinja2
import eda

ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")), autoescape=True)

def page(h, name, headers=None, **ctx):
    body = ENV.get_template(name).render(**ctx).encode()
    h.send_response(200); h.send_header("Content-Type", "text/html; charset=utf-8"); h.send_header("Content-Length", str(len(body)))
    for k, v in (headers or {}).items(): h.send_header(k, v)
    h.end_headers(); h.wfile.write(body)

def redirect(h, url):
    h.send_response(303); h.send_header("Location", url); h.send_header("Content-Length", "0"); h.end_headers()

def handle_get(h):
    path, _, query = h.path.partition("?")
    if path == "/": page(h, "index.html", jobs=eda.list_jobs(), q=eda.queue_status()); return True
    if path == "/healthz": eda.reply(h, 200, {"ok": True}); return True
    if path == "/ui/queue": page(h, "_queue.html", q=eda.queue_status()); return True
    if path == "/ui/jobs": page(h, "_jobs.html", jobs=eda.list_jobs()); return True
    m = re.fullmatch(r"/ui/jobs/([a-f0-9]{12})(/events)?", path)
    if not m: return False
    job = eda.job_view(m.group(1))
    if not job: eda.reply(h, 404, {"error": "unknown job"}); return True
    active = job.get("status") in ("queued", "running", "agent-done", "ci-waiting")
    if m.group(2):          # polled fragment; when the job finishes, ask htmx to reload the whole page once
        page(h, "_events.html", headers={"HX-Refresh": "true"} if ("live=1" in query and not active) else None, events=eda.job_events(job["id"]))
        return True
    page(h, "job.html", job=job, stages=eda.STAGES, events=eda.job_events(job["id"]), subs=eda.job_subs(job["id"]), live=active)
    return True

def handle_post(h):
    path = h.path.partition("?")[0]
    m = re.fullmatch(r"/ui/jobs/([a-f0-9]{12})/retry", path)
    if m:
        jid = m.group(1); job = eda.job_view(jid)
        if not job: eda.reply(h, 404, {"error": "unknown job"}); return True
        if job.get("status") == "failed":              # only a failed job can be retried; the worker carries the last failure into attempt 1
            eda.setf(jid, status="queued", stage="queued", retry="1"); eda.R.lpush("jobs", jid)
            eda.emit(jid, "queued", "retry requested from the UI")
        redirect(h, f"/ui/jobs/{jid}"); return True
    return False
