# System Architecture: Text-to-SQL LLM Platform

## Layers Overview

```
 ┌────────────────────────────────────────────────────────┐
 │  Client / Eval / Load Test                             │
 │  (HTTP POST /answer  •  run_eval.py  •  driver.py)     │
 └───────────────────────┬────────────────────────────────┘
                         │ HTTP :8001
 ┌───────────────────────▼────────────────────────────────┐
 │  Agent Server  (FastAPI + LangGraph)                   │
 │                                                        │
 │  attach_schema ──► generate_sql ──► execute            │
 │                                         │              │
 │                                      verify            │
 │                        revise  ◄────── │ ok=false      │
 │                                        │ ok=true ──► END│
 └───────────┬─────────────────────┬──────────────────────┘
             │ OpenAI API :8000    │ Langfuse callback
             │                     │ HTTP :3001
 ┌───────────▼──────────┐  ┌───────▼──────────────────────┐
 │  vLLM                │  │  Langfuse (trace store)       │
 │  Qwen3-30B-A3B       │  │  spans: generate/verify/revise│
 │  H100 80GB           │  │  per-call latency, tokens     │
 │  /metrics → Prom.    │  └──────────────────────────────┘
 └───────────┬──────────┘
             │ scrape every 5s
 ┌───────────▼──────────┐
 │  Prometheus :9090    │
 └───────────┬──────────┘
             │ datasource
 ┌───────────▼──────────┐
 │  Grafana :3000       │
 │  latency / throughput│
 │  / KV cache panels   │
 └──────────────────────┘
```

## Port Map

| Service | Port | Purpose |
|---|---|---|
| vLLM | 8000 | LLM inference, OpenAI-compatible API |
| Agent server | 8001 | FastAPI, exposes `/answer` and `/health` |
| Prometheus | 9090 | Metrics store, scrapes vLLM every 5s |
| Grafana | 3000 | Serving dashboards |
| Langfuse | 3001 | Agent trace store |

---

## Data Flow (per request)

1. `POST /answer {question, db}` hits FastAPI ([agent/server.py](../mlops-assignment/agent/server.py))
2. `attach_schema` renders the target SQLite DB's DDL into the LangGraph state
3. `generate_sql` → LLM call #1 → returns SQL inside a code fence
4. `execute_sql` → SQLite read-only (`file:?mode=ro`), returns rows or error
5. `verify` → LLM call #2 → returns `{"ok": bool, "issue": str}`
6. Router: `ok=true` or `iteration >= MAX_ITERATIONS` → return final state; else `revise` (LLM call #3) → back to step 4

Every LLM call is traced as a Langfuse span. Every LLM call hits vLLM and appears in Prometheus metrics.

---

## Two Observability Planes

| Plane | Tool | What it answers |
|---|---|---|
| **Infrastructure** | Prometheus + Grafana | Is vLLM slow, queued, or KV-cache-starved right now? |
| **Application** | Langfuse | Which specific request triggered 3 revise loops and why? |

They answer different questions. Grafana diagnoses the serving layer bottleneck; Langfuse diagnoses query-level failures. You need both to close the Phase 6 diagnostic loop.

---

## Agent Graph ([agent/graph.py](../mlops-assignment/agent/graph.py))

```
START
  │
  ▼
attach_schema          — renders SQLite DDL, stored in AgentState.schema
  │
  ▼
generate_sql           — LLM call #1, extracts SQL from code fence
  │
  ▼
execute                — sqlite3 read-only, returns ExecutionResult
  │
  ▼
verify                 — LLM call #2, parses {"ok", "issue"} JSON
  │
  ├─ ok=true  ──────────────────────────────────────────► END
  │
  └─ ok=false AND iteration < MAX_ITERATIONS
       │
       ▼
     revise             — LLM call #3, given schema + question + prior SQL + issue
       │
       └──────────────► execute  (loop back)
```

`MAX_ITERATIONS = 3` caps worst-case latency at roughly 3× a single generate-execute-verify cycle.

---

## Key Design Decisions and Trade-offs

### Verify-revise loop vs. single prompt

The loop adds 1-2 extra LLM calls per bad query but catches errors a single pass cannot self-correct: SQL syntax errors, wrong column references, zero rows for a specific-entity question. The cost is latency — worst case is `MAX_ITERATIONS × (LLM + SQLite + LLM)`. The 5-second P95 SLO makes this the central tension of Phase 6.

### Schema rendering into context

Full DDL goes into every LLM call, driving prompt length to 1.5-3K tokens. This causes long prefill phases and high KV cache pressure. Truncating the schema risks wrong table/column references; including it fully is correct but expensive. The right lever if prompts are too long is schema pruning (include only tables relevant to the question), not shortening prompts arbitrarily.

### OpenAI-compatible API as the integration seam

The agent talks to `VLLM_BASE_URL` via `langchain_openai.ChatOpenAI`. This makes the inference backend swappable — vLLM on H100, OpenAI API, or CPU vLLM for local dev — without touching agent code. Configured via `VLLM_BASE_URL` and `VLLM_MODEL` in `.env`.

### SQLite read-only execution

`sqlite3.connect("file:path?mode=ro", uri=True)` prevents accidental writes. Correct and sufficient for a PoC. Replacing this with a real warehouse (Snowflake, BigQuery) requires only swapping `execute_sql` in [agent/execution.py](../mlops-assignment/agent/execution.py) — the graph shape is unchanged.

### MoE model (Qwen3-30B-A3B)

Only ~3B parameters are active per forward pass despite 30B total. This enables high throughput on a single H100, but KV cache behavior differs from dense models: active-expert patterns vary per token, so prefix reuse rates are lower. Under load, KV cache exhaustion is the most likely first bottleneck.

---

## Bottleneck Map (Phase 6 reference)

At 10 RPS with 2-3 LLM calls per agent run, vLLM receives ~20-30 concurrent requests. Failure modes in order of likelihood:

| Symptom in Grafana | Root cause | Lever to pull |
|---|---|---|
| `gpu_cache_usage_perc` → 100%, P95 latency spikes | KV cache exhaustion from long prompts | Reduce `max_model_len`, trim schema in prompts |
| `num_requests_running` climbs, TTFT grows | Request queue backpressure | Increase `max_num_seqs`, review chunked prefill config |
| Latency variance high, some requests 10×+ slower | Revise loop amplification — bad query batch triggers 3 revisions | Improve prompts to reduce revise rate; check per-iteration eval |

Diagnostic sequence for each iteration: identify the metric that moves first → form a hypothesis → change one vLLM flag → re-run → confirm the target metric moved → check whether P95 end-to-end latency followed.

---

## File Map

```
mlops-assignment/
├── agent/
│   ├── server.py        FastAPI app, Langfuse callback wiring
│   ├── graph.py         LangGraph state machine, all node implementations
│   ├── prompts.py       Prompt templates for generate/verify/revise
│   ├── execution.py     SQLite executor, ExecutionResult dataclass
│   └── schema.py        Schema DDL renderer, db_path resolver
├── evals/
│   └── run_eval.py      Offline eval: execution accuracy, per-iteration pass rate
├── load_test/
│   └── driver.py        Load test driver for SLO validation
├── scripts/
│   ├── load_data.py     Loads BIRD-bench SQLite databases
│   └── smoke_phase4.py  Smoke test (10 questions via /answer)
├── infra/
│   ├── prometheus.yml              Scrape config (vLLM at host.docker.internal:8000)
│   └── grafana/provisioning/
│       ├── datasources/            Prometheus datasource
│       └── dashboards/serving.json Grafana dashboard (latency, throughput, KV cache)
├── results/
│   ├── eval_baseline.json          Baseline eval results (Phase 5)
│   └── eval_after_tuning.json      Post-tuning eval results (Phase 6)
└── docs/
    ├── running.md       How to start all services
    └── architecture.md  This file
```
