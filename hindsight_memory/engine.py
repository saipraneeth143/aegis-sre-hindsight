"""
Biomimetic Agent Memory Engine powered by Vectorize Hindsight.

Supports:
1. Official `hindsight-client` SDK (`from hindsight_client import Hindsight`) when configured
   with HINDSIGHT_API_KEY and HINDSIGHT_BASE_URL (https://api.hindsight.vectorize.io).
2. Local SQLite-persisted Biomimetic TEMPR Engine with 4-strategy parallel retrieval
   (Semantic Cosine + BM25 Keyword + Entity Knowledge Graph Traversal + Temporal Decay)
   fused via Reciprocal Rank Fusion (RRF), ensuring 100% deterministic reliability for
   offline or local execution while mirroring Hindsight's exact data model (`world`,
   `experience`, and `observation` memory tiers).
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config_loader import PROJECT_ROOT, load_env_files

# Optional import of official Vectorize Hindsight Python SDK
try:
    from hindsight_client import Hindsight as OfficialHindsightClient  # type: ignore
except ImportError:
    OfficialHindsightClient = None


STOP_WORDS: Set[str] = {
    "the", "and", "for", "that", "this", "with", "from", "was", "were", "are",
    "have", "has", "had", "not", "but", "what", "when", "where", "which", "who",
    "how", "why", "can", "could", "would", "should", "into", "during", "after",
    "before", "over", "under", "between", "through", "about", "our", "their",
}


@dataclass
class MemoryRecord:
    """Represents a structured biomimetic memory unit inside a Hindsight bank."""

    memory_id: str
    bank_id: str
    memory_type: str  # "world" | "experience" | "observation"
    text: str
    entities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    days_ago: float = 0.0
    # Per-strategy TEMPR scores populated during recall()
    rrf_score: float = 0.0
    semantic_score: float = 0.0
    bm25_score: float = 0.0
    graph_score: float = 0.0
    temporal_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecallResult:
    """Result returned by Hindsight `recall(bank_id, query)`."""

    bank_id: str
    query: str
    results: List[MemoryRecord]
    strategy_breakdown: Dict[str, Any]
    latency_ms: float
    backend_mode: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bank_id": self.bank_id,
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "strategy_breakdown": self.strategy_breakdown,
            "latency_ms": round(self.latency_ms, 2),
            "backend_mode": self.backend_mode,
        }


@dataclass
class ReflectResult:
    """Result returned by Hindsight `reflect(bank_id, query)`."""

    bank_id: str
    query: str
    text: str
    synthesized_observations: List[str]
    supporting_memories: List[MemoryRecord]
    latency_ms: float
    backend_mode: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bank_id": self.bank_id,
            "query": self.query,
            "text": self.text,
            "synthesized_observations": self.synthesized_observations,
            "supporting_memories": [m.to_dict() for m in self.supporting_memories],
            "latency_ms": round(self.latency_ms, 2),
            "backend_mode": self.backend_mode,
        }


class HindsightMemoryEngine:
    """
    Unified Hindsight Memory Layer implementing `retain`, `recall`, and `reflect`.

    Organizes operational intelligence into three biomimetic memory categories:
    - `world`: Infrastructure topology, dependencies, ownership, static architecture facts.
    - `experience`: Historical incidents, post-mortems, verified fixes, and failed runbooks.
    - `observation`: High-level cross-incident engineering laws synthesized via `reflect()`.
    """

    # Domain concept expansions for dense semantic similarity matching
    CONCEPT_SYNONYMS: Dict[str, List[str]] = {
        "pool": ["connection", "pg_bouncer", "exhausted", "max_client_conn", "database", "rds", "postgres", "leak"],
        "timeout": ["deadline", "latency", "slow", "hang", "cascade", "504", "grpc", "upstream", "stall"],
        "redis": ["cache", "eviction", "oom", "memory", "cluster", "cart", "keys", "thundering", "herd"],
        "restart": ["rollout", "reboot", "drain", "kill", "bounce", "crashloop", "pod"],
        "auth": ["token", "jwt", "oauth", "middleware", "session", "refresh", "flag"],
        "deploy": ["release", "commit", "canary", "rollback", "pr", "diff", "pipeline", "version"],
        "rate": ["throttle", "kong", "ingress", "limit", "429", "traffic", "spike", "drop"],
    }

    KNOWN_ENTITIES: List[str] = [
        "checkout-service",
        "payment-gateway-grpc",
        "redis-cart-cluster",
        "auth-middleware",
        "pg-ledger-primary",
        "kong-edge-ingress",
        "inventory-worker",
        "envoy-sidecar",
        "INC-3910",
        "INC-4082",
        "INC-4119",
        "INC-4205",
        "0x8F4A_POOL_EXHAUSTED",
        "GRPC_DEADLINE_EXCEEDED",
        "REDIS_OOM_EVICTION_SPIKE",
        "FF_ASYNC_TOKEN_REFRESH",
        "pg_bouncer",
    ]

    def __init__(
        self,
        db_path: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        load_env_files()
        self.db_path = db_path or str(PROJECT_ROOT / "aegis_hindsight_memory.db")
        self.base_url = (
            base_url
            or os.environ.get("HINDSIGHT_BASE_URL")
            or "https://api.hindsight.vectorize.io"
        ).rstrip("/")
        self._explicit_api_key = api_key

        self._sdk_client: Any = None
        if self.api_key and OfficialHindsightClient is not None:
            try:
                self._sdk_client = OfficialHindsightClient(
                    base_url=self.base_url,
                    api_key=self.api_key,
                )
            except Exception:
                self._sdk_client = None

        self._init_db()

    @property
    def api_key(self) -> str:
        load_env_files()
        return (self._explicit_api_key or os.environ.get("HINDSIGHT_API_KEY", "")).strip()

    @property
    def backend_mode(self) -> str:
        if self._sdk_client is not None:
            return "hindsight-cloud-sdk"
        if self.api_key:
            return "hindsight-cloud-rest"
        return "hindsight-biomimetic-tempr-engine"

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hindsight_memories (
                    memory_id TEXT PRIMARY KEY,
                    bank_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    entities_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    days_ago REAL DEFAULT 0.0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bank_id ON hindsight_memories(bank_id)"
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Entity & Biomimetic Fact Extraction
    # ------------------------------------------------------------------
    def extract_entities(self, text: str) -> List[str]:
        """Extracts microservice names, incident IDs, error codes, and config keys."""
        found: Set[str] = set()
        text_lower = text.lower()

        for entity in self.KNOWN_ENTITIES:
            if entity.lower() in text_lower:
                found.add(entity)

        # Match INC-XXXX patterns
        for match in re.findall(r"\bINC-\d{3,6}\b", text, flags=re.IGNORECASE):
            found.add(match.upper())

        # Match hex error codes or UPPER_SNAKE_CASE flags
        for match in re.findall(r"\b0x[0-9A-Fa-f]{4}_[A-Z0-9_]+\b", text):
            found.add(match)
        for match in re.findall(r"\b[A-Z]{2,}_[A-Z0-9_]{4,}\b", text):
            found.add(match)

        # Match microservice kebab-case names ending in -service, -worker, -cluster, -ingress, -grpc, -primary
        for match in re.findall(
            r"\b[a-z0-9]+-(?:service|worker|cluster|ingress|grpc|primary|middleware|sidecar)\b",
            text_lower,
        ):
            found.add(match)

        return sorted(found)

    def classify_memory_type(self, text: str, explicit_type: Optional[str] = None) -> str:
        """
        Classifies unstructured operational input into Hindsight's three biomimetic tiers:
        - `world`: Static infrastructure facts, architecture dependencies, service owners
        - `experience`: Specific incident timeline, root cause, worked/failed runbook actions
        - `observation`: Cross-incident rules, patterns, or preventive invariants
        """
        if explicit_type in ("world", "experience", "observation"):
            return explicit_type

        lowered = text.lower()
        if any(
            kw in lowered
            for kw in (
                "pattern across",
                "invariant:",
                "observation:",
                "whenever ",
                "always throttle",
                "learned rule",
                "synthesized",
                "across multiple incidents",
            )
        ):
            return "observation"
        if any(
            kw in lowered
            for kw in (
                "inc-",
                "outage",
                "root cause",
                "mitigation",
                "resolved by",
                "failed runbook",
                "do not run",
                "post-mortem",
                "rollback",
                "caused a",
            )
        ):
            return "experience"
        return "world"

    # ------------------------------------------------------------------
    # 1. RETAIN: Store & Extract Structured Facts into Memory Bank
    # ------------------------------------------------------------------
    def retain(
        self,
        bank_id: str,
        content: str,
        memory_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        days_ago: float = 0.0,
        memory_id: Optional[str] = None,
    ) -> MemoryRecord:
        """
        Hindsight `retain(bank_id, content)` operation.

        Processes raw text, post-mortem notes, or war-room logs, extracts entities,
        classifies biomimetic memory tier (`world`, `experience`, `observation`),
        syncs with Hindsight Cloud if configured, and persists to the local bank.
        """
        clean_text = content.strip()
        resolved_type = self.classify_memory_type(clean_text, memory_type)
        entities = self.extract_entities(clean_text)
        meta = dict(metadata or {})

        # Detect if this experience memory records a dangerous/failed runbook command
        lower_text = clean_text.lower()
        if any(
            phrase in lower_text
            for phrase in (
                "do not ",
                "never ",
                "worsened",
                "failed runbook",
                "anti-pattern",
                "triggered a retry storm",
                "caused oom",
            )
        ):
            meta["has_antipattern_warning"] = True

        # Sync to Hindsight Cloud SDK or REST endpoint if configured
        if self._sdk_client is not None:
            try:
                self._sdk_client.retain(bank_id=bank_id, content=clean_text)
                meta["cloud_synced"] = True
            except Exception as exc:
                meta["cloud_sync_note"] = f"Local fallback used ({type(exc).__name__})"
        elif self.api_key:
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/v1/banks/{bank_id}/retain",
                    data=json.dumps({"bank_id": bank_id, "content": clean_text}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=4.0) as _:
                    meta["cloud_synced"] = True
            except Exception as exc:
                meta["cloud_sync_note"] = f"Local fallback used ({type(exc).__name__})"

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            if not memory_id:
                count_row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM hindsight_memories WHERE bank_id = ?",
                    (bank_id,),
                ).fetchone()
                seq = (count_row["cnt"] if count_row else 0) + 1
                memory_id = f"mem-{bank_id[:8]}-{seq:04d}"

            conn.execute(
                """
                INSERT OR REPLACE INTO hindsight_memories
                (memory_id, bank_id, memory_type, text, entities_json, metadata_json, created_at, days_ago)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    bank_id,
                    resolved_type,
                    clean_text,
                    json.dumps(entities),
                    json.dumps(meta),
                    now_iso,
                    float(days_ago),
                ),
            )
            conn.commit()

        return MemoryRecord(
            memory_id=memory_id,
            bank_id=bank_id,
            memory_type=resolved_type,
            text=clean_text,
            entities=entities,
            metadata=meta,
            created_at=now_iso,
            days_ago=float(days_ago),
        )

    # ------------------------------------------------------------------
    # 2. RECALL: 4-Strategy Parallel TEMPR Search + RRF Fusion
    # ------------------------------------------------------------------
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        tokens = re.findall(r"[a-z0-9_.\-]+", text.lower())
        return [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]

    def _expand_semantic_tokens(self, tokens: List[str]) -> Set[str]:
        expanded = set(tokens)
        for token in tokens:
            for root, synonyms in self.CONCEPT_SYNONYMS.items():
                if root in token or any(s in token for s in synonyms):
                    expanded.add(root)
                    expanded.update(synonyms)
        return expanded

    def recall(
        self,
        bank_id: str,
        query: str,
        top_k: int = 6,
    ) -> RecallResult:
        """
        Hindsight `recall(bank_id, query)` operation executing TEMPR multi-strategy retrieval:
        1. Semantic Similarity (Concept-expanded cosine similarity)
        2. BM25 Keyword Matching (Exact IDF-weighted term frequency over error codes, SHAs, flags)
        3. Entity Graph Traversal (1-hop & 2-hop entity co-occurrence across the bank's graph)
        4. Temporal Reasoning (Exponential recency weighting with observation permanence boost)
        Results are fused via Reciprocal Rank Fusion (RRF).
        """
        t0 = time.perf_counter()
        records = self.list_memories(bank_id=bank_id)
        if not records:
            return RecallResult(
                bank_id=bank_id,
                query=query,
                results=[],
                strategy_breakdown={"total_candidates": 0},
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                backend_mode=self.backend_mode,
            )

        query_tokens = self._tokenize(query)
        query_expanded = self._expand_semantic_tokens(query_tokens)
        query_entities = set(self.extract_entities(query))

        # Build corpus stats for BM25
        doc_tokens_list: List[List[str]] = [self._tokenize(r.text) for r in records]
        n_docs = len(records)
        avgdl = sum(len(dt) for dt in doc_tokens_list) / max(n_docs, 1)

        df: Dict[str, int] = {}
        for dt in doc_tokens_list:
            for tok in set(dt):
                df[tok] = df.get(tok, 0) + 1

        # Build 1-hop entity adjacency graph for Graph Traversal
        adjacency: Dict[str, Set[str]] = {}
        for r in records:
            ents = [e.lower() for e in r.entities]
            for e1 in ents:
                adjacency.setdefault(e1, set()).update(ents)

        query_ents_lower = {e.lower() for e in query_entities}
        two_hop_entities: Set[str] = set()
        for qe in query_ents_lower:
            two_hop_entities.update(adjacency.get(qe, set()))

        k1 = 1.5
        b = 0.75

        for idx, rec in enumerate(records):
            doc_tokens = doc_tokens_list[idx]
            doc_token_set = set(doc_tokens)
            doc_expanded = self._expand_semantic_tokens(doc_tokens)

            # Strategy 1: Semantic Similarity (Concept-expanded cosine overlap)
            if query_expanded and doc_expanded:
                overlap = len(query_expanded.intersection(doc_expanded))
                rec.semantic_score = round(
                    overlap / math.sqrt(len(query_expanded) * len(doc_expanded)), 4
                )
            else:
                rec.semantic_score = 0.0

            # Strategy 2: BM25 Exact Keyword Score
            bm25_raw = 0.0
            doc_len = len(doc_tokens)
            for q_tok in query_tokens:
                if q_tok in doc_token_set:
                    tf = doc_tokens.count(q_tok)
                    doc_freq = df.get(q_tok, 0)
                    idf = math.log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
                    denom = tf + k1 * (1.0 - b + b * (doc_len / max(avgdl, 1.0)))
                    bm25_raw += idf * ((tf * (k1 + 1.0)) / max(denom, 1e-6))
            rec.bm25_score = round(min(bm25_raw / 8.0, 1.0), 4)

            # Strategy 3: Entity Knowledge Graph Traversal (Direct + 2-hop relational link)
            rec_ents_lower = {e.lower() for e in rec.entities}
            direct_hits = len(query_ents_lower.intersection(rec_ents_lower))
            hop_hits = len(two_hop_entities.intersection(rec_ents_lower))
            if query_ents_lower:
                graph_val = (direct_hits * 0.75 + hop_hits * 0.25) / max(len(query_ents_lower), 1)
                rec.graph_score = round(min(graph_val, 1.0), 4)
            else:
                rec.graph_score = round(min(len(rec_ents_lower) * 0.15, 0.6), 4)

            # Strategy 4: Temporal Reasoning (Exponential decay + observation permanence)
            # Recent incidents & synthesized observations receive higher temporal relevance
            decay = math.exp(-0.025 * max(rec.days_ago, 0.0))
            if rec.memory_type == "observation":
                decay = min(1.0, decay * 1.15)
            rec.temporal_score = round(decay, 4)

        # Rank each strategy independently and fuse via Reciprocal Rank Fusion (RRF)
        rrf_k = 10.0
        weights = {
            "semantic": 0.30,
            "bm25": 0.30,
            "graph": 0.25,
            "temporal": 0.15,
        }

        sem_ranked = sorted(records, key=lambda r: r.semantic_score, reverse=True)
        bm25_ranked = sorted(records, key=lambda r: r.bm25_score, reverse=True)
        graph_ranked = sorted(records, key=lambda r: r.graph_score, reverse=True)
        temp_ranked = sorted(records, key=lambda r: r.temporal_score, reverse=True)

        sem_rank_map = {r.memory_id: i + 1 for i, r in enumerate(sem_ranked)}
        bm25_rank_map = {r.memory_id: i + 1 for i, r in enumerate(bm25_ranked)}
        graph_rank_map = {r.memory_id: i + 1 for i, r in enumerate(graph_ranked)}
        temp_rank_map = {r.memory_id: i + 1 for i, r in enumerate(temp_ranked)}

        for rec in records:
            mid = rec.memory_id
            rrf = (
                weights["semantic"] / (rrf_k + sem_rank_map[mid])
                + weights["bm25"] / (rrf_k + bm25_rank_map[mid])
                + weights["graph"] / (rrf_k + graph_rank_map[mid])
                + weights["temporal"] / (rrf_k + temp_rank_map[mid])
            )
            # Normalize RRF to an intuitive 0..1 confidence score
            max_possible_rrf = sum(weights.values()) / (rrf_k + 1.0)
            rec.rrf_score = round(min(rrf / max_possible_rrf, 1.0), 4)

        fused = sorted(
            records,
            key=lambda r: (
                r.rrf_score,
                r.bm25_score + r.semantic_score + r.graph_score,
            ),
            reverse=True,
        )[:top_k]

        latency_ms = (time.perf_counter() - t0) * 1000.0
        breakdown = {
            "total_candidates": n_docs,
            "query_entities_detected": sorted(query_entities),
            "two_hop_graph_entities": sorted(two_hop_entities),
            "weights": weights,
            "top_semantic_id": sem_ranked[0].memory_id if sem_ranked else None,
            "top_bm25_id": bm25_ranked[0].memory_id if bm25_ranked else None,
            "top_graph_id": graph_ranked[0].memory_id if graph_ranked else None,
        }

        return RecallResult(
            bank_id=bank_id,
            query=query,
            results=fused,
            strategy_breakdown=breakdown,
            latency_ms=latency_ms,
            backend_mode=self.backend_mode,
        )

    # ------------------------------------------------------------------
    # 3. REFLECT: Higher-Order Agentic Reasoning Across Memory Tiers
    # ------------------------------------------------------------------
    def reflect(
        self,
        bank_id: str,
        query: str,
        persist_observation: bool = False,
    ) -> ReflectResult:
        """
        Hindsight `reflect(bank_id, query)` operation.

        Synthesizes structured `world` facts and `experience` memories into higher-order
        operational `observation` invariants, surfacing recurring failure chains and
        runbook anti-patterns across time.
        """
        t0 = time.perf_counter()
        recall_res = self.recall(bank_id=bank_id, query=query, top_k=6)
        supporting = recall_res.results

        world_facts = [m for m in supporting if m.memory_type == "world"]
        experiences = [m for m in supporting if m.memory_type == "experience"]
        observations = [m for m in supporting if m.memory_type == "observation"]
        antipatterns = [
            m for m in supporting if m.metadata.get("has_antipattern_warning")
        ]

        synthesized_points: List[str] = []

        if antipatterns:
            for ap in antipatterns[:2]:
                synthesized_points.append(
                    f"CRITICAL RUNBOOK GUARDRAIL (from {ap.memory_id}): {ap.text}"
                )

        if experiences:
            inc_ids = []
            for exp in experiences:
                for ent in exp.entities:
                    if ent.startswith("INC-") and ent not in inc_ids:
                        inc_ids.append(ent)
            inc_str = ", ".join(inc_ids) if inc_ids else "prior operational history"
            synthesized_points.append(
                f"Historical Correlation ({inc_str}): {experiences[0].text}"
            )

        if world_facts:
            synthesized_points.append(
                f"Topology Dependency Context: {world_facts[0].text}"
            )

        if observations:
            synthesized_points.append(
                f"Established Team Invariant: {observations[0].text}"
            )

        if not synthesized_points:
            reflection_text = (
                "No prior historical incidents matched in memory bank. "
                "Operating in cold-start discovery mode; retain the post-mortem after resolution."
            )
        else:
            reflection_text = (
                "Hindsight Cross-Incident Synthesis:\n"
                + "\n".join(f"• {pt}" for pt in synthesized_points)
            )

        if persist_observation and synthesized_points:
            obs_summary = (
                f"Synthesized operational invariant for '{query[:60]}': "
                f"{synthesized_points[0]}"
            )
            self.retain(
                bank_id=bank_id,
                content=obs_summary,
                memory_type="observation",
                metadata={"generated_by": "hindsight_reflect"},
                days_ago=0.0,
            )

        latency_ms = (time.perf_counter() - t0) * 1000.0
        return ReflectResult(
            bank_id=bank_id,
            query=query,
            text=reflection_text,
            synthesized_observations=synthesized_points,
            supporting_memories=supporting,
            latency_ms=latency_ms,
            backend_mode=self.backend_mode,
        )

    # ------------------------------------------------------------------
    # Inspection & Knowledge Graph Utilities for UI Visualization
    # ------------------------------------------------------------------
    def list_memories(
        self,
        bank_id: str,
        memory_type: Optional[str] = None,
    ) -> List[MemoryRecord]:
        with self._get_conn() as conn:
            if memory_type:
                rows = conn.execute(
                    """
                    SELECT * FROM hindsight_memories
                    WHERE bank_id = ? AND memory_type = ?
                    ORDER BY days_ago ASC, memory_id DESC
                    """,
                    (bank_id, memory_type),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM hindsight_memories
                    WHERE bank_id = ?
                    ORDER BY days_ago ASC, memory_id DESC
                    """,
                    (bank_id,),
                ).fetchall()

        result: List[MemoryRecord] = []
        for row in rows:
            result.append(
                MemoryRecord(
                    memory_id=row["memory_id"],
                    bank_id=row["bank_id"],
                    memory_type=row["memory_type"],
                    text=row["text"],
                    entities=json.loads(row["entities_json"]),
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                    days_ago=float(row["days_ago"]),
                )
            )
        return result

    def clear_bank(self, bank_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM hindsight_memories WHERE bank_id = ?", (bank_id,))
            conn.commit()

    def get_knowledge_graph(self, bank_id: str) -> Dict[str, Any]:
        """Builds nodes and edges from retained memory entities for the interactive graph UI."""
        memories = self.list_memories(bank_id=bank_id)
        node_counts: Dict[str, int] = {}
        node_types: Dict[str, str] = {}
        edges_map: Dict[Tuple[str, str], int] = {}

        for mem in memories:
            ents = mem.entities
            for ent in ents:
                node_counts[ent] = node_counts.get(ent, 0) + 1
                if ent.startswith("INC-"):
                    node_types[ent] = "incident"
                elif ent.isupper() or ent.startswith("0x"):
                    node_types[ent] = "error_or_flag"
                else:
                    node_types[ent] = "service"

            for i in range(len(ents)):
                for j in range(i + 1, len(ents)):
                    pair = tuple(sorted((ents[i], ents[j])))
                    edges_map[pair] = edges_map.get(pair, 0) + 1

        nodes = [
            {"id": ent, "label": ent, "group": node_types.get(ent, "service"), "weight": cnt}
            for ent, cnt in node_counts.items()
        ]
        edges = [
            {"source": src, "target": dst, "weight": weight}
            for (src, dst), weight in edges_map.items()
        ]
        return {
            "bank_id": bank_id,
            "total_memories": len(memories),
            "counts_by_type": {
                "world": sum(1 for m in memories if m.memory_type == "world"),
                "experience": sum(1 for m in memories if m.memory_type == "experience"),
                "observation": sum(1 for m in memories if m.memory_type == "observation"),
            },
            "nodes": nodes,
            "edges": edges,
        }

