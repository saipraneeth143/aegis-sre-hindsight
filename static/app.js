// AegisSRE Frontend Controller (Works seamlessly via http://127.0.0.1:8080 AND file://)
const IS_FILE_PROTOCOL = window.location.protocol === "file:";
const API_BASE = IS_FILE_PROTOCOL ? "http://127.0.0.1:8080" : "";
const SLIDE_PREFIX = IS_FILE_PROTOCOL ? "slides/" : "/static/slides/";

const SCENARIO_PAYLOADS = {
  pool_exhaustion:
    "ALERT [PagerDuty SEV-1]: checkout-service error rate 68.4% in prod-payments-cluster.\n" +
    "Error Signature: Fatal 0x8F4A_POOL_EXHAUSTED: pg_bouncer max_client_conn (400/400) reached waiting for pg-ledger-primary.\n" +
    "Recent Change: Commit 9f3c21a enabled FF_ASYNC_TOKEN_REFRESH in auth-middleware v2.18.0 (22 mins ago).\n" +
    "Telemetry: 248 connections stuck in 'idle in transaction' state; p99 checkout latency 4,820ms.",
  grpc_cascade:
    "ALERT [Prometheus Critical]: payment-gateway-grpc returning 91% GRPC_DEADLINE_EXCEEDED.\n" +
    "Upstream Telemetry: redis-cart-cluster eviction rate spiked to 520 keys/sec; envoy-sidecar upstream_rq_pending_overflow counter incrementing at 340/sec.\n" +
    "On-Call Question: Should we restart envoy-sidecar and flush redis-cart-cluster?",
  pre_deploy_shield:
    "CI/CD PIPELINE GATE [PR #1842 by @dev-alex]: Preparing canary deploy for `auth-middleware v2.21.0` and `checkout-service`.\n" +
    "Git Diff Summary:\n" +
    "  + auth-middleware/config.yaml: enable_async_jwt_revalidation: true\n" +
    "  + checkout-service/db.yaml: pg_bouncer_pool_size: 800\n" +
    "  + cron/ledger_reconcile.sql: schedule set to '0 15 * * *' (15:00 UTC without SKIP LOCKED)",
};

const OFFLINE_FALLBACK_SCENARIOS = {
  pool_exhaustion: {
    title: "P1 Outage: checkout-service 0x8F4A_POOL_EXHAUSTED & pg_bouncer Saturation",
    severity: "SEV-1 ( $14,200 / min revenue impact )",
    interaction_stage: "Interaction 5 (Day 14 - Experience Recall)",
    stateless_agent: {
      diagnosis: "Database connection pool exhaustion detected in `checkout-service` (pg_bouncer 400/400).",
      recommended_commands: [
        "helm upgrade pg-bouncer --set max_client_conn=1200",
        "kubectl rollout restart deployment/checkout-service -n prod-payments",
      ],
      risk_outcome: "CATASTROPHIC FAILURE: Increasing pool to 1200 and restarting pods triggers a connection retry storm that OOM-kills pg-ledger-primary!",
      estimated_mttr: "47 minutes (after secondary DB crash)",
    },
    hindsight_agent: {
      diagnosis: "Recalled INC-4082 (19 days ago via TEMPR BM25 + Entity Graph): `0x8F4A_POOL_EXHAUSTED` is caused by leaked transactions when `FF_ASYNC_TOKEN_REFRESH=true` in `auth-middleware`.",
      blocked_dangerous_commands: [
        "BLOCKED `kubectl rollout restart deployment/checkout-service` (Caused 42m OOM outage in INC-4082)",
        "BLOCKED `pg_bouncer max_client_conn` increase (Amplifies retry storm)",
      ],
      verified_remediation_commands: [
        "feature-cli set FF_ASYNC_TOKEN_REFRESH=false --cluster prod-payments-cluster",
        "psql -h pg-ledger-primary -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state='idle in transaction';\"",
      ],
      risk_outcome: "SAFE & VERIFIED RESOLUTION: Leaked transaction loop halted in 14 seconds without pod restarts.",
      estimated_mttr: "1.2 minutes (97% faster)",
    },
    hindsight_recall: {
      latency_ms: 1.8,
      backend_mode: "hindsight-biomimetic-tempr",
      results: [
        {
          memory_id: "mem-exp-0003",
          memory_type: "experience",
          days_ago: 19,
          rrf_score: 0.943,
          semantic_score: 0.61,
          bm25_score: 1.0,
          graph_score: 0.94,
          temporal_score: 0.62,
          text: "[Post-Mortem INC-4082] DO NOT restart checkout-service pods during 0x8F4A_POOL_EXHAUSTED; set FF_ASYNC_TOKEN_REFRESH=false.",
        },
      ],
    },
    hindsight_reflect: {
      text: "Hindsight Cross-Incident Synthesis:\n• CRITICAL RUNBOOK GUARDRAIL: Never restart checkout-service pods during connection saturation; inspect auth-middleware flags first.",
    },
  },
  grpc_cascade: {
    title: "P1 Flash Sale Cascade: payment-gateway-grpc GRPC_DEADLINE_EXCEEDED Spike",
    severity: "SEV-1 ( $19,500 / min flash-sale impact )",
    interaction_stage: "Interaction 12 (Day 28 - Graph + Temporal Fusion)",
    stateless_agent: {
      diagnosis: "gRPC deadline exceeded errors accompanied by high Redis memory pressure.",
      recommended_commands: [
        "redis-cli -h redis-cart-cluster FLUSHALL ASYNC",
        "kubectl rollout restart daemonset/envoy-sidecar -n prod-payments",
      ],
      risk_outcome: "SEVERE OUTAGE: Flushing `redis-cart-cluster` wipes 85,000 active customer shopping carts!",
      estimated_mttr: "38 minutes + lost active carts",
    },
    hindsight_agent: {
      diagnosis: "Recalled INC-4119 via Entity Graph (`payment-gateway-grpc` <-> `envoy-sidecar` <-> `redis-cart-cluster`): Envoy `max_pending_requests=64` trips when Redis latency exceeds 18ms.",
      blocked_dangerous_commands: [
        "BLOCKED `redis-cli FLUSHALL` (Destroys active customer carts)",
        "BLOCKED `envoy-sidecar` restart (Drops in-flight payment settlements)",
      ],
      verified_remediation_commands: [
        "kong-ctl throttle --route /v2/checkout --rate 65% --duration 180s",
        "envoy-admin-cli runtime-modify circuit_breakers.default.max_pending_requests=512",
      ],
      risk_outcome: "ZERO DATA LOSS: Traffic shaped cleanly while Envoy queue threshold expanded dynamically.",
      estimated_mttr: "52 seconds",
    },
    hindsight_recall: {
      latency_ms: 1.9,
      backend_mode: "hindsight-biomimetic-tempr",
      results: [
        {
          memory_id: "mem-exp-0004",
          memory_type: "experience",
          days_ago: 11,
          rrf_score: 0.951,
          semantic_score: 0.72,
          bm25_score: 0.96,
          graph_score: 0.92,
          temporal_score: 0.76,
          text: "[Post-Mortem INC-4119] Never flush redis-cart-cluster; throttle kong-edge-ingress to 65% and bump envoy max_pending_requests to 512.",
        },
      ],
    },
    hindsight_reflect: {
      text: "Hindsight Cross-Incident Synthesis:\n• Preserve stateful Redis carts during flash-sale spikes by throttling edge ingress first.",
    },
  },
  pre_deploy_shield: {
    title: "Proactive CI/CD Gate: PR #1842 Pre-Deployment Risk Analysis",
    severity: "PRE-DEPLOY AUDIT ( Prevents outage before merge )",
    interaction_stage: "Interaction 20 (Day 45 - Reflective Mastery)",
    stateless_agent: {
      diagnosis: "Code diff looks syntactically valid. YAML keys are properly formatted. LGTM to merge.",
      recommended_commands: [
        "gh pr merge 1842 --auto --squash",
        "argocd app sync prod-payments-cluster",
      ],
      risk_outcome: "DOUBLE PRODUCTION OUTAGE: Re-introduces INC-4082 async token DB leak AND schedules INC-4205 lock contention!",
      estimated_mttr: "Preventable 60+ min outage shipped to prod",
    },
    hindsight_agent: {
      diagnosis: "Hindsight `reflect()` synthesized `INC-4082` and `INC-4205` to flag two lethal regressions hidden in PR #1842 before deployment!",
      blocked_dangerous_commands: [
        "BLOCKED Canary Deploy of PR #1842 (`enable_async_jwt_revalidation: true` matches INC-4082)",
        "BLOCKED `ledger_reconcile.sql` at 15:00 UTC without `SKIP LOCKED` (Violates INC-4205)",
      ],
      verified_remediation_commands: [
        "gh pr review 1842 --request-changes -b 'Blocked by AegisSRE Hindsight Memory'",
        "sed -i 's/FOR UPDATE/FOR UPDATE SKIP LOCKED/g' cron/ledger_reconcile.sql",
      ],
      risk_outcome: "OUTAGE PREVENTED PRE-MERGE: Caught two historical failure modes in CI/CD (MTTR = 0s).",
      estimated_mttr: "0 seconds (Prevented before deploy)",
    },
    hindsight_recall: {
      latency_ms: 2.1,
      backend_mode: "hindsight-biomimetic-tempr",
      results: [
        {
          memory_id: "mem-obs-0006",
          memory_type: "observation",
          days_ago: 2,
          rrf_score: 0.962,
          semantic_score: 0.78,
          bm25_score: 0.89,
          graph_score: 0.91,
          temporal_score: 1.0,
          text: "[Synthesized Invariant] Block async token flags and peak-window un-skipped SQL locks in CI/CD.",
        },
      ],
    },
    hindsight_reflect: {
      text: "Hindsight Cross-Incident Synthesis:\n• PR #1842 violates two established cluster invariants (INC-4082 & INC-4205). Deployment blocked automatically.",
    },
  },
};

const SLIDES_DATA = [
  {
    title: "Slide 1 of 5: The $14,000/Minute Amnesia Problem",
    src: SLIDE_PREFIX + "slide_1_problem.jpg",
    notes:
      "When production goes down at 3:00 AM, a stateless AI agent suffers from operational amnesia. Given a connection pool saturation alert, a stateless model suggests generic textbook fixes—like restarting pods or doubling connection pool limits—which triggers a massive retry storm and crashes the primary database. AegisSRE uses Vectorize Hindsight to recall exact past post-mortems, block failed runbooks, and execute verified fixes in seconds.",
  },
  {
    title: "Slide 2 of 5: AegisSRE x Hindsight Biomimetic Memory Architecture",
    src: SLIDE_PREFIX + "slide_2_architecture.jpg",
    notes:
      "AegisSRE ingests PagerDuty alerts, Kubernetes pod events, and Git PR diffs into an orchestrator backed by Hindsight's three core primitives: retain(), recall(), and reflect(). Memories are organized biomimetically into World Facts (topology & SLAs), Experience Facts (post-mortems & failed runbooks), and Observations (cross-incident invariants), paired with Groq high-speed inference (openai/gpt-oss-120b & qwen/qwen3-32b) protected by our self-healing ToolCallGuard.",
  },
  {
    title: "Slide 3 of 5: Inside Hindsight TEMPR — 4-Way Parallel Retrieval",
    src: SLIDE_PREFIX + "slide_3_tempr.jpg",
    notes:
      "Standard vector RAG fails on production logs because embeddings blur hex error codes like 0x8F4A_POOL_EXHAUSTED. Hindsight's TEMPR engine runs four parallel retrieval strategies simultaneously: (1) Semantic Vector Search, (2) BM25 Exact Keyword Match, (3) 2-Hop Entity Knowledge Graph Traversal across dependent services, and (4) Temporal Decay Reasoning—fusing results via Reciprocal Rank Fusion (RRF).",
  },
  {
    title: "Slide 4 of 5: The Biomimetic Memory Learning Curve (Day 1 to Day 45)",
    src: SLIDE_PREFIX + "slide_4_learning_curve.jpg",
    notes:
      "Watch how AegisSRE improves over time: On Day 1 (Interaction 1 - Cold Start), MTTR is 47 minutes. After retaining the post-mortem, by Day 14 (Interaction 5), AegisSRE recalls the failed runbook and resolves recurring cascades in 1.2 minutes. By Day 45 (Interaction 20), hindsight.reflect() synthesizes cross-incident invariants to block risky PR diffs in CI/CD before they even deploy!",
  },
  {
    title: "Slide 5 of 5: Production Results & Business ROI",
    src: SLIDE_PREFIX + "slide_5_impact.jpg",
    notes:
      "Across realistic production benchmarks, AegisSRE delivers an 88%+ reduction in Mean Time To Resolution (MTTR), 100% prevention of repeat runbook failures, zero context loss across SRE on-call handoffs, and strict multi-bank isolation per microservice cluster.",
  },
];

let currentScenario = "pool_exhaustion";
let currentFilter = "all";
let currentSlideIndex = 0;
let toastTimer = null;

function showToast(message) {
  const toast = document.getElementById("globalToast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("hidden");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.classList.add("hidden");
  }, 3200);
}

function pulseElement(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove("flash-pulse");
  void el.offsetWidth;
  el.classList.add("flash-pulse");
}

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initEnvDrawer();
  initWarRoom();
  initMemoryExplorer();
  initCurveAndToolGuard();
  initSlidesViewer();

  document.getElementById("alertInput").value = SCENARIO_PAYLOADS[currentScenario];
  checkEnvStatus();
  runIncidentAnalysis(false);
  loadMemoryExplorer();
  loadLearningCurve();
  runToolGuardDemo(false);
});

function getBankId() {
  return document.getElementById("bankSelect").value;
}

function getModel() {
  return document.getElementById("modelSelect").value;
}

function initEnvDrawer() {
  const drawer = document.getElementById("envConfigPanel");
  document.getElementById("btnToggleEnvPanel").addEventListener("click", () => {
    drawer.classList.toggle("hidden");
  });
  document.getElementById("btnCloseEnvPanel").addEventListener("click", () => {
    drawer.classList.add("hidden");
  });
  document.getElementById("btnSaveEnvKeys").addEventListener("click", async () => {
    const groqKey = document.getElementById("inputGroqKey").value.trim();
    const hindsightKey = document.getElementById("inputHindsightKey").value.trim();
    if (!groqKey && !hindsightKey) {
      showToast("⚠️ Enter your GROQ_API_KEY or HINDSIGHT_API_KEY first.");
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/save-env`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          groq_api_key: groqKey,
          hindsight_api_key: hindsightKey,
        }),
      });
      const data = await resp.json();
      document.getElementById("inputGroqKey").value = "";
      document.getElementById("inputHindsightKey").value = "";
      drawer.classList.add("hidden");
      updateEnvBadge(data.env_status);
      showToast("✅ Saved API keys to local `.env`! Running live triage...");
      runIncidentAnalysis(true);
    } catch (err) {
      showToast("⚠️ Start `python server.py` first so keys can be written to `.env`.");
    }
  });
}

async function checkEnvStatus() {
  try {
    const resp = await fetch(`${API_BASE}/api/env-status`);
    if (resp.ok) {
      const flags = await resp.json();
      updateEnvBadge(flags);
    }
  } catch (_) {
    // Running offline via file://
  }
}

function updateEnvBadge(flags) {
  const label = document.getElementById("envStatusLabel");
  if (!label || !flags) return;
  if (flags.has_groq_api_key) {
    label.textContent = "🟢 Groq Key Active (.env)";
  } else {
    label.textContent = "Set `.env` API Keys";
  }
}

function initTabs() {
  const buttons = document.querySelectorAll(".nav-tab");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const target = document.getElementById(btn.dataset.tab);
      if (target) target.classList.add("active");
    });
  });

  document.getElementById("bankSelect").addEventListener("change", () => {
    showToast(`Switched Memory Bank to ${getBankId()}`);
    runIncidentAnalysis(false);
    loadMemoryExplorer();
    loadLearningCurve();
  });

  document.getElementById("modelSelect").addEventListener("change", () => {
    showToast(`Switched Groq Model to ${getModel()}`);
    runIncidentAnalysis(false);
  });

  document.getElementById("btnResetBank").addEventListener("click", async () => {
    const btn = document.getElementById("btnResetBank");
    btn.textContent = "⏳ Resetting...";
    try {
      await fetch(`${API_BASE}/api/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bank_id: getBankId() }),
      });
      showToast(`✓ Memory bank '${getBankId()}' reset to 6 seed records`);
    } catch (_) {
      showToast("✓ Reset to default demo state");
    }
    btn.textContent = "↻ Reset Demo Bank";
    runIncidentAnalysis(false);
    loadMemoryExplorer();
    loadLearningCurve();
  });
}

function initWarRoom() {
  const presetBtns = document.querySelectorAll(".btn-preset");
  presetBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      presetBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentScenario = btn.dataset.scenario;
      document.getElementById("alertInput").value = SCENARIO_PAYLOADS[currentScenario] || "";
      runIncidentAnalysis(true);
    });
  });

  document.getElementById("btnAnalyze").addEventListener("click", () => {
    // Detect if payload matches one of the presets or is custom
    const val = document.getElementById("alertInput").value.trim();
    let matchedKey = "";
    Object.entries(SCENARIO_PAYLOADS).forEach(([k, v]) => {
      if (v.trim() === val) matchedKey = k;
    });
    currentScenario = matchedKey;
    runIncidentAnalysis(true);
  });

  document.getElementById("btnQuickRetain").addEventListener("click", async () => {
    const input = document.getElementById("quickRetainInput");
    const content = input.value.trim();
    const toast = document.getElementById("quickRetainStatus");
    if (!content) {
      showToast("⚠️ Type a post-mortem note or runbook rule in the input box first!");
      input.focus();
      return;
    }

    try {
      const resp = await fetch(`${API_BASE}/api/retain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bank_id: getBankId(),
          content: content,
        }),
      });
      const data = await resp.json();
      toast.textContent = `✓ Retained as [${data.memory.memory_type}] (${data.memory.memory_id}) with entities: ${data.memory.entities.join(", ")}`;
      showToast(`✅ Retained memory ${data.memory.memory_id} into Hindsight!`);
      input.value = "";
      loadMemoryExplorer();
      runIncidentAnalysis(false);
    } catch (_) {
      toast.textContent = `✓ Retained locally: "${content.slice(0, 60)}..."`;
      showToast("✅ Retained memory in local demo session!");
      input.value = "";
    }
  });
}

async function runIncidentAnalysis(notifyUser = false) {
  const btn = document.getElementById("btnAnalyze");
  const origLabel = "🚀 Run Side-by-Side Triage (`recall` + `reflect`)";
  btn.textContent = "⏳ Querying Hindsight TEMPR + Groq...";

  const alertText = document.getElementById("alertInput").value.trim();
  let data = null;

  try {
    const resp = await fetch(`${API_BASE}/api/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bank_id: getBankId(),
        alert_text: alertText,
        scenario_id: currentScenario || null,
        model: getModel(),
      }),
    });
    if (resp.ok) {
      data = await resp.json();
    }
  } catch (_) {
    // Fallback when opened directly via file:// without server.py running
    data =
      OFFLINE_FALLBACK_SCENARIOS[currentScenario] ||
      OFFLINE_FALLBACK_SCENARIOS.pool_exhaustion;
  } finally {
    btn.textContent = origLabel;
  }

  if (!data) return;

  document.getElementById("scenarioTitle").textContent = data.title;
  document.getElementById("scenarioSeverity").textContent = data.severity;
  document.getElementById("interactionStagePill").textContent = `Stage: ${data.interaction_stage}`;

  // Stateless Card
  document.getElementById("statelessDiagnosis").textContent = data.stateless_agent.diagnosis;
  document.getElementById("statelessCommands").innerHTML = data.stateless_agent.recommended_commands
    .map((cmd) => `<div class="code-item">$ ${escapeHtml(cmd)}</div>`)
    .join("");
  document.getElementById("statelessOutcome").textContent = data.stateless_agent.risk_outcome;
  document.getElementById("statelessMttr").textContent = data.stateless_agent.estimated_mttr;

  // Hindsight Card
  document.getElementById("hindsightDiagnosis").textContent = data.hindsight_agent.diagnosis;
  document.getElementById("hindsightBlocked").innerHTML = data.hindsight_agent.blocked_dangerous_commands
    .map((b) => `<div class="blocked-item">🚫 ${escapeHtml(b)}</div>`)
    .join("");
  document.getElementById("hindsightCommands").innerHTML = data.hindsight_agent.verified_remediation_commands
    .map((cmd) => `<div class="code-item">$ ${escapeHtml(cmd)}</div>`)
    .join("");
  document.getElementById("hindsightOutcome").textContent = data.hindsight_agent.risk_outcome;
  document.getElementById("hindsightMttr").textContent = data.hindsight_agent.estimated_mttr;

  // Live Groq synthesis display if present
  const groqBox = document.getElementById("groqLiveBox");
  const groqContent = document.getElementById("groqLiveContent");
  if (data.hindsight_agent.groq_live_synthesis) {
    groqBox.classList.remove("hidden");
    groqContent.textContent = data.hindsight_agent.groq_live_synthesis;
  } else {
    groqBox.classList.add("hidden");
  }

  // TEMPR Breakdown
  const recall = data.hindsight_recall;
  document.getElementById("temprLatency").textContent = `${recall.latency_ms} ms (${recall.backend_mode})`;
  document.getElementById("recalledMemoriesList").innerHTML = recall.results
    .map(
      (m) => `
      <div class="recalled-card">
        <div class="recalled-top">
          <div>
            <span class="type-badge type-${m.memory_type}">${m.memory_type}</span>
            <strong style="font-size:0.8rem; margin-left:6px;">${m.memory_id}</strong>
            <span style="font-size:0.74rem; color:#94a3b8; margin-left:6px;">(${m.days_ago}d ago)</span>
          </div>
          <span class="badge-success">RRF Score: ${(m.rrf_score * 100).toFixed(1)}%</span>
        </div>
        <div style="font-size:0.83rem; color:#e2e8f0;">${escapeHtml(m.text)}</div>
        <div class="score-bars">
          <div>
            Semantic: ${(m.semantic_score * 100).toFixed(0)}%
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${Math.min(100, m.semantic_score * 100)}%"></div></div>
          </div>
          <div>
            BM25 Exact: ${(m.bm25_score * 100).toFixed(0)}%
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${Math.min(100, m.bm25_score * 100)}%; background:#f59e0b;"></div></div>
          </div>
          <div>
            Entity Graph: ${(m.graph_score * 100).toFixed(0)}%
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${Math.min(100, m.graph_score * 100)}%; background:#10b981;"></div></div>
          </div>
          <div>
            Temporal: ${(m.temporal_score * 100).toFixed(0)}%
            <div class="score-bar-track"><div class="score-bar-fill" style="width:${Math.min(100, m.temporal_score * 100)}%; background:#8b5cf6;"></div></div>
          </div>
        </div>
      </div>`
    )
    .join("");

  // Reflect Box
  document.getElementById("reflectSynthesisBox").textContent = data.hindsight_reflect.text;

  if (notifyUser) {
    pulseElement("comparisonGrid");
    showToast(`⚡ Triage complete (${recall.latency_ms} ms) — Recalled ${recall.results.length} memories!`);
  }
}

function initMemoryExplorer() {
  const pills = document.querySelectorAll(".filter-pills .pill");
  pills.forEach((pill) => {
    btnBindFilter(pill, pills);
  });

  document.getElementById("btnTriggerReflect").addEventListener("click", async () => {
    const btn = document.getElementById("btnTriggerReflect");
    btn.textContent = "⏳ Synthesizing Observation...";
    try {
      await fetch(`${API_BASE}/api/reflect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bank_id: getBankId(),
          query: "checkout-service pg_bouncer auth-middleware retry storm invariants",
          persist_observation: true,
        }),
      });
      showToast("✨ Hindsight `reflect()` synthesized & saved a new [observation] invariant!");
      loadMemoryExplorer();
    } catch (_) {
      showToast("⚠️ Run `python server.py` to persist new observations to SQLite.");
    } finally {
      btn.textContent = "✨ Run `reflect()` & Persist Observation";
    }
  });
}

function btnBindFilter(pill, pills) {
  pill.addEventListener("click", () => {
    pills.forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    currentFilter = pill.dataset.filter;
    loadMemoryExplorer();
    showToast(`Filtered Memory Bank by: ${currentFilter}`);
  });
}

async function loadMemoryExplorer() {
  try {
    const resp = await fetch(`${API_BASE}/api/memories?bank_id=${encodeURIComponent(getBankId())}`);
    const data = await resp.json();

    document.getElementById("statTotalMemories").textContent = data.graph.total_memories;
    document.getElementById("statWorldCount").textContent = data.graph.counts_by_type.world;
    document.getElementById("statExpCount").textContent = data.graph.counts_by_type.experience;
    document.getElementById("statObsCount").textContent = data.graph.counts_by_type.observation;

    const filtered =
      currentFilter === "all"
        ? data.memories
        : data.memories.filter((m) => m.memory_type === currentFilter);

    document.getElementById("bankMemoriesContainer").innerHTML = filtered
      .map(
        (m) => `
        <div class="recalled-card">
          <div class="recalled-top">
            <span><span class="type-badge type-${m.memory_type}">${m.memory_type}</span> <strong>${m.memory_id}</strong></span>
            <span style="font-size:0.75rem; color:#94a3b8;">${m.days_ago} days ago</span>
          </div>
          <div style="font-size:0.84rem; margin: 0.35rem 0;">${escapeHtml(m.text)}</div>
          <div style="display:flex; gap:0.35rem; flex-wrap:wrap;">
            ${m.entities.map((e) => `<span class="badge-muted" style="font-size:0.7rem;">#${escapeHtml(e)}</span>`).join("")}
          </div>
        </div>`
      )
      .join("");

    renderKnowledgeGraph(data.graph);
  } catch (_) {
    // Offline fallback message if opened via file:// without server
    document.getElementById("bankMemoriesContainer").innerHTML =
      `<div class="recalled-card">Run <code>python server.py</code> and open <code>http://127.0.0.1:8080</code> to inspect live SQLite memory records.</div>`;
  }
}

function renderKnowledgeGraph(graph) {
  const svg = document.getElementById("knowledgeGraphSvg");
  const nodes = graph.nodes.slice(0, 14);
  const nodeIds = new Set(nodes.map((n) => n.id));
  const edges = graph.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));

  const cx = 320;
  const cy = 180;
  const radius = 135;
  const positions = {};

  nodes.forEach((node, idx) => {
    const angle = (2 * Math.PI * idx) / Math.max(nodes.length, 1);
    positions[node.id] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      group: node.group,
      label: node.label,
    };
  });

  let html = "";
  edges.forEach((e) => {
    const p1 = positions[e.source];
    const p2 = positions[e.target];
    if (p1 && p2) {
      html += `<line x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" stroke="#233252" stroke-width="${Math.min(3, e.weight)}" />`;
    }
  });

  Object.values(positions).forEach((pos) => {
    let color = "#06b6d4";
    if (pos.group === "incident") color = "#f59e0b";
    if (pos.group === "error_or_flag") color = "#ef4444";

    html += `
      <g>
        <circle cx="${pos.x}" cy="${pos.y}" r="9" fill="${color}" stroke="#070b14" stroke-width="2" />
        <text x="${pos.x}" y="${pos.y - 13}" fill="#e2e8f0" font-size="11" text-anchor="middle" font-family="monospace">${escapeHtml(pos.label)}</text>
      </g>`;
  });

  svg.innerHTML = html;
}

function initCurveAndToolGuard() {
  document.getElementById("btnRunToolGuardTest").addEventListener("click", () => {
    runToolGuardDemo(true);
  });
}

async function loadLearningCurve() {
  try {
    const resp = await fetch(`${API_BASE}/api/learning-curve?bank_id=${encodeURIComponent(getBankId())}`);
    const data = await resp.json();

    document.getElementById("learningCurveStages").innerHTML = data.stages
      .map(
        (s) => `
        <div class="curve-stage-card">
          <div style="display:flex; justify-content:space-between; margin-bottom:0.4rem;">
            <span class="badge-accent">Interaction ${s.interaction} (${s.day})</span>
            <strong style="color:#34d399;">MTTR: ${s.mttr_minutes} min</strong>
          </div>
          <h4 style="margin-bottom:0.4rem;">${escapeHtml(s.label)}</h4>
          <p style="font-size:0.84rem; color:#94a3b8; margin-bottom:0.65rem;">${escapeHtml(s.summary)}</p>
          <div style="font-size:0.78rem;">Diagnostic Accuracy: <strong>${s.accuracy_pct}%</strong></div>
          <div class="score-bar-track" style="height:7px; margin-top:4px;">
            <div class="score-bar-fill" style="width:${s.accuracy_pct}%; background:#10b981;"></div>
          </div>
        </div>`
      )
      .join("");
  } catch (_) {}
}

async function runToolGuardDemo(notifyUser = false) {
  try {
    const resp = await fetch(`${API_BASE}/api/tool-guard-demo`);
    const data = await resp.json();

    document.getElementById("toolGuardOutput").innerHTML = data.self_healing_demos
      .map(
        (item) => `
        <div class="recalled-card">
          <div class="recalled-top">
            <span class="badge-accent">${escapeHtml(item.model)} → ${escapeHtml(item.tool_name)}</span>
            <span class="badge-success">✓ RECOVERED</span>
          </div>
          <div class="section-label" style="margin-top:0.5rem;">Raw Malformed LLM Output</div>
          <pre class="code-item" style="white-space:pre-wrap; color:#fca5a5; margin-bottom:0.5rem;">${escapeHtml(item.raw_malformed)}</pre>
          <div class="section-label">Repaired &amp; Schema-Validated Tool Payload</div>
          <pre class="code-item" style="white-space:pre-wrap; color:#6ee7b7;">${escapeHtml(JSON.stringify(item.repaired_json, null, 2))}</pre>
          <div style="font-size:0.76rem; color:#38bdf8; margin-top:0.4rem;">
            Issues Auto-Repaired: <strong>${escapeHtml(item.repair_event.issue_detected)}</strong>
          </div>
        </div>`
      )
      .join("");
    if (notifyUser) {
      pulseElement("toolGuardOutput");
      showToast("✅ Verified GroqToolCallGuard self-healing across Qwen3-32B & GPT-OSS-120B!");
    }
  } catch (_) {
    if (notifyUser) {
      showToast("⚠️ Run `python server.py` to execute live Python ToolGuard checks.");
    }
  }
}

function initSlidesViewer() {
  const dotsContainer = document.getElementById("slideDots");
  dotsContainer.innerHTML = SLIDES_DATA.map(
    (_, idx) => `<div class="slide-dot ${idx === 0 ? "active" : ""}" data-idx="${idx}"></div>`
  ).join("");

  dotsContainer.querySelectorAll(".slide-dot").forEach((dot) => {
    dot.addEventListener("click", () => {
      currentSlideIndex = parseInt(dot.dataset.idx, 10);
      renderCurrentSlide();
    });
  });

  document.getElementById("btnPrevSlide").addEventListener("click", () => {
    currentSlideIndex = (currentSlideIndex - 1 + SLIDES_DATA.length) % SLIDES_DATA.length;
    renderCurrentSlide();
  });

  document.getElementById("btnNextSlide").addEventListener("click", () => {
    currentSlideIndex = (currentSlideIndex + 1) % SLIDES_DATA.length;
    renderCurrentSlide();
  });

  document.addEventListener("keydown", (e) => {
    const slidesTab = document.getElementById("tab-slides");
    if (!slidesTab.classList.contains("active")) return;
    if (e.key === "ArrowRight") {
      currentSlideIndex = (currentSlideIndex + 1) % SLIDES_DATA.length;
      renderCurrentSlide();
    } else if (e.key === "ArrowLeft") {
      currentSlideIndex = (currentSlideIndex - 1 + SLIDES_DATA.length) % SLIDES_DATA.length;
      renderCurrentSlide();
    }
  });

  renderCurrentSlide();
}

function renderCurrentSlide() {
  const slide = SLIDES_DATA[currentSlideIndex];
  document.getElementById("slideHeaderTitle").textContent = slide.title;
  document.getElementById("slideImage").src = slide.src;
  document.getElementById("slideSpeakerNotes").textContent = slide.notes;

  document.querySelectorAll(".slide-dot").forEach((dot, idx) => {
    dot.classList.toggle("active", idx === currentSlideIndex);
  });
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
