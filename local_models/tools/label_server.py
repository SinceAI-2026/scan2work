"""Serve the worktree for label.html and save the expert's labels.

  python tools/label_server.py [--labels data/pipeline/labels.json] [--port 8002]

GET  any file under the worktree (label.html, objects.json, crops...), plus GET /labels: the
     labels the page shows, merged as the classifier merges them (the expert's label wins), each
     with its source.
POST /labels {"id", "label", "merged_from"}: sets the expert's label of one object ("label": null
     removes it, for undo), with source "expert" and the time. The file survives restarts.
POST /train: runs pipeline/classifier.py on the expert's labels and the stand-in labels file
     (labels-claude.json, if present) and returns its report and the new queue.
Bound to 127.0.0.1 only.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
pipeline = repo / "data" / "pipeline"
STAND_IN = pipeline / "labels-claude.json"  # an AI's labels until a human's replace them


def read(path):
    return json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}


class Handler(SimpleHTTPRequestHandler):
    labels = pipeline / "labels.json"

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0] == "/labels":
            return self.reply({**read(STAND_IN), **read(self.labels)})
        super().do_GET()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
        if self.path == "/labels":
            labels = read(self.labels)
            if body.get("label") is None:
                labels.pop(body["id"], None)
            else:
                labels[body["id"]] = {"label": body["label"], "source": "expert",
                                      "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "merged_from": body.get("merged_from")}
            self.labels.parent.mkdir(parents=True, exist_ok=True)
            self.labels.write_text(json.dumps(labels, indent=1))
            return self.reply({"labels": len(labels)})
        if self.path == "/train":
            files = [str(p) for p in (STAND_IN, self.labels) if p.exists()] or [str(self.labels)]
            run = subprocess.run([sys.executable, str(repo / "pipeline" / "classifier.py"), "--labels", *files],
                                 capture_output=True, text=True)
            if run.returncode:
                self.send_error(500, run.stderr[-500:])
                return
            return self.reply({"report": read(pipeline / "eval" / "classifier.json"), "log": run.stdout,
                               "queue": json.loads((pipeline / "label_queue.json").read_text())})
        self.send_error(404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path, default=pipeline / "labels.json", help="the expert's labels file")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    Handler.labels = args.labels.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(repo)))
    print(f"http://127.0.0.1:{args.port}/label.html  (labels: {Handler.labels})")
    server.serve_forever()
