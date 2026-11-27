"""Run in the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 -e PYTHONPATH=/app <image> /t/test_ui.py
Renders every UI page against an in-memory Redis: content, escaping, retry rules, htmx refresh. The UI only tracks jobs and their logs; it has no submit form."""
import io, json, os, sys, tempfile, time, types
sys.modules["redis"] = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *a, **k: None))
cat = tempfile.mkdtemp() + "/c.json"
open(cat, "w").write(json.dumps({"my-service": {"repo": "OWNER/REPO", "base_branch": "main", "allowed_paths": ["plugins/"], "owner": "t", "lint": ["json"]}}))
os.environ["CATALOG"] = cat

class FakeR:
    def __init__(self): self.h, self.l, self.z = {}, {}, {}
    def hset(self, k, key=None, value=None, mapping=None): self.h.setdefault(k, {}).update(mapping or {key: value})
    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hgetall(self, k): return dict(self.h.get(k, {}))
    def hdel(self, k, *fs): [self.h.get(k, {}).pop(f, None) for f in fs]
    def zadd(self, k, m): self.z.setdefault(k, {}).update(m)
    def zrevrange(self, k, a, b): return [x for x, _ in sorted(self.z.get(k, {}).items(), key=lambda i: -i[1])][a:b + 1]
    def lpush(self, k, v): self.l.setdefault(k, []).insert(0, v)
    def rpush(self, k, v): self.l.setdefault(k, []).append(v)
    def lrange(self, k, a, b): return self.l.get(k, [])[a:(None if b == -1 else b + 1)]
    def llen(self, k): return len(self.l.get(k, []))
import eda, ui
eda.R = FakeR()

class H:                                     # just enough of BaseHTTPRequestHandler
    def __init__(self, path, body=b"", ctype=""):
        self.path = path; self.headers = {"Content-Length": str(len(body)), "Content-Type": ctype}; self.rfile = io.BytesIO(body); self.wfile = io.BytesIO()
        self.code = None; self.out_headers = {}
    def send_response(self, c): self.code = c
    def send_header(self, k, v): self.out_headers[k] = v
    def end_headers(self): pass
    @property
    def body(self): return self.wfile.getvalue().decode()
    class _Hdr(dict):
        def get(self, k, d=None): return super().get(k, d)

def get(path):
    h = H(path); h.headers = H._Hdr(h.headers); assert ui.handle_get(h), path; return h
def post(path, form):
    from urllib.parse import urlencode
    b = urlencode(form).encode(); h = H(path, b, "application/x-www-form-urlencoded"); h.headers = H._Hdr(h.headers); assert ui.handle_post(h), path; return h

ok = True
def check(c, w):
    global ok; print(("PASS " if c else "FAIL ") + w); ok = ok and c

h = get("/"); check(h.code == 200 and "No jobs yet." in h.body and 'action="/ui/jobs"' not in h.body and "<textarea" not in h.body, "index renders an empty job list and has no submit form")
job = eda.canonical_job({"service": "my-service", "instruction": "Add a skill about <script>alert(1)</script> rollouts", "ticket_key": "SRE-5", "dry_run": True})
eda.enqueue(job); jid = job["id"]
check(eda.R.llen("jobs") == 1 and eda.job_view(jid)["dry_run"] is True, "a job created through intake is queued")
h = get("/"); check(h.code == 200 and f"/ui/jobs/{jid}" in h.body and "SRE-5" in h.body, "index lists the job with its link and ticket key")
h = get(f"/ui/jobs/{jid}")
check(h.code == 200 and "<script>alert(1)</script>" not in h.body and "&lt;script&gt;" in h.body, "instruction text is HTML-escaped (no script injection)")
check('hx-get="/ui/jobs/%s/events?live=1"' % jid in h.body and eda.job_view(jid)["branch"].startswith("SRE-5/") and eda.job_view(jid)["branch"] in h.body, "a queued job polls its events and shows the runtime-owned branch")
eda.setf(jid, status="running", stage="agent", attempts="1"); eda.emit(jid, "agent", "attempt 1/3: fresh session")
eda.R.rpush(f"subs:{jid}", json.dumps({"t": "10:00:01", "ok": False, "msg": "[shell] walk.sh:3:1: SC2164"}))
eda.R.rpush(f"subs:{jid}", json.dumps({"t": "10:00:09", "ok": True, "msg": "checks passed"}))
h = get(f"/ui/jobs/{jid}")
check('class="stage cur">agent' in h.body and "FAIL" in h.body and "PASS" in h.body and "SC2164" in h.body, "stage chip, validator FAIL then PASS and the report are shown")
h = get(f"/ui/jobs/{jid}/events?live=1"); check("HX-Refresh" not in h.out_headers and "attempt 1/3" in h.body, "events fragment while running: content, no refresh")
eda.setf(jid, status="validated-dry-run", stage="done")
h = get(f"/ui/jobs/{jid}/events?live=1"); check(h.out_headers.get("HX-Refresh") == "true", "when the job finishes the live fragment asks htmx to reload the page")
eda.setf(jid, pr_url="https://github.com/OWNER/REPO/pull/7")
h = get("/ui/jobs"); check("validated (dry run)" in h.body and "SRE-5" in h.body and "pull/7" in h.body, "job list shows the friendly status, ticket and PR link")
h = get(f"/ui/jobs/{jid}"); check("Draft PR" in h.body and "pull/7" in h.body, "job page links the draft PR")
# retry rules
h = post(f"/ui/jobs/{jid}/retry", {}); check(eda.job_view(jid)["status"] == "validated-dry-run" and eda.R.llen("jobs") == 1, "retry on a non-failed job does nothing")
eda.setf(jid, status="failed", error="boom", last_error="[shell] x")
h = get("/ui/queue"); check("Retry" in h.body and "boom" in h.body, "a failed job appears in the queue panel with a Retry button")
h = post(f"/ui/jobs/{jid}/retry", {}); v = eda.job_view(jid)
check(v["status"] == "queued" and v["retry"] == "1" and eda.R.llen("jobs") == 2, "retry on a failed job requeues it and flags the retry")
check(get("/ui/jobs/aaaaaaaaaaaa").code == 404, "unknown job is a 404")
h = H("/ui/jobs"); h.headers = H._Hdr(h.headers); check(ui.handle_post(h) is False and eda.R.llen("jobs") == 2, "POST /ui/jobs (the removed submit form) is not handled and queues nothing")
h = post("/ui/jobs/aaaaaaaaaaaa/retry", {}); check(h.code == 404, "retrying an unknown job is a 404")
sys.exit(0 if ok else 1)
