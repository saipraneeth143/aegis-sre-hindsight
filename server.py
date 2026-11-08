"""
AegisSRE HTTP & REST API Server.

Serves:
- Interactive Mission Control UI & Pictured Slide Deck at `http://localhost:8080/`
- JSON REST endpoints for Hindsight `retain`, `recall`, `reflect`, and Side-by-Side Triage:
  - GET  `/api/health`
  - GET  `/api/memories?bank_id=...`
  - GET  `/api/learning-curve?bank_id=...`
  - GET  `/api/tool-guard-demo`
  - POST `/api/analyze`
  - POST `/api/retain`
  - POST `/api/recall`
  - POST `/api/reflect`
  - POST `/api/reset`
"""

from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from agent.aegis_sre import AegisSREAgent
from config_loader import load_env_files, save_keys_to_project_env
from data.realistic_incidents import DEFAULT_BANK_ID

load_env_files()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

GLOBAL_AGENT = AegisSREAgent()


class AegisRequestHandler(BaseHTTPRequestHandler):
    """Thread-safe HTTP request handler for AegisSRE API and static assets."""

    def log_message(self, format: str, *args: Any) -> None:
        # Quiet standard request logging during tests unless AEGIS_VERBOSE is set
        if os.environ.get("AEGIS_VERBOSE") == "1":
            super().log_message(format, *args)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)
        bank_id = query_params.get("bank_id", [DEFAULT_BANK_ID])[0]

        if path == "/api/health":
            env_flags = load_env_files()
            self._send_json(
                {
                    "status": "ok",
                    "service": "AegisSRE Incident Commander",
                    "hindsight_backend": GLOBAL_AGENT.memory.backend_mode,
                    "groq_live_enabled": GLOBAL_AGENT.llm.is_live_groq,
                    "env_status": env_flags,
                    "supported_groq_models": GLOBAL_AGENT.llm.SUPPORTED_MODELS,
                }
            )
            return

        if path == "/api/env-status":
            self._send_json(load_env_files())
            return

        if path == "/api/memories":
            GLOBAL_AGENT.ensure_seeded(bank_id)
            memories = [m.to_dict() for m in GLOBAL_AGENT.memory.list_memories(bank_id=bank_id)]
            graph = GLOBAL_AGENT.memory.get_knowledge_graph(bank_id=bank_id)
            self._send_json({"bank_id": bank_id, "memories": memories, "graph": graph})
            return

        if path == "/api/learning-curve":
            GLOBAL_AGENT.ensure_seeded(bank_id)
            self._send_json(GLOBAL_AGENT.get_learning_curve_report(bank_id=bank_id))
            return

        if path == "/api/tool-guard-demo":
            self._send_json(GLOBAL_AGENT.demonstrate_tool_guard_self_healing())
            return

        # Serve favicon.ico cleanly (no 404 in browser console)
        if path == "/favicon.ico":
            svg_icon = (
                b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
                b'<path fill="#06b6d4" d="M32 4L8 14v18c0 16.5 10.2 31.6 24 36 13.8-4.4 24-19.5 24-36V14L32 4z"/>'
                b'<path fill="#070b14" d="M32 12l16 6.8v13.2c0 11.8-6.8 22.6-16 26.4-9.2-3.8-16-14.6-16-26.4V18.8L32 12z"/>'
                b'<circle cx="32" cy="32" r="8" fill="#10b981"/>'
                b"</svg>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(svg_icon)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(svg_icon)
            return

        # Serve dedicated interactive pictured slide deck on /slides
        if path == "/slides":
            slides_file = STATIC_DIR / "slides.html"
            target = slides_file if slides_file.is_file() else (STATIC_DIR / "index.html")
            content = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # Serve index.html on root
        if path in ("/", "/index.html"):
            index_file = STATIC_DIR / "index.html"
            content = index_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # Serve static files
        if path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            target_file = (STATIC_DIR / rel_path).resolve()
            if str(target_file).startswith(str(STATIC_DIR.resolve())) and target_file.is_file():
                mime_type, _ = mimetypes.guess_type(str(target_file))
                content = target_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime_type or "application/octet-stream")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        self._send_json({"error": f"Not found: {path}"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()
        bank_id = body.get("bank_id", DEFAULT_BANK_ID)

        if path == "/api/analyze":
            GLOBAL_AGENT.ensure_seeded(bank_id)
            alert_text = body.get("alert_text", "")
            scenario_id = body.get("scenario_id")
            model = body.get("model", "openai/gpt-oss-120b")
            result = GLOBAL_AGENT.analyze_incident(
                alert_text=alert_text,
                bank_id=bank_id,
                scenario_id=scenario_id,
                model=model,
            )
            self._send_json(result)
            return

        if path == "/api/retain":
            GLOBAL_AGENT.ensure_seeded(bank_id)
            content = body.get("content", "")
            memory_type = body.get("memory_type")
            metadata = body.get("metadata", {})
            rec = GLOBAL_AGENT.memory.retain(
                bank_id=bank_id,
                content=content,
                memory_type=memory_type,
                metadata=metadata,
                days_ago=0.0,
            )
            self._send_json({"status": "retained", "memory": rec.to_dict()})
            return

        if path == "/api/recall":
            GLOBAL_AGENT.ensure_seeded(bank_id)
            query = body.get("query", "")
            top_k = int(body.get("top_k", 5))
            res = GLOBAL_AGENT.memory.recall(bank_id=bank_id, query=query, top_k=top_k)
            self._send_json(res.to_dict())
            return

        if path == "/api/reflect":
            GLOBAL_AGENT.ensure_seeded(bank_id)
            query = body.get("query", "")
            persist = bool(body.get("persist_observation", False))
            res = GLOBAL_AGENT.memory.reflect(
                bank_id=bank_id, query=query, persist_observation=persist
            )
            self._send_json(res.to_dict())
            return

        if path == "/api/reset":
            seeded = GLOBAL_AGENT.reset_and_seed(bank_id=bank_id)
            self._send_json({"status": "reset", "bank_id": bank_id, "count": len(seeded)})
            return

        if path == "/api/save-env":
            flags = save_keys_to_project_env(
                groq_api_key=body.get("groq_api_key"),
                hindsight_api_key=body.get("hindsight_api_key"),
                hindsight_base_url=body.get("hindsight_base_url"),
            )
            self._send_json({"status": "saved", "env_status": flags})
            return

        self._send_json({"error": f"Unknown POST endpoint: {path}"}, status=404)


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    os.environ["AEGIS_VERBOSE"] = "1"
    server = ThreadingHTTPServer((host, port), AegisRequestHandler)
    print(f"\n🛡️  AegisSRE Mission Control & Hindsight Memory Server running at:")
    print(f"    ➜  http://{host}:{port}/")
    print(f"    ➜  Interactive Pitch Slides: http://{host}:{port}/slides\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down AegisSRE server.")
        server.server_close()


if __name__ == "__main__":
    run_server()

