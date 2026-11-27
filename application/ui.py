"""Small server-rendered UI (Jinja + htmx), served by the intake pod: a job list (filterable by service) and a job page with
stage chips, validator results and an event log. Read-only except retrying a failed job, from its job page. Tickets live in Linear/Jira; jobs are created by /events, the Jira webhook and the Linear poller."""
import os, re
from urllib.parse import parse_qs
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

def jobs_for(query):
    """(service, jobs): ?service= is honoured only if it names a catalog service, anything else shows every service."""
    svc = parse_qs(query).get("service", [""])[0]
    svc = svc if svc in eda.CATALOG else ""
    return svc, ([j for j in eda.list_jobs(200) if j.get("service") == svc][:50] if svc else eda.list_jobs())

def handle_get(h):
    path, _, query = h.path.partition("?")
    if path == "/":
        svc, jobs = jobs_for(query)
        page(h, "index.html", jobs=jobs, service=svc, services=[{"name": k, "repo": v["repo"]} for k, v in sorted(eda.CATALOG.items())]); return True
    if path == "/healthz": eda.reply(h, 200, {"ok": True}); return True
    if path == "/ui/jobs": page(h, "_jobs.html", jobs=jobs_for(query)[1]); return True
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
