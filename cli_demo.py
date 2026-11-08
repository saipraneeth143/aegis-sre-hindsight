"""
AegisSRE Interactive Terminal Demo (`cli_demo.py`).

Demonstrates live Hindsight `retain()`, `recall()` (4-way TEMPR), and `reflect()` calls
alongside `GroqToolCallGuard` self-healing for `openai/gpt-oss-120b` and `qwen/qwen3-32b`.
"""

from __future__ import annotations

from agent.aegis_sre import AegisSREAgent
from data.realistic_incidents import DEFAULT_BANK_ID, INTERACTION_SCENARIOS


def print_banner(title: str) -> None:
    line = "=" * 78
    print(f"\n{line}\n  {title}\n{line}")


def run_cli_walkthrough() -> None:
    agent = AegisSREAgent()
    agent.reset_and_seed(DEFAULT_BANK_ID)

    print_banner("AEGISSRE x VECTORIZE HINDSIGHT — LIVE MEMORY WALKTHROUGH")
    print(f"Memory Bank       : {DEFAULT_BANK_ID}")
    print(f"Hindsight Backend : {agent.memory.backend_mode}")
    print(f"Groq Models Ready : {', '.join(agent.llm.SUPPORTED_MODELS)}")

    # Step 1: Show initial retained memories in bank
    memories = agent.memory.list_memories(DEFAULT_BANK_ID)
    print(f"\n[1] Initial Retained Memories in `{DEFAULT_BANK_ID}`: {len(memories)} records")
    for m in memories:
        print(f"    • [{m.memory_type.upper():11s}] ({m.memory_id}) {m.text[:88]}...")

    # Step 2: Execute Side-by-Side Triage on P1 Pool Exhaustion Alert
    print_banner("STEP 2: P1 INCIDENT TRIAGE — STATELESS AI vs. HINDSIGHT MEMORY")
    scenario = INTERACTION_SCENARIOS["pool_exhaustion"]
    result = agent.analyze_incident(
        alert_text=scenario["alert_payload"],
        bank_id=DEFAULT_BANK_ID,
        scenario_id="pool_exhaustion",
        model="openai/gpt-oss-120b",
    )

    print("\n--- [A] WITHOUT MEMORY (Stateless AI Agent) ---")
    print(f"Diagnosis : {result['stateless_agent']['diagnosis']}")
    print(f"Commands  : {result['stateless_agent']['recommended_commands']}")
    print(f"Outcome   : {result['stateless_agent']['risk_outcome']}")
    print(f"Est. MTTR : {result['stateless_agent']['estimated_mttr']}")

    print("\n--- [B] WITH HINDSIGHT MEMORY (AegisSRE Agent) ---")
    print(f"Diagnosis : {result['hindsight_agent']['diagnosis']}")
    print("Blocked Anti-Patterns:")
    for blk in result["hindsight_agent"]["blocked_dangerous_commands"]:
        print(f"  [BLOCKED] {blk}")
    print("Verified Remediation Commands:")
    for cmd in result["hindsight_agent"]["verified_remediation_commands"]:
        print(f"  $ {cmd}")
    print(f"Outcome   : {result['hindsight_agent']['risk_outcome']}")
    print(f"Est. MTTR : {result['hindsight_agent']['estimated_mttr']}")

    # Step 3: Show TEMPR 4-Way Retrieval Scores
    print_banner("STEP 3: HINDSIGHT TEMPR 4-WAY PARALLEL RECALL BREAKDOWN")
    for r in result["hindsight_recall"]["results"][:3]:
        print(
            f"  {r['memory_id']} [{r['memory_type']}] -> RRF={r['rrf_score']:.3f} "
            f"(Semantic={r['semantic_score']:.2f}, BM25={r['bm25_score']:.2f}, "
            f"Graph={r['graph_score']:.2f}, Temporal={r['temporal_score']:.2f})"
        )

    # Step 4: Retain a brand-new incident post-mortem & recall it immediately
    print_banner("STEP 4: LIVE `retain()` + `recall()` + `reflect()` IN ACTION")
    new_postmortem = (
        "[Post-Mortem INC-4490] Symptom: kong-edge-ingress 502 Bad Gateway spike after TLS cert rotation. "
        "FAILED RUNBOOK: Do not reload nginx worker processes with -s HUP during active WebSocket drainage. "
        "VERIFIED FIX: Run `kong-ctl graceful-drain --wait 15s` before rotating secret bundle."
    )
    retained = agent.memory.retain(bank_id=DEFAULT_BANK_ID, content=new_postmortem)
    print(f"Retained Memory ID : {retained.memory_id} | Tier: [{retained.memory_type}]")
    print(f"Extracted Entities : {retained.entities}")

    recalled_after = agent.memory.recall(
        bank_id=DEFAULT_BANK_ID,
        query="kong-edge-ingress 502 Bad Gateway TLS cert rotation WebSocket",
        top_k=2,
    )
    top_hit = recalled_after.results[0]
    print(f"Immediate Recall Top Hit: {top_hit.memory_id} (RRF={top_hit.rrf_score:.3f})")
    print(f"  -> {top_hit.text}")

    reflection = agent.memory.reflect(
        bank_id=DEFAULT_BANK_ID,
        query="What runbook actions are dangerous across checkout-service and kong-edge-ingress?",
        persist_observation=True,
    )
    print(f"\nHindsight Reflect Synthesis:\n{reflection.text}")

    # Step 5: Demonstrate Groq Function-Calling Self-Healing
    print_banner("STEP 5: GROQ TOOL-CALL SELF-HEALING (`qwen/qwen3-32b` & `openai/gpt-oss-120b`)")
    guard_demo = agent.demonstrate_tool_guard_self_healing()
    for d in guard_demo["self_healing_demos"]:
        print(f"  Model: {d['model']} | Tool: {d['tool_name']}")
        print(f"  Issues Auto-Repaired: {d['repair_event']['issue_detected']}")
        print(f"  Clean Args: {d['repaired_json']}\n")


if __name__ == "__main__":
    run_cli_walkthrough()

