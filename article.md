# A stateless agent nearly OOM-killed our Postgres primary; Hindsight fixed it

At 3:14 AM during a payment checkout outage, a stateless incident triage bot looked at a saturated `pg_bouncer` connection pool (`400/400` active connections) and recommended the most obvious textbook fix in the world: double `max_client_conn` and run `kubectl rollout restart deployment/checkout-service`. When we ran that exact sequence nineteen days earlier during `INC-4082`, the simultaneous pod restarts triggered a connection retry storm that OOM-killed `pg-ledger-primary` and turned a localized degradation into a 42-minute total outage.

Stateless LLMs do not have operational scars. Every time PagerDuty fires, a stateless model wakes up with complete amnesia, treats your production cluster like a generic tutorial environment, and cheerfully recommends runbooks that already failed two weeks ago.

To solve this permanently, we built **AegisSRE**—an autonomous incident commander and pre-deployment risk gate backed by [Hindsight open-source agent memory](https://github.com/vectorize-io/hindsight) and high-speed Groq inference (`openai/gpt-oss-120b` and `qwen/qwen3-32b`). Instead of stuffing raw Slack logs into a context window, AegisSRE retains structured post-mortems, recalls exact failure chains across microservices, actively blocks runbook anti-patterns that previously caused secondary damage, and reflects across past outages to stop risky pull requests before they merge.

![AegisSRE x Hindsight Biomimetic Memory Architecture](static/slides/slide_2_architecture.jpg)

---

## How AegisSRE Hangs Together

Most engineering teams who try to build an incident bot start with standard vector RAG over Markdown post-mortems. We tried that first, and it failed in two predictable ways:

1. **Dense embeddings blur exact operational tokens.** Cosine similarity over text chunks treats `0x8F4A_POOL_EXHAUSTED` in `checkout-service` and `0x9C12_LOCK_TIMEOUT` in `inventory-worker` as nearly identical "database connection errors," returning the wrong runbook at the worst possible moment.
2. **Vector stores do not distinguish static topology from Negative Experience.** In site reliability engineering, knowing *what not to run* during a cascading failure is far more important than knowing what a service does on paper.

When we looked closely at [what agent memory actually requires in production](https://vectorize.io/what-is-agent-memory), we realized we needed a biomimetic memory layer that separates static architectural facts from lived operational experience and periodically consolidates them into team-wide engineering invariants.

AegisSRE is structured around three tight components:

* **Biomimetic Memory Engine (`hindsight_memory/engine.py`)**: Wraps the official Hindsight SDK (`hindsight-client`) and organizes every cluster into an isolated memory bank (`bank_id="prod-payments-cluster"`). Every ingested artifact is classified into one of three tiers:
  * `world`: Static service topology, Envoy sidecar timeouts, database pool limits, and team ownership.
  * `experience`: Specific post-mortems (`INC-4082`, `INC-4119`, `INC-4205`), verified remediation commands, and explicit **failed runbook anti-patterns**.
  * `observation`: Cross-incident operational invariants synthesized via `hindsight.reflect()`.
* **Resilient Groq Tool Orchestrator (`agent/groq_client.py`)**: Runs low-latency inference on Groq using `openai/gpt-oss-120b` and `qwen/qwen3-32b`, protected by `GroqToolCallGuard`—a schema-repair middleware we built after discovering how often open-weight reasoning models emit `<think>` blocks or trailing commas inside tool-call JSON payloads.
* **Incident War-Room & Pre-Deploy Gate (`agent/aegis_sre.py` & `server.py`)**: Executes parallel triage comparing a stateless baseline against Hindsight-grounded analysis, exposing both a real-time web mission control dashboard and a CI/CD webhook gate.

---

## The Core Technical Story: Why 4-Way TEMPR Beats Vector RAG at 3 AM

The defining architectural shift in AegisSRE came from adopting the multi-strategy retrieval pipeline documented in the [Hindsight technical documentation](https://hindsight.vectorize.io/). When an alert hits AegisSRE, `recall()` does not just run a single embedding lookup. It executes **TEMPR**—four parallel retrieval strategies fused via Reciprocal Rank Fusion (RRF):

1. **Semantic Similarity**: Captures conceptual phrasing ("database latency spike during checkout").
2. **BM25 Keyword Matching**: Locks onto exact high-entropy tokens like hex error codes (`0x8F4A_POOL_EXHAUSTED`), feature flags (`FF_ASYNC_TOKEN_REFRESH`), and commit SHAs (`9f3c21a`).
3. **Entity Knowledge Graph Traversal**: Walks 1-hop and 2-hop links across shared infrastructure (`checkout-service` $\leftrightarrow$ `auth-middleware` $\leftrightarrow$ `pg-ledger-primary`) so an alert on a downstream proxy still retrieves upstream root causes.
4. **Temporal Decay Reasoning**: Applies exponential recency weighting while boosting permanent `observation` invariants so yesterday's canary deployment outweighs a deprecated configuration from six months ago.

![Inside Hindsight TEMPR: 4-Way Parallel Retrieval](static/slides/slide_3_tempr.jpg)

Here is how we wired `retain()` and the 4-way RRF `recall()` pipeline in `hindsight_memory/engine.py`:

```python
# From hindsight_memory/engine.py
def retain(
    self,
    bank_id: str,
    content: str,
    memory_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    days_ago: float = 0.0,
    memory_id: Optional[str] = None,
) -> MemoryRecord:
    clean_text = content.strip()
    resolved_type = self.classify_memory_type(clean_text, memory_type)
    entities = self.extract_entities(clean_text)
    meta = dict(metadata or {})

    # Flag negative operational experiences (failed runbooks that worsened outages)
    if any(p in clean_text.lower() for p in ("do not ", "never ", "failed runbook", "caused oom")):
        meta["has_antipattern_warning"] = True

    if self._sdk_client is not None:
        self._sdk_client.retain(bank_id=bank_id, content=clean_text)
```

When `recall()` executes during an active P1 incident, we rank candidates across all four strategies independently and fuse them with Reciprocal Rank Fusion:

```python
# From hindsight_memory/engine.py
rrf_k = 10.0
weights = {"semantic": 0.30, "bm25": 0.30, "graph": 0.25, "temporal": 0.15}

for rec in records:
    mid = rec.memory_id
    rrf = (
        weights["semantic"] / (rrf_k + sem_rank_map[mid])
        + weights["bm25"] / (rrf_k + bm25_rank_map[mid])
        + weights["graph"] / (rrf_k + graph_rank_map[mid])
        + weights["temporal"] / (rrf_k + temp_rank_map[mid])
    )
    max_possible_rrf = sum(weights.values()) / (rrf_k + 1.0)
    rec.rrf_score = round(min(rrf / max_possible_rrf, 1.0), 4)
```

### Surviving Function-Calling Failures in Open-Weight Models

Speed matters during an outage, which is why we paired Hindsight with Groq running `openai/gpt-oss-120b` and `qwen/qwen3-32b`. However, early in testing (while iterating rapidly on our repo structure with `Code.in`), we hit a painful edge case: under high-token stack trace prompts, `qwen/qwen3-32b` occasionally prepended `<think>...</think>` blocks inside tool arguments or left trailing commas, while `openai/gpt-oss-120b` sometimes triggered Groq HTTP 400 `tool_use_failed` errors when unescaped quotes appeared in SQL remediation strings.

Instead of letting a malformed tool call crash an active incident triage, we built `GroqToolCallGuard` in `agent/groq_client.py`:

```python
# From agent/groq_client.py
if "<think>" in cleaned:
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    issues.append("stripped_qwen_think_tags")

if re.search(r",\s*[}\]]", cleaned):
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    issues.append("removed_trailing_commas")

if cleaned.startswith("{") and not cleaned.endswith("}"):
    if cleaned.count('"') % 2 == 1:
        cleaned += '"'
    cleaned += "}"
    issues.append("closed_truncated_json_brace")
```

If Groq returns an HTTP 400 `tool_use_failed` exception, `GroqToolCallGuard.recover_from_failed_generation()` extracts `error.failed_generation`, sanitizes the payload, coerces stringified integers (`"top_k": "5"` $\rightarrow$ `5`), and executes the Hindsight memory call without dropping the request.

---

## Concrete Behavior: Before vs. After Hindsight

To see why persistent memory changes the operational equation, look at what happens when `checkout-service` throws `0x8F4A_POOL_EXHAUSTED` with 248 connections stuck in `idle in transaction`:

### Without Hindsight (Stateless Agent)
* **Diagnosis**: *"Database connection pool exhaustion detected in `checkout-service` (`pg_bouncer 400/400`). Increase pool capacity and restart application pods."*
* **Suggested Commands**:
  ```bash
  helm upgrade pg-bouncer --set max_client_conn=1200
  kubectl rollout restart deployment/checkout-service -n prod-payments
  ```
* **Result**: **Catastrophic secondary outage.** Restarting the pods while `auth-middleware` is leaking transactions triggers a thundering herd that OOM-kills `pg-ledger-primary` for 42+ minutes.

### With Hindsight (AegisSRE after `retain` + `recall` + `reflect`)
* **TEMPR Recall Hit (`RRF=0.943`, `1.8ms`)**: Retrieves `mem-exp-0003` (`INC-4082`, 19 days ago) via BM25 exact match on `0x8F4A_POOL_EXHAUSTED` and Entity Graph traversal linking `checkout-service` $\rightarrow$ `auth-middleware` $\rightarrow$ `FF_ASYNC_TOKEN_REFRESH`.
* **Blocked Runbook Anti-Patterns**:
  * `BLOCKED: kubectl rollout restart deployment/checkout-service (Caused 42m OOM outage in INC-4082)`
  * `BLOCKED: pg_bouncer max_client_conn increase (Amplifies retry storm against pg-ledger-primary)`
* **Verified Remediation Commands**:
  ```bash
  feature-cli set FF_ASYNC_TOKEN_REFRESH=false --cluster prod-payments-cluster
  psql -h pg-ledger-primary -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction' AND state_change < now() - interval '30s';"
  ```
* **Result**: Leaked transaction loop halted in 14 seconds without a single pod restart. Mean Time To Resolution (MTTR) drops from **47 minutes to 1.2 minutes**.

![The Biomimetic Memory Learning Curve: Day 1 to Day 45](static/slides/slide_4_learning_curve.jpg)

Even better, by **Day 45 (Interaction 20)**, calling `hindsight.reflect()` across accumulated memories allows AegisSRE to act as a **Proactive CI/CD Pre-Deploy Gate**. When a developer opens `PR #1842` re-enabling async JWT revalidation alongside a 15:00 UTC reconciliation cron job without `SKIP LOCKED`, AegisSRE correlates two separate historical incidents (`INC-4082` and `INC-4205`) and blocks the canary deployment before a single production packet is affected.

---

## Lessons Learned Building with Hindsight

1. **Negative memory ("what failed") is twice as valuable as positive memory.** Storing only successful fixes leaves an agent vulnerable to suggesting intuitive but destructive commands before applying the fix. Tagging failed runbooks inside Hindsight `experience` memories gave us an immediate safety guardrail.
2. **Hybrid retrieval is mandatory for systems engineering.** Pure vector similarity failed whenever two different failure modes shared similar English descriptions. Combining BM25 exact token matching with 2-hop Entity Graph traversal inside Hindsight ensured that hex error codes and upstream service dependencies were never lost.
3. **Use `reflect()` to compress episodic noise into durable invariants.** Raw war-room logs are noisy. Running `reflect()` after every post-mortem distills dozens of Slack messages into a single high-signal `observation` record that stays hot in temporal scoring.
4. **Never trust raw tool-call JSON from fast reasoning models without a schema guard.** Models like `qwen/qwen3-32b` and `openai/gpt-oss-120b` are blisteringly fast on Groq, but wrapping tool execution in a deterministic JSON repair layer (`GroqToolCallGuard`) was essential to achieve zero-crash reliability under chaotic stack trace inputs.

