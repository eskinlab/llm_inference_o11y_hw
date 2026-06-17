# Assignment 2 - Task Log

## Phase 0 — Setup (Local) ✅

- [x] Opened project in WSL2 to avoid Windows compatibility issues with `ray` and `vllm`
- [x] Installed Python 3.12 via uv (`uv python install 3.12`)
- [x] Installed all dependencies (`uv sync --python 3.12`)
- [x] `.env` created from `.env.example`
- [x] `docker compose up -d` — all 9 containers healthy (Prometheus, Grafana, Postgres, ClickHouse, Redis, MinIO, MinIO-init, Langfuse-worker, Langfuse-web)
- [x] `uv run python scripts/load_data.py` — BIRD data downloaded:
  - 11 SQLite databases under `data/bird/`
  - `evals/eval_set.jsonl` — 30 eval questions
  - `load_test/perf_pool.jsonl` — perf pool questions
- [x] Langfuse project created at `http://localhost:3001`, API keys added to `.env`
- [x] OpenAI API configured in `.env` (`gpt-4o-mini` for local dev)
- [x] Fix `.env`: rename `LANGFUSE_BASE_URL` → `LANGFUSE_HOST` (SDK expects this name)
- [x] Fix `.env`: remove duplicate `VLLM_MODEL` line leftover from template

### UIs verified
| URL | Status |
|-----|--------|
| http://localhost:9090 | Prometheus ✅ |
| http://localhost:3000 | Grafana (admin/admin) ✅ |
| http://localhost:3001 | Langfuse ✅ |

### Notes
- Using OpenAI `gpt-4o-mini` as LLM backend for local development
- Will switch to `Qwen3-30B-A3B` on H100 for final screenshots and results
- HF token not needed until H100 run
- Grafana dashboard will be built and validated on H100 (hosted APIs don't expose `/metrics`)

---

## Phase 3 — Agent Implementation ✅

*Local dev with Nebius AI (`Qwen/Qwen3-30B-A3B-Instruct-2507`).*

- [x] Implemented `verify_node`, `revise_node`, `route_after_verify` in `agent/graph.py`
- [x] Filled all 6 prompt strings in `agent/prompts.py`
- [x] Added `_strip_thinking` to handle Qwen3 `<think>` tokens, defensive JSON parsing for verify
- [x] Tested 5 questions from `eval_set.jsonl` — revise loop triggered on the financial/crimes question (`iterations: 3`, hit cap)
- [x] Agent server confirmed running at `http://localhost:8001`

---

## Phase 4 — Langfuse Tracing ✅

*Local dev — Langfuse at `http://localhost:3001`.*

### Analysis

The callback handler is already scaffolded in `agent/server.py:25-29`. Two bugs and one gap:

1. **Wrong import path** — `server.py:27` uses `langfuse.langchain` but README says `langfuse.callback`.
2. **No automatic metadata** — `config["metadata"]` only passes user-supplied `req.tags`; `db_id` should always be injected so every trace is filterable by database in Langfuse (needed in Phase 6).
3. **Module-level init is fine** — `load_dotenv()` runs before the handler is constructed, so env vars are available. No change needed there.

`agent/graph.py` needs no changes — LangGraph's callback system auto-creates spans for every node and captures LLM prompt/response/tokens.

`evals/run_eval.py` and `load_test/driver.py` don't need Phase 4 changes: `eval_one()` isn't implemented yet (Phase 5), and load-test tags can be added in Phase 6 if needed.

### Tasks

- [x] **Fix import** — `server.py:27`: change `from langfuse.langchain import CallbackHandler` → `from langfuse.callback import CallbackHandler`
- [x] **Inject db_id into trace metadata** — `server.py:58-61`: change `"metadata": req.tags` → `"metadata": {"db_id": req.db, **req.tags}` so every trace carries the database name without callers needing to set it
- [x] **Smoke test** — 10/10 ok, 0 errors, revise triggered on 3/10 (Q4 financial ITER=3, Q6 formula_1 ITER=2, Q8 superhero ITER=2, Q9 toxicology ITER=3)
- [x] **Verify waterfall in Langfuse UI** — waterfall confirmed in Langfuse UI
- [x] **Screenshot: `screenshots/langfuse_trace.png`** — captured
- [x] **Screenshot: `screenshots/langfuse_tags.png`** — captured

### What the fix looks like

**`agent/server.py` diff (two lines change):**
```python
# Line 27 — fix import
from langfuse.callback import CallbackHandler   # was: langfuse.langchain

# Line 60 — inject db_id into every trace
"metadata": {"db_id": req.db, **req.tags},     # was: req.tags
```

### Notes
- No custom span instrumentation needed — LangGraph auto-captures all node spans
- Tags useful for Phase 6: `db_id` (auto), `phase` (pass via `req.tags` from eval/load-test callers), `question_hash` (optional, add if filtering by question is useful during SLO debugging)
- Phase 4 is 5% of grade — scope is small by design

---

## Phase 5 — Evals 🔄

*Harness implemented and validated on Nebius AI (`Qwen/Qwen3-30B-A3B-Instruct-2507`). Grafana screenshot pending H100.*

### Analysis

`evals/run_eval.py` had two stub functions to implement. The helpers (`run_sql`, `canonicalize`, `matches`) and the main loop were already provided.

Key design decisions:
1. **Re-run all SQLs via `run_sql()`** — don't use `rows` from the agent response. Keeps gold and pred on the same execution path; also required for intermediate iterations where the agent only stores the SQL, not the rows.
2. **Carry-forward per iteration** — if the agent terminated at iter `j < k`, `iter_correct[k]` = `iter_correct[j]`. Reflects what would have been served at checkpoint `k`.
3. **Fail-safe on agent errors** — network/HTTP exceptions return a fully-failing record (all `iter_correct` = `False`, `correct` = `False`) so they count against the pass rate rather than being silently dropped.
4. **`correct` = final served answer** — `iter_correct[len(history) - 1]`, not the carry-forward at iter 2. This is what was actually returned to the caller.

### Tasks

- [x] **Implement `eval_one()`** — POST to agent (120 s timeout), run gold SQL, re-run each history SQL, carry-forward per-iteration correctness
- [x] **Implement `summarize()`** — aggregate `iter_correct[k]` for k=0,1,2; compute overall pass rate from `correct`; add `mean_iterations`
- [x] **Run baseline eval** — `uv run python evals/run_eval.py --out results/eval_baseline.json` (30 questions, Nebius AI API)
- [x] **Baseline results recorded** — see `results/eval_baseline.json`
- [ ] **Screenshot: `screenshots/grafana_eval_run.png`** — requires H100 + vLLM (no Prometheus metrics from hosted API)
- [ ] **Re-run on H100** — overwrite `results/eval_baseline.json` with vLLM results for grading

### Baseline results (Nebius AI API, Qwen3-30B-A3B-Instruct-2507)

```json
{
  "n": 30,
  "overall_pass_rate": 0.3667,
  "per_iter_pass_rate": {"0": 0.3333, "1": 0.3667, "2": 0.3667},
  "mean_iterations": 1.27
}
```

### Loop value analysis

- Revise triggered on ~27% of questions (~8/30, inferred from `mean_iterations = 1.27`)
- Loop fixed **1 question** (33.3% → 36.7%, +3.4pp)
- iter 1 == iter 2 → no second revise helped; gain was all from the first revise
- Fix rate on triggered revisions: ~12.5% (1 fix out of ~8 revisions)
- Root cause: verifier too permissive (accepts wrong-but-plausible results ~73% of the time), and revise tends to regenerate structurally similar SQL rather than rethinking the approach

### Notes
- Hosted API produces no Prometheus metrics — Grafana panels are flat during this run; Grafana screenshot must come from H100
- Pass rates from API run are likely representative of H100 numbers (same model), but graders expect the full vLLM stack

---

## Phase 1 — vLLM (H100 only) ⏳

*To be done after booking H100 slot.*

---

## Phase 2 — Grafana Dashboard (H100 only) ⏳

*Requires vLLM `/metrics` — build and validate on H100.*

---

## Phase 6 — SLO Tuning (H100 only) ⏳

---

## Phase 7 — Report ⏳

---

## Preparing to H100 (after local run)

### Before H100 (do now)

- [ ] **Fix `agent/server.py:27`** — wrong Langfuse import: `langfuse.langchain` → `langfuse.callback`. File was never saved after the fix noted in Phase 4 log.

- [ ] **Build out Grafana dashboard** (`infra/grafana/provisioning/dashboards/serving.json`) — currently only 2 panels. Add 6 more covering all 3 required categories:
  - **Latency**: E2E latency P50/P95/P99 + TTFT P50/P95
    - `histogram_quantile(0.95, rate(vllm:e2e_request_latency_seconds_bucket[1m]))`
    - `histogram_quantile(0.95, rate(vllm:time_to_first_token_seconds_bucket[1m]))`
  - **Throughput**: requests waiting (queue depth) + request completion rate
    - `vllm:num_requests_waiting`
    - `rate(vllm:e2e_request_latency_seconds_count[1m])`
    - `rate(vllm:prompt_tokens_total[1m])`
  - **KV cache**: `vllm:gpu_cache_usage_perc * 100`

- [ ] **Write tuned `scripts/start_vllm.sh`** — current script has zero optimization flags. Flags for Qwen3-30B-A3B (MoE, ~3B activated) on H100 80 GB:
  - `--dtype bfloat16` — H100 native format
  - `--max-model-len 8192` — covers 1.5-3K prompts + short SQL outputs; smaller than default leaves more KV slots
  - `--gpu-memory-utilization 0.95` — model weights ~16 GB bf16, ~60 GB left for KV cache
  - `--max-num-seqs 64` — MoE with 3B activated params batches cheaply
  - `--enable-chunked-prefill` — prevents long prefill bursts from blocking decode; critical for P95 under concurrent load
  - `--enable-automatic-prefix-caching` — many queries share the same DB schema prefix; KV reuse cuts TTFT on repeated DB calls
  - `--disable-log-requests` — reduces logging overhead at 10+ RPS

- [ ] **Create `REPORT.md` skeleton** — stub out all 7 sections so on H100 we fill in numbers, not structure.

---

### On H100 (ordered — do not skip steps)

| Step | Command / Action | Artifact |
|---|---|---|
| 1 | `git clone` + `uv sync` + `docker compose up -d` + forward 5 ports (3000, 9090, 3001, 8000, 8001) | — |
| 2 | `bash scripts/start_vllm.sh` — wait for model to load (~3-5 min), confirm `/health` responds | — |
| 3 | Manual curl 3-5 queries from `evals/eval_set.jsonl`, confirm SQL output looks reasonable | `screenshots/vllm_manual_query.png` |
| 4 | Start agent: `uvicorn agent.server:app --host 0.0.0.0 --port 8001` | — |
| 5 | Fire a few `/answer` requests, confirm all Grafana panels react to load | `screenshots/grafana_serving.png` |
| 6 | `uv run python evals/run_eval.py` (30 q × ~2 LLM calls ≈ 60 vLLM requests) — watch Grafana while running | `screenshots/grafana_eval_run.png`, `results/eval_baseline.json` |
| 7 | `uv run python load_test/driver.py --rps 10 --duration 300` — watch Grafana, note which metric moves first | `screenshots/grafana_before.png` |
| 8 | Diagnose bottleneck from dashboard, change **one** vLLM flag, restart vLLM | — |
| 9 | Re-run load test, confirm targeted metric moved and P95 latency followed | `screenshots/grafana_after.png` |
| 10 | `uv run python evals/run_eval.py --out results/eval_after_tuning.json` — verify quality survived | `results/eval_after_tuning.json` |
| 11 | Fill REPORT.md: numbers, iteration log ("saw X → hypothesized Y → changed Z → result W"), verdict | `REPORT.md` |
