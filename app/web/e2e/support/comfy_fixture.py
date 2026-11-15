"""Test-only HTTP contract fixture. It does not perform diffusion or validate hardware."""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True)
args = parser.parse_args()
image = Path(args.image).read_bytes()
lock = threading.Lock()
jobs = {}
state = {"delay": 0.5, "hold": False, "prompts": 0, "noop_cancels": 0, "cancels": 0, "global_interrupts": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, content_type="application/json"):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        if path.startswith("/object_info/"):
            node = path.split("/")[-1]
            required = {}
            if node == "CheckpointLoaderSimple":
                required = {"ckpt_name": [["fixture.safetensors"]]}
            if node == "KSampler":
                required = {"sampler_name": [["euler"]], "scheduler": [["normal"]]}
            return self.reply({node: {"input": {"required": required}}})
        if path == "/system_stats":
            return self.reply(
                {
                    "system": {"comfyui_version": "HTTP test fixture, no GPU"},
                    "devices": [{"name": "Test-only CPU", "type": "cpu", "vram_total": 0, "vram_free": 0}],
                }
            )
        if path == "/api/jobs":
            return self.reply({"jobs": [], "pagination": {"total": 0}})
        if path == "/test/stats":
            with lock:
                return self.reply(dict(state))
        if path.startswith("/api/jobs/"):
            pid = path.split("/")[-1]
            with lock:
                job = jobs.get(pid)
                if not job:
                    return self.reply({"error": "missing"}, 404)
                if (
                    job["status"] == "in_progress"
                    and not state["hold"]
                    and time.monotonic() - job["start"] >= state["delay"]
                ):
                    job["status"] = "completed"
                return self.reply({"id": pid, "status": job["status"]})
        if path.startswith("/history/"):
            pid = path.split("/")[-1]
            with lock:
                if pid not in jobs or jobs[pid]["status"] != "completed":
                    return self.reply({})
            return self.reply(
                {
                    pid: {
                        "outputs": {
                            "7": {
                                "images": [
                                    {
                                        "filename": pid + "_00001_.png",
                                        "subfolder": "Taverna",
                                        "type": "output",
                                    }
                                ]
                            }
                        }
                    }
                }
            )
        if path == "/view":
            query = parse_qs(url.query)
            filename = query.get("filename", [""])[0]
            if query.get("subfolder") != ["Taverna"] or not any(
                filename == pid + "_00001_.png" for pid in jobs
            ):
                return self.reply({"error": "output"}, 404)
            return self.reply(image, content_type="image/png")
        self.reply({"error": "unknown"}, 404)

    def do_POST(self):
        path = urlsplit(self.path).path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if path == "/test/config":
            with lock:
                if "delay" in body:
                    state["delay"] = body["delay"]
                if "hold" in body:
                    state["hold"] = body["hold"]
            return self.reply({"ok": True})
        if path == "/prompt":
            pid = body["prompt_id"]
            graph = body["prompt"]
            if set(n["class_type"] for n in graph.values()) != {
                "CheckpointLoaderSimple",
                "CLIPTextEncode",
                "EmptyLatentImage",
                "KSampler",
                "VAEDecode",
                "SaveImage",
            }:
                return self.reply({"error": "unsupported graph"}, 400)
            with lock:
                jobs[pid] = {"status": "in_progress", "start": time.monotonic()}
                state["prompts"] += 1
            return self.reply({"prompt_id": pid, "number": 1, "node_errors": {}})
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            pid = path.split("/")[3]
            with lock:
                if pid not in jobs:
                    state["noop_cancels"] += 1
                    return self.reply({"cancelled": False})
                jobs[pid]["status"] = "cancelled"
                state["cancels"] += 1
            return self.reply({"cancelled": True})
        if path == "/interrupt":
            with lock:
                state["global_interrupts"] += 1
        self.reply({"error": "unknown"}, 404)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(json.dumps({"port": server.server_address[1]}), flush=True)
server.serve_forever()
