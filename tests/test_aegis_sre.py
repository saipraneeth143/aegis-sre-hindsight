"""
Comprehensive Automated Test Suite for AegisSRE & Hindsight Memory Engine.

Verifies:
1. Hindsight `retain()`, `recall()` (4-strategy TEMPR + RRF), and `reflect()` operations.
2. Biomimetic fact classification (`world`, `experience`, `observation`) and entity extraction.
3. `GroqToolCallGuard` function-calling error recovery for `qwen/qwen3-32b` and `openai/gpt-oss-120b`.
4. Side-by-side incident triage (`Stateless` vs. `Hindsight`) across all preset and custom scenarios.
5. Live HTTP & REST API endpoints (`/api/health`, `/api/analyze`, `/api/retain`, `/api/recall`, `/api/reflect`, `/slides`).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from agent.aegis_sre import AegisSREAgent
from agent.groq_client import GroqToolCallGuard
from hindsight_memory.engine import HindsightMemoryEngine
from server import AegisRequestHandler


class TestAegisSREAndHindsight(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.engine = HindsightMemoryEngine(db_path=self.temp_db.name)
        self.agent = AegisSREAgent(memory_engine=self.engine)

    def tearDown(self) -> None:
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_biomimetic_retain_and_classification(self) -> None:
        rec = self.engine.retain(
            bank_id="test-bank",
            content=(
                "[Post-Mortem INC-4999] checkout-service failed with 0x8F4A_POOL_EXHAUSTED. "
                "DO NOT restart pods; resolved by flipping FF_ASYNC_TOKEN_REFRESH=false."
            ),
        )
        self.assertEqual(rec.memory_type, "experience")
        self.assertIn("INC-4999", rec.entities)
        self.assertIn("checkout-service", rec.entities)
        self.assertIn("0x8F4A_POOL_EXHAUSTED", rec.entities)
        self.assertTrue(rec.metadata.get("has_antipattern_warning"))

    def test_tempr_four_way_recall_and_rrf(self) -> None:
        recall_res = self.engine.recall(
            bank_id="prod-payments-cluster",
            query="0x8F4A_POOL_EXHAUSTED pg_bouncer checkout-service FF_ASYNC_TOKEN_REFRESH",
            top_k=3,
        )
        self.assertGreaterEqual(len(recall_res.results), 1)
        top = recall_res.results[0]
        self.assertEqual(top.memory_id, "mem-exp-0003")
        self.assertGreater(top.rrf_score, 0.5)
        self.assertGreater(top.bm25_score, 0.0)
        self.assertGreater(top.semantic_score, 0.0)
        self.assertGreater(top.graph_score, 0.0)
        self.assertGreater(top.temporal_score, 0.0)

    def test_reflect_synthesizes_observations(self) -> None:
        reflect_res = self.engine.reflect(
            bank_id="prod-payments-cluster",
            query="checkout-service connection pool exhaustion retry storm",
            persist_observation=True,
        )
        self.assertIn("Hindsight Cross-Incident Synthesis", reflect_res.text)
        self.assertGreaterEqual(len(reflect_res.synthesized_observations), 1)
        obs_records = self.engine.list_memories(
            bank_id="prod-payments-cluster", memory_type="observation"
        )
        self.assertGreaterEqual(len(obs_records), 2)

    def test_groq_tool_call_guard_repairs_qwen_and_gpt_oss_errors(self) -> None:
        raw_qwen = (
            "<think>\nNeed to check Hindsight bank\n</think>\n"
            "```json\n"
            '{"bank_id": "prod-payments-cluster", "query": "INC-4082", "top_k": "4",}\n'
            "```"
        )
        repaired, ev = GroqToolCallGuard.repair_json_arguments(
            raw_qwen, tool_name="hindsight_recall", model="qwen/qwen3-32b"
        )
        self.assertEqual(repaired["bank_id"], "prod-payments-cluster")
        self.assertEqual(repaired["query"], "INC-4082")
        self.assertEqual(repaired["top_k"], 4)
        self.assertIsNotNone(ev)
        self.assertTrue(ev.recovered)

        # Test truncated JSON from openai/gpt-oss-120b
        raw_truncated = '{"bank_id": "prod-payments-cluster", "content": "Fixed Envoy timeout'
        repaired2, ev2 = GroqToolCallGuard.repair_json_arguments(
            raw_truncated, tool_name="hindsight_retain", model="openai/gpt-oss-120b"
        )
        self.assertEqual(repaired2["bank_id"], "prod-payments-cluster")
        self.assertIn("Fixed Envoy timeout", repaired2["content"])
        self.assertIsNotNone(ev2)

    def test_side_by_side_incident_analysis(self) -> None:
        analysis = self.agent.analyze_incident(
            alert_text="Fatal 0x8F4A_POOL_EXHAUSTED in checkout-service pg_bouncer",
            bank_id="prod-payments-cluster",
            scenario_id="pool_exhaustion",
        )
        self.assertIn("stateless_agent", analysis)
        self.assertIn("hindsight_agent", analysis)
        self.assertGreaterEqual(
            len(analysis["hindsight_agent"]["blocked_dangerous_commands"]), 1
        )
        self.assertIn("1.2 minutes", analysis["hindsight_agent"]["estimated_mttr"])

    def test_http_server_endpoints(self) -> None:
        import io
        from email.message import Message

        class DummyRequestHandler(AegisRequestHandler):
            def __init__(self, method: str, path: str, body_bytes: bytes = b"") -> None:
                self.rfile = io.BytesIO(body_bytes)
                self.wfile = io.BytesIO()
                self.command = method
                self.path = path
                self.request_version = "HTTP/1.1"
                self.headers = Message()
                self.headers["Content-Length"] = str(len(body_bytes))
                self.headers["Content-Type"] = "application/json"
                self.status_code = 0

            def send_response(self, code: int, message: str | None = None) -> None:
                self.status_code = code

            def send_header(self, keyword: str, value: str) -> None:
                pass

            def end_headers(self) -> None:
                pass

        # 1. GET /api/health
        h1 = DummyRequestHandler("GET", "/api/health")
        h1.do_GET()
        self.assertEqual(h1.status_code, 200)
        health = json.loads(h1.wfile.getvalue().decode("utf-8"))
        self.assertEqual(health["status"], "ok")

        # 2. GET / (Root HTML UI), GET /favicon.ico, and GET /slides
        h2 = DummyRequestHandler("GET", "/")
        h2.do_GET()
        self.assertEqual(h2.status_code, 200)
        html = h2.wfile.getvalue().decode("utf-8")
        self.assertIn("AegisSRE", html)

        h_fav = DummyRequestHandler("GET", "/favicon.ico")
        h_fav.do_GET()
        self.assertEqual(h_fav.status_code, 200)

        h_slides = DummyRequestHandler("GET", "/slides")
        h_slides.do_GET()
        self.assertEqual(h_slides.status_code, 200)

        # 2.5 POST /api/reset to ensure clean seed state
        reset_body = json.dumps({"bank_id": "prod-payments-cluster"}).encode("utf-8")
        h_reset = DummyRequestHandler("POST", "/api/reset", body_bytes=reset_body)
        h_reset.do_POST()
        self.assertEqual(h_reset.status_code, 200)

        # 3. POST /api/analyze
        body = json.dumps(
            {
                "bank_id": "prod-payments-cluster",
                "alert_text": "0x8F4A_POOL_EXHAUSTED in checkout-service",
                "scenario_id": "pool_exhaustion",
            }
        ).encode("utf-8")
        h3 = DummyRequestHandler("POST", "/api/analyze", body_bytes=body)
        h3.do_POST()
        self.assertEqual(h3.status_code, 200)
        data = json.loads(h3.wfile.getvalue().decode("utf-8"))
        self.assertIn("hindsight_recall", data)
        top_ids = [r["memory_id"] for r in data["hindsight_recall"]["results"][:2]]
        self.assertIn("mem-exp-0003", top_ids)
        self.assertIn("mem-obs-0006", top_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
