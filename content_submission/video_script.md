# 3-Minute Screen-Recorded Demo Script (`video_script.md`)

**Presenter**: [YOUR NAME]
**Project**: AegisSRE — Autonomous Incident Commander & Pre-Deploy Shield powered by Vectorize Hindsight

---

## 5 High-Performing YouTube Titles
1. **A Stateless AI Agent Almost OOM-Killed Our Postgres Primary — Here’s How Hindsight Fixed It**
2. **Why Vector RAG Fails at 3 AM (Building a Production SRE Agent with Hindsight Memory)**
3. **Teaching an AI Agent Which Runbooks NOT to Run with Hindsight & Groq**
4. **From 47-Minute Outages to 42 Seconds: Inside Hindsight’s 4-Way TEMPR Memory Engine**
5. **How I Built an Autonomous DevOps Incident Commander with Hindsight (`retain`, `recall`, `reflect`)**

---

## Video Script (3 Minutes Total)

### 1. Quick Intro (0:00 – 0:30)
* **[SCREEN CUE]**: Start on camera (or split-screen) showing the AegisSRE Mission Control UI at `http://localhost:8080/` (`static/index.html`) with the glowing `HINDSIGHT MEMORY ENGINE` badge.
* **Narration**:
  "Hi, I’m [YOUR NAME]. If you’ve ever been paged at 3 AM for a production outage, you know the worst thing you can do is run a textbook command that accidentally triggers a retry storm. Stateless AI agents do this constantly because they wake up with zero memory of your cluster’s past outages. So I built **AegisSRE**—an incident response and CI/CD pre-deploy agent powered by **Vectorize Hindsight** and **Groq** that remembers every post-mortem, learns which runbooks failed, and gets measurably smarter after every incident."

### 2. The Problem: Stateless Agent Failing Without Memory (0:30 – 1:00)
* **[SCREEN CUE]**: Click **Scenario 1 (`0x8F4A_POOL_EXHAUSTED` in `checkout-service`)** in Tab 1 (`Incident War-Room`). Zoom in on the left red card labeled **WITHOUT MEMORY: Stateless AI Agent**.
* **Narration**:
  "Look at what happens here when `checkout-service` hits `0x8F4A_POOL_EXHAUSTED` with `pg_bouncer` saturated at 400 connections. On the left, the stateless agent sees connection exhaustion and tells the on-call engineer to bump `max_client_conn` to 1200 and run `kubectl rollout restart deployment/checkout-service`. In our cluster, nineteen days ago during `INC-4082`, running that exact command caused a thundering herd that OOM-killed our primary Postgres database for 42 minutes."

### 3. Live Demo: `retain`, `recall`, and `reflect` in Action (1:00 – 2:30)
* **[SCREEN CUE]**: Pan to the right green card (**WITH HINDSIGHT: AegisSRE Memory Agent**) and scroll down to the **Hindsight TEMPR 4-Way Parallel Retrieval Breakdown** panel.
* **Narration**:
  "Now look at the right panel powered by Hindsight. When `/api/analyze` in `server.py` calls `hindsight.recall()`, it doesn't just run basic cosine similarity. It runs Hindsight’s 4-way TEMPR search—combining Semantic search, BM25 exact keyword matching on `0x8F4A_POOL_EXHAUSTED`, a 2-hop Entity Knowledge Graph walk from `checkout-service` to `auth-middleware`, and Temporal decay. In 1.8 milliseconds, it recalls `INC-4082`, actively **blocks** the dangerous pod restart, and outputs the verified 14-second fix: flipping `FF_ASYNC_TOKEN_REFRESH=false`."
* **[SCREEN CUE]**: Type a new post-mortem into the **Quick Retain (`hindsight.retain`)** input box at the bottom right (or switch to terminal running `python cli_demo.py`), click **Retain in Bank**, and switch to **Tab 2 (`Hindsight Memory Bank`)**.
* **Narration**:
  "Let’s retained a brand new post-mortem live using `hindsight.retain()`. Watch how `hindsight_memory/engine.py` classifies it into Hindsight's biomimetic tiers—`world` facts, `experience` facts, and synthesized `observation` invariants—and wires the entities directly into the 2-hop blast-radius graph. And when we click **Scenario 3 (Interaction 20)**, `hindsight.reflect()` synthesizes our past outages to block a risky pull request (`PR #1842`) in CI/CD before it even deploys."
* **[SCREEN CUE]**: Briefly click **Tab 3 (`Learning Curve & Groq Tool-Guard`)** to show `GroqToolCallGuard` repairing `<think>` tags and trailing commas from `qwen/qwen3-32b` and `openai/gpt-oss-120b`.
* **Narration**:
  "We also built `GroqToolCallGuard` in `agent/groq_client.py` so even when fast reasoning models like `qwen/qwen3-32b` emit `<think>` tags or malformed JSON during tool calls, AegisSRE self-heals the payload with zero crashes."

### 4. Wrap-Up & Key Takeaway (2:30 – 3:00)
* **[SCREEN CUE]**: Switch to **Tab 4 (`Interactive Pictured Pitch Slides`)** showing Slide 4 (`The Biomimetic Memory Learning Curve: Day 1 to Day 45`).
* **Narration**:
  "What surprised me most building with Hindsight is that **negative memory**—remembering what failed—is twice as valuable in production as remembering what worked. By separating `world`, `experience`, and `observation` memories, our agent went from a 47-minute cold-start triage on Day 1 to preventing outages in 0 seconds before merge by Day 45. Check out the repo link in the description, and give Hindsight a spin!"

