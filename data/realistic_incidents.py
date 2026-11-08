"""
Realistic Production SRE Dataset for AegisSRE.

Models a high-throughput financial checkout & payments platform (`prod-payments-cluster`)
with interconnected microservices (`checkout-service`, `payment-gateway-grpc`,
`redis-cart-cluster`, `auth-middleware`, `pg-ledger-primary`, `kong-edge-ingress`).
"""

from typing import Any, Dict, List

DEFAULT_BANK_ID = "prod-payments-cluster"

INITIAL_MEMORIES: List[Dict[str, Any]] = [
    # --- WORLD FACTS (Static Infrastructure Topology & Ownership) ---
    {
        "memory_id": "mem-world-0001",
        "memory_type": "world",
        "days_ago": 45.0,
        "text": (
            "[Topology] checkout-service communicates synchronously with payment-gateway-grpc "
            "over Envoy sidecar (timeout=1200ms) and validates JWT sessions via auth-middleware. "
            "Primary state is stored in pg-ledger-primary fronted by pg_bouncer (max_client_conn=400)."
        ),
        "metadata": {"owner": "@priya-sre", "tier": "tier-0-critical"},
    },
    {
        "memory_id": "mem-world-0002",
        "memory_type": "world",
        "days_ago": 40.0,
        "text": (
            "[Topology] redis-cart-cluster runs Redis 7.2 with allkeys-lru eviction policy and "
            "shares VPC subnet with checkout-service and inventory-worker. Ingress traffic is "
            "routed through kong-edge-ingress with dynamic rate-limiting plugins."
        ),
        "metadata": {"owner": "@rohan-infra", "tier": "tier-0-critical"},
    },
    # --- EXPERIENCE FACTS (Historical Post-Mortems & Runbook Anti-Patterns) ---
    {
        "memory_id": "mem-exp-0003",
        "memory_type": "experience",
        "days_ago": 19.0,
        "text": (
            "[Post-Mortem INC-4082] Symptom: checkout-service threw 0x8F4A_POOL_EXHAUSTED "
            "and pg_bouncer saturation (400/400 connections) after deploy v2.14.3. "
            "FAILED RUNBOOK ANTI-PATTERN: DO NOT increase pg_bouncer max_client_conn or run "
            "`kubectl rollout restart deployment/checkout-service`—doing so triggered a retry storm "
            "that OOM-killed pg-ledger-primary for 42 minutes! "
            "TRUE ROOT CAUSE: Feature flag FF_ASYNC_TOKEN_REFRESH=true in auth-middleware leaked "
            "unclosed DB transactions on token timeout. "
            "VERIFIED FIX: Run `feature-cli set FF_ASYNC_TOKEN_REFRESH=false --cluster prod-payments` "
            "and drain idle pg_bouncer connections via `psql -c 'KILL IDLE_IN_TRANSACTION'`."
        ),
        "metadata": {
            "incident_id": "INC-4082",
            "mttr_minutes": 42,
            "has_antipattern_warning": True,
        },
    },
    {
        "memory_id": "mem-exp-0004",
        "memory_type": "experience",
        "days_ago": 11.0,
        "text": (
            "[Post-Mortem INC-4119] Symptom: payment-gateway-grpc spiked to 94% GRPC_DEADLINE_EXCEEDED "
            "during flash-sale traffic when redis-cart-cluster eviction rate crossed 450 keys/sec. "
            "FAILED RUNBOOK ANTI-PATTERN: Never flush redis-cart-cluster or restart envoy-sidecar "
            "during active flash traffic. "
            "TRUE ROOT CAUSE: envoy-sidecar circuit-breaker max_pending_requests was capped at 64, "
            "dropping internal health probes whenever Redis latency exceeded 18ms. "
            "VERIFIED FIX: Throttle kong-edge-ingress rate limit to 65% for 180s (`kong-ctl throttle "
            "--route /v2/checkout --rate 65%`) while bumping envoy max_pending_requests to 512."
        ),
        "metadata": {
            "incident_id": "INC-4119",
            "mttr_minutes": 18,
            "has_antipattern_warning": True,
        },
    },
    {
        "memory_id": "mem-exp-0005",
        "memory_type": "experience",
        "days_ago": 4.0,
        "text": (
            "[Post-Mortem INC-4205] Symptom: inventory-worker consumer lag exceeded 140,000 messages "
            "with deadlock error 0x9C12_LOCK_TIMEOUT on pg-ledger-primary. "
            "TRUE ROOT CAUSE: Concurrent batch reconciliation job running without `SKIP LOCKED` "
            "during peak European checkout window (14:00-16:00 UTC). "
            "VERIFIED FIX: Pause batch cron (`kubectl suspend cronjob/ledger-reconcile`) and set "
            "worker concurrency to 8 (`helm upgrade inventory-worker --set replicaConcurrency=8`)."
        ),
        "metadata": {
            "incident_id": "INC-4205",
            "mttr_minutes": 9,
            "has_antipattern_warning": False,
        },
    },
    # --- OBSERVATION FACTS (Synthesized Cross-Incident Engineering Laws) ---
    {
        "memory_id": "mem-obs-0006",
        "memory_type": "observation",
        "days_ago": 2.0,
        "text": (
            "[Synthesized Invariant] Observation across INC-4082 and INC-4119: Whenever checkout-service "
            "exhibits connection pool exhaustion (0x8F4A_POOL_EXHAUSTED) alongside elevated auth-middleware "
            "latency, pod restarts and pool-size increases amplify retry storms against pg-ledger-primary. "
            "Always inspect auth-middleware async token flags and throttle kong-edge-ingress first."
        ),
        "metadata": {"generated_by": "hindsight_reflect", "confidence": 0.98},
    },
]

INTERACTION_SCENARIOS: Dict[str, Dict[str, Any]] = {
    "pool_exhaustion": {
        "id": "pool_exhaustion",
        "title": "P1 Outage: checkout-service 0x8F4A_POOL_EXHAUSTED & pg_bouncer Saturation",
        "severity": "SEV-1 ( $14,200 / min revenue impact )",
        "service": "checkout-service",
        "InteractionStage": "Interaction 5 (Day 14 - Experience Recall)",
        "alert_payload": (
            "ALERT [PagerDuty SEV-1]: checkout-service error rate 68.4% in prod-payments-cluster.\n"
            "Error Signature: Fatal 0x8F4A_POOL_EXHAUSTED: pg_bouncer max_client_conn (400/400) reached "
            "waiting for pg-ledger-primary.\n"
            "Recent Change: Commit 9f3c21a enabled FF_ASYNC_TOKEN_REFRESH in auth-middleware v2.18.0 (22 mins ago).\n"
            "Telemetry: 248 connections stuck in 'idle in transaction' state; p99 checkout latency 4,820ms."
        ),
        "stateless_response": {
            "diagnosis": (
                "Database connection pool exhaustion detected in `checkout-service` (pg_bouncer 400/400). "
                "The application is running out of available PostgreSQL connections under high load."
            ),
            "recommended_commands": [
                "helm upgrade pg-bouncer --set max_client_conn=1200",
                "kubectl rollout restart deployment/checkout-service -n prod-payments",
            ],
            "risk_outcome": (
                "CATASTROPHIC FAILURE: Increasing pg_bouncer pool to 1200 and restarting checkout-service "
                "pods triggers an immediate connection retry storm, OOM-killing pg-ledger-primary "
                "(repeating the exact 42-minute outage from INC-4082!)."
            ),
            "estimated_mttr": "47 minutes (after secondary DB crash)",
        },
        "hindsight_response": {
            "diagnosis": (
                "Recalled INC-4082 (19 days ago via TEMPR BM25 + Entity Graph): `0x8F4A_POOL_EXHAUSTED` with "
                "`idle in transaction` connections is NOT a capacity shortage—it is caused by leaked "
                "transactions when `FF_ASYNC_TOKEN_REFRESH=true` in `auth-middleware`."
            ),
            "blocked_dangerous_commands": [
                "BLOCKED `kubectl rollout restart deployment/checkout-service` (Caused 42m OOM outage in INC-4082)",
                "BLOCKED `pg_bouncer max_client_conn` increase (Amplifies retry storm against pg-ledger-primary)",
            ],
            "verified_remediation_commands": [
                "feature-cli set FF_ASYNC_TOKEN_REFRESH=false --cluster prod-payments-cluster",
                "psql -h pg-ledger-primary -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND state_change < now() - interval '30s';\"",
            ],
            "risk_outcome": (
                "SAFE & VERIFIED RESOLUTION: Leaked transaction loop halted in 14 seconds without pod restarts. "
                "Connection pool utilization dropped from 100% to 18% within 45 seconds."
            ),
            "estimated_mttr": "1.2 minutes (97% faster)",
        },
    },
    "grpc_cascade": {
        "id": "grpc_cascade",
        "title": "P1 Flash Sale Cascade: payment-gateway-grpc GRPC_DEADLINE_EXCEEDED Spike",
        "severity": "SEV-1 ( $19,500 / min flash-sale impact )",
        "service": "payment-gateway-grpc",
        "InteractionStage": "Interaction 12 (Day 28 - Graph + Temporal Fusion)",
        "alert_payload": (
            "ALERT [Prometheus Critical]: payment-gateway-grpc returning 91% GRPC_DEADLINE_EXCEEDED.\n"
            "Upstream Telemetry: redis-cart-cluster eviction rate spiked to 520 keys/sec; envoy-sidecar "
            "upstream_rq_pending_overflow counter incrementing at 340/sec.\n"
            "On-Call Question: Should we restart envoy-sidecar and flush redis-cart-cluster?"
        ),
        "stateless_response": {
            "diagnosis": (
                "gRPC deadline exceeded errors between `checkout-service` and `payment-gateway-grpc` "
                "accompanied by high Redis memory pressure."
            ),
            "recommended_commands": [
                "redis-cli -h redis-cart-cluster FLUSHALL ASYNC",
                "kubectl rollout restart daemonset/envoy-sidecar -n prod-payments",
            ],
            "risk_outcome": (
                "SEVERE OUTAGE: Flushing `redis-cart-cluster` wipes 85,000 active customer shopping carts "
                "and restarting `envoy-sidecar` drops all in-flight payment authorizations!"
            ),
            "estimated_mttr": "38 minutes + lost active carts",
        },
        "hindsight_response": {
            "diagnosis": (
                "Recalled INC-4119 via Entity Graph (`payment-gateway-grpc` <-> `envoy-sidecar` <-> "
                "`redis-cart-cluster`): `upstream_rq_pending_overflow` occurs because Envoy's "
                "`max_pending_requests=64` trips when Redis eviction latency exceeds 18ms."
            ),
            "blocked_dangerous_commands": [
                "BLOCKED `redis-cli FLUSHALL` (Destroys active customer carts; prohibited in INC-4119)",
                "BLOCKED `envoy-sidecar` restart (Drops in-flight payment settlements)",
            ],
            "verified_remediation_commands": [
                "kong-ctl throttle --route /v2/checkout --rate 65% --duration 180s",
                "envoy-admin-cli runtime-modifycircuit_breakers.default.max_pending_requests=512",
            ],
            "risk_outcome": (
                "ZERO DATA LOSS: Ingress traffic shaped cleanly for 180s while Envoy queue threshold "
                "expanded dynamically. All active carts and payment sessions preserved."
            ),
            "estimated_mttr": "52 seconds",
        },
    },
    "pre_deploy_shield": {
        "id": "pre_deploy_shield",
        "title": "Proactive CI/CD Gate: PR #1842 Pre-Deployment Risk Analysis",
        "severity": "PRE-DEPLOY AUDIT ( Prevents outage before merge )",
        "service": "auth-middleware",
        "InteractionStage": "Interaction 20 (Day 45 - Reflective Mastery)",
        "alert_payload": (
            "CI/CD PIPELINE GATE [PR #1842 by @dev-alex]: Preparing canary deploy for `auth-middleware v2.21.0` "
            "and `checkout-service`.\n"
            "Git Diff Summary:\n"
            "  + auth-middleware/config.yaml: enable_async_jwt_revalidation: true\n"
            "  + checkout-service/db.yaml: pg_bouncer_pool_size: 800\n"
            "  + cron/ledger_reconcile.sql: schedule set to '0 15 * * *' (15:00 UTC without SKIP LOCKED)"
        ),
        "stateless_response": {
            "diagnosis": (
                "Code diff looks syntactically valid. YAML configuration keys are properly formatted "
                "and increasing connection pool size to 800 improves concurrency. LGTM to merge."
            ),
            "recommended_commands": [
                "gh pr merge 1842 --auto --squash",
                "argocd app sync prod-payments-cluster",
            ],
            "risk_outcome": (
                "DOUBLE PRODUCTION OUTAGE: Merging PR #1842 simultaneously re-introduces the INC-4082 "
                "async token DB leak AND schedules the INC-4205 lock-contention job during peak EU traffic!"
            ),
            "estimated_mttr": "Preventable 60+ min outage shipped to prod",
        },
        "hindsight_response": {
            "diagnosis": (
                "Hindsight `reflect()` synthesized 3 historical post-mortems (`INC-4082`, `INC-4119`, `INC-4205`) "
                "and flagged TWO lethal production regressions hidden in PR #1842 before deployment!"
            ),
            "blocked_dangerous_commands": [
                "BLOCKED Canary Deploy of PR #1842 (`enable_async_jwt_revalidation: true` matches INC-4082 leak pattern)",
                "BLOCKED `ledger_reconcile.sql` at 15:00 UTC without `SKIP LOCKED` (Violates INC-4205 peak-window invariant)",
            ],
            "verified_remediation_commands": [
                "gh pr review 1842 --request-changes -b 'Blocked by AegisSRE Hindsight Memory: Violates INC-4082 & INC-4205 invariants'",
                "sed -i 's/FOR UPDATE/FOR UPDATE SKIP LOCKED/g' cron/ledger_reconcile.sql",
            ],
            "risk_outcome": (
                "OUTAGE PREVENTED PRE-MERGE: Caught two historical failure modes in CI/CD before a single "
                "production packet was affected. MTTR = 0 seconds."
            ),
            "estimated_mttr": "0 seconds (Prevented before deploy)",
        },
    },
}

LEARNING_CURVE_STAGES: List[Dict[str, Any]] = [
    {
        "interaction": 1,
        "day": "Day 1",
        "label": "Cold Start (0 Memories)",
        "memories_count": 2,
        "mttr_minutes": 47.0,
        "accuracy_pct": 35,
        "summary": (
            "Agent has only static topology facts. Suggests generic textbook fixes; "
            "engineer manually resolves INC-4082 and calls `hindsight.retain()` with the post-mortem."
        ),
    },
    {
        "interaction": 5,
        "day": "Day 14",
        "label": "Experience Recall (6 Memories)",
        "memories_count": 6,
        "mttr_minutes": 1.2,
        "accuracy_pct": 94,
        "summary": (
            "When `0x8F4A_POOL_EXHAUSTED` recurs, `hindsight.recall()` retrieves INC-4082 via BM25 + "
            "Entity Graph, blocks the dangerous pod restart, and outputs the exact feature-flag fix."
        ),
    },
    {
        "interaction": 20,
        "day": "Day 45",
        "label": "Reflective Mastery (24 Memories)",
        "memories_count": 24,
        "mttr_minutes": 0.7,
        "accuracy_pct": 99,
        "summary": (
            "Using `hindsight.reflect()`, the agent synthesizes cross-incident invariants across "
            "`checkout-service`, `auth-middleware`, and `pg-ledger-primary`, blocking risky PRs before deploy."
        ),
    },
]

