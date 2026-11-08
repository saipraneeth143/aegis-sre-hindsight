// AegisSRE Frontend Controller
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

const SLIDES_DATA = [
  {
    title: "Slide 1 of 5: The $14,000/Minute Amnesia Problem",
    src: "/static/slides/slide_1_problem.jpg",
    notes:
      "When production goes down at 3:00 AM, a stateless AI agent suffers from operational amnesia. Given a connection pool saturation alert, a stateless model suggests generic textbook fixes—like restarting pods or doubling connection pool limits—which triggers a massive retry storm and crashes the primary database. AegisSRE uses Vectorize Hindsight to recall exact past post-mortems, block failed runbooks, and execute verified fixes in seconds.",
  },
  {
    title: "Slide 2 of 5: AegisSRE x Hindsight Biomimetic Memory Architecture",
    src: "/static/slides/slide_2_architecture.jpg",
    notes:
      "AegisSRE ingests PagerDuty alerts, Kubernetes pod events, and Git PR diffs into an orchestrator backed by Hindsight's three core primitives: retain(), recall(), and reflect(). Memories are organized biomimetically into World Facts (topology & SLAs), Experience Facts (post-mortems & failed runbooks), and Observations (cross-incident invariants), paired with Groq high-speed inference (openai/gpt-oss-120b & qwen/qwen3-32b) protected by our self-healing ToolCallGuard.",
  },
  {
    title: "Slide 3 of 5: Inside Hindsight TEMPR — 4-Way Parallel Retrieval",
    src: "/static/slides/slide_3_tempr.jpg",
    notes:
      "Standard vector RAG fails on production logs because embeddings blur hex error codes like 0x8F4A_POOL_EXHAUSTED. Hindsight's TEMPR engine runs four parallel retrieval strategies simultaneously: (1) Semantic Vector Search, (2) BM25 Exact Keyword Match, (3) 2-Hop Entity Knowledge Graph Traversal across dependent services, and (4) Temporal Decay Reasoning—fusing results via Reciprocal Rank Fusion (RRF).",
  },
  {
    title: "Slide 4 of 5: The Biomimetic Memory Learning Curve (Day 1 to Day 45)",
    src: "/static/slides/slide_4_learning_curve.jpg",
    notes:
      "Watch how AegisSRE improves over time: On Day 1 (Interaction 1 - Cold Start), MTTR is 47 minutes. After retaining the post-mortem, by Day 14 (Interaction 5), AegisSRE recalls the failed runbook and resolves recurring cascades in 1.2 minutes. By Day 45 (Interaction 20), hindsight.reflect() synthesizes cross-incident invariants to block risky PR diffs in CI/CD before they even deploy!",
  },
  {
    title: "Slide 5 of 5: Production Results & Business ROI",
    src: "/static/slides/slide_5_impact.jpg",
    notes:
      "Across realistic production benchmarks, AegisSRE delivers an 88%+ reduction in Mean Time To Resolution (MTTR), 100% prevention of repeat runbook failures, zero context loss across SRE on-call handoffs, and strict multi-bank isolation per microservice cluster.",
  },
];

let currentScenario = "pool_exhaustion";
let currentFilter = "all";
let currentSlideIndex = 0;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initWarRoom();
  initMemoryExplorer();
  initCurveAndToolGuard();
  initSlidesViewer();

  // Initial load
  document.getElementById("alertInput").value = SCENARIO_PAYLOADS[currentScenario];
  runIncidentAnalysis();
  loadMemoryExplorer();
  loadLearningCurve();
  runToolGuardDemo();
});

function getBankId() {
  return document.getElementById("bankSelect").value;
}

function getModel() {
  return document.getElementById("modelSelect").value;
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
    runIncidentAnalysis();
    loadMemoryExplorer();
    loadLearningCurve();
  });

  document.getElementById("btnResetBank").addEventListener("click", async () => {
    await fetch("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bank_id: getBankId() }),
    });
    runIncidentAnalysis();
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
      runIncidentAnalysis();
    });
  });

  document.getElementById("btnAnalyze").addEventListener("click", () => {
    currentScenario = "";
    runIncidentAnalysis();
  });

  document.getElementById("btnQuickRetain").addEventListener("click", async () => {
    const input = document.getElementById("quickRetainInput");
    const content = input.value.trim();
    const toast = document.getElementById("quickRetainStatus");
    if (!content) return;

    const resp = await fetch("/api/retain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bank_id: getBankId(),
        content: content,
      }),
    });
    const data = await resp.json();
    toast.textContent = `✓ Retained as [${data.memory.memory_type}] (${data.memory.memory_id}) with entities: ${data.memory.entities.join(", ")}`;
    input.value = "";
    loadMemoryExplorer();
    runIncidentAnalysis();
  });
}

async function runIncidentAnalysis() {
  const alertText = document.getElementById("alertInput").value.trim();
  const resp = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      bank_id: getBankId(),
      alert_text: alertText,
      scenario_id: currentScenario || null,
      model: getModel(),
    }),
  });
  const data = await resp.json();

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
}

function initMemoryExplorer() {
  const pills = document.querySelectorAll(".filter-pills .pill");
  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      currentFilter = pill.dataset.filter;
      loadMemoryExplorer();
    });
  });

  document.getElementById("btnTriggerReflect").addEventListener("click", async () => {
    await fetch("/api/reflect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bank_id: getBankId(),
        query: "checkout-service pg_bouncer auth-middleware retry storm invariants",
        persist_observation: true,
      }),
    });
    loadMemoryExplorer();
  });
}

async function loadMemoryExplorer() {
  const resp = await fetch(`/api/memories?bank_id=${encodeURIComponent(getBankId())}`);
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
  document.getElementById("btnRunToolGuardTest").addEventListener("click", runToolGuardDemo);
}

async function loadLearningCurve() {
  const resp = await fetch(`/api/learning-curve?bank_id=${encodeURIComponent(getBankId())}`);
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
}

async function runToolGuardDemo() {
  const resp = await fetch("/api/tool-guard-demo");
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

