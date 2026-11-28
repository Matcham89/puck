"""Run in the gate image:  docker run --rm -v $PWD/application/tests:/t --entrypoint python3 <image> /t/test_submit.py
Checks the agent-side submit script: only changed files are uploaded, PR.md is required, transport failures are retried."""
import io, os, re, subprocess, sys, tarfile, tempfile, threading, hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer

src = open("/app/eda.py").read()
script = re.search(r'SUBMIT_SH = r"""(.*?)"""', src, re.S).group(1)
fails = {"left": 2}; got = []

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if fails["left"] > 0:                       # simulate the proxy dropping the connection mid-request
            fails["left"] -= 1; self.connection.close(); return
        import base64; got.append(base64.b64decode(body)); out = b'{"ok": true, "message": "checks passed"}'
        self.send_response(200); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
srv = HTTPServer(("127.0.0.1", 18999), H); threading.Thread(target=srv.serve_forever, daemon=True).start()

root = tempfile.mkdtemp(); os.makedirs(f"{root}/.eda"); os.makedirs(f"{root}/plugins/a")
open(f"{root}/plugins/a/keep.md", "w").write("unchanged\n"); open(f"{root}/plugins/a/edit.md", "w").write("old\n"); open(f"{root}/README.md", "w").write("readme\n")
man = [f"{hashlib.sha256(open(f'{root}/{p}','rb').read()).hexdigest()}  {p}" for p in ("plugins/a/keep.md", "plugins/a/edit.md", "README.md")]
open(f"{root}/.eda/manifest", "w").write("\n".join(sorted(man)) + "\n")
open(f"{root}/.eda/submit.sh", "w").write(script.replace("@GATE@", "http://127.0.0.1:18999").replace("@JOB@", "abc123abc123"))
ok = True
def check(c, what):
    global ok; print(("PASS " if c else "FAIL ") + what); ok = ok and c

r = subprocess.run(["sh", f"{root}/.eda/submit.sh"], capture_output=True, text=True)
check('"ok":false' in r.stdout.replace(" ", "") and "PR.md" in r.stdout, "refuses to run without PR.md")
open(f"{root}/PR.md", "w").write("Title\n\nbody\n"); open(f"{root}/plugins/a/edit.md", "w").write("new\n"); open(f"{root}/plugins/a/new.md", "w").write("added\n")
r = subprocess.run(["sh", f"{root}/.eda/submit.sh"], capture_output=True, text=True)
check('"ok": true' in r.stdout, "succeeds after two dropped connections (retries transport failures): " + r.stdout.strip()[:60])
print("received", len(got), "bodies; first:", len(got[0]) if got else None, repr(got[0][:12]) if got else None)
names = sorted(m.name for m in tarfile.open(fileobj=io.BytesIO(got[0]), mode="r:gz").getmembers()) if got else []
check(names == ["PR.md", "plugins/a/edit.md", "plugins/a/new.md"], f"uploads only changed + new files + PR.md: {names}")
os.remove(f"{root}/plugins/a/keep.md"); got.clear()
r = subprocess.run(["sh", f"{root}/.eda/submit.sh"], capture_output=True, text=True)
tf = tarfile.open(fileobj=io.BytesIO(got[0]), mode="r:gz") if got else None
check(tf and ".eda/deleted" in tf.getnames() and tf.extractfile(".eda/deleted").read().decode().split() == ["plugins/a/keep.md"], "a file removed since the snapshot is reported as a deletion")
sys.exit(0 if ok else 1)
