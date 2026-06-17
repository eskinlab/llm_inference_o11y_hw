# LLM Inference + Observability — Report

Model: `Qwen/Qwen3-30B-A3B-Instruct-2507` · Hardware: 1× H100 80 GB · Stack: vLLM + LangGraph + Langfuse + Prometheus/Grafana

---

## 1. Serving Configuration (Phase 1)

| Flag | Value | Rationale |
|---|---|---|
| `--dtype` | `bfloat16` | <!-- H100 native format; no accuracy loss vs fp32 for inference --> |
| `--max-model-len` | `8192` | <!-- Prompts are 1.5-3K tokens + short SQL output; keeping well under default (32K) allocates more KV slots to concurrent requests --> |
| `--gpu-memory-utilization` | `0.95` | <!-- Model weights ~16 GB bf16 on 80 GB H100; 0.95 leaves ~1 GB safety margin and maximises KV cache budget --> |
| `--max-num-seqs` | `64` | <!-- MoE activates only ~3B params per token; cheap per-sequence cost allows large batch size without OOM --> |
| `--enable-chunked-prefill` | — | <!-- Prevents a long prompt from monopolising the prefill step and spiking TTFT for concurrently queued requests; critical for P95 under 10 RPS --> |
| `--enable-automatic-prefix-caching` | — | <!-- Each DB schema is ~1.5K tokens and is shared across many questions; KV reuse eliminates redundant prefill after the first request per DB --> |
| `--disable-log-requests` | — | <!-- Removes per-request stdout overhead at high throughput --> |

<!-- TODO on H100: paste final start_vllm.sh command here -->

---

## 2. Observability Dashboard (Phase 2)

Dashboard: `infra/grafana/provisioning/dashboards/serving.json`

Three categories of panels:

**Latency** — answers "is it slow, and where?"
- E2E request latency P50 / P95 / P99 (`vllm:e2e_request_latency_seconds_bucket`)
- Time to first token P50 / P95 (`vllm:time_to_first_token_seconds_bucket`)

**Throughput** — answers "how loaded is the system?"
- Requests running / waiting in queue (`vllm:num_requests_running`, `vllm:num_requests_waiting`)
- Request completion rate (`rate(vllm:e2e_request_latency_seconds_count[1m])`)
- Generated tokens / sec (`rate(vllm:generation_tokens_total[1m])`)
- Prompt tokens / sec (`rate(vllm:prompt_tokens_total[1m])`)

**KV cache** — answers "do we have headroom for more concurrency?"
- GPU cache usage % (`vllm:gpu_cache_usage_perc * 100`)

<!-- TODO on H100: confirm all panels react under load; add any extra panels if a metric proves more useful -->

---

## 3. Agent Design (Phase 3)

Graph: `START → attach_schema → generate_sql → execute → verify → (revise → execute → verify)* → END`

Loop cap: `MAX_ITERATIONS = 3` total generate + revise calls.

The `verify` node asks the model for `{"ok": bool, "issue": str}` and marks `ok=false` when:
- The result is an SQL error
- Zero rows returned but the question implies specific entities exist
- Returned columns clearly do not answer the question

The `revise` node receives the failing SQL, its execution result, and the verifier's one-line issue description, then produces a corrected query.

During local smoke testing (10 questions, Nebius AI API): revise triggered on 3/10 questions (iterations 2-3), confirming the loop fires on real failure cases.

---

## 4. Baseline Eval Results (Phase 5)

Eval method: execution accuracy — run agent SQL and gold SQL against the same SQLite DB, compare canonicalized row sets (sorted, cells stringified, None → "").

**Baseline results** (Nebius AI API, `Qwen3-30B-A3B-Instruct-2507`, 30 questions):

| Metric | Value |
|---|---|
| Overall pass rate | 36.7% |
| Pass rate @ iter 0 (generate only) | 33.3% |
| Pass rate @ iter 1 (after 1st revise) | 36.7% |
| Pass rate @ iter 2 (after 2nd revise) | 36.7% |
| Mean iterations | 1.27 |

<!-- TODO on H100: re-run and overwrite with vLLM numbers -->

**Loop value:** iter 0 → iter 1 gained +3.4 pp; iter 1 → iter 2 gained 0. The loop fixed 1 of ~8 triggered revisions (~12.5% fix rate). The verifier is too permissive — it accepts wrong-but-plausible results ~73% of the time — and revise tends to regenerate structurally similar SQL rather than rethinking the approach.

---

## 5. SLO Diagnosis & Iteration (Phase 6)

**Target SLO:** P95 end-to-end agent latency < 5 s at 10+ RPS sustained over 5 minutes.

**Baseline load test** (`--rps 10 --duration 300`):

| Metric | Baseline |
|---|---|
| Achieved RPS | <!-- TODO --> |
| P50 latency | <!-- TODO --> |
| P95 latency | <!-- TODO --> |
| P99 latency | <!-- TODO --> |
| Timeouts | <!-- TODO --> |

<!-- TODO on H100: fill after first load test run; screenshot → screenshots/grafana_before.png -->

**Iteration log:**

| # | Saw | Hypothesised | Changed | Result |
|---|---|---|---|---|
| 1 | <!-- metric that moved first --> | <!-- hypothesis --> | <!-- one flag / change --> | <!-- P95 before → after --> |
| 2 | | | | |
| 3 | | | | |

<!-- TODO on H100: fill each row as you iterate; screenshot after the change that moved the needle → screenshots/grafana_after.png -->

**Final numbers** (post-tuning):

| Metric | Final | SLO |
|---|---|---|
| P95 latency | <!-- TODO --> | < 5 s |
| Achieved RPS | <!-- TODO --> | ≥ 10 |
| SLO verdict | <!-- HIT / MISSED (gap: X s) --> | |

**Post-tuning eval** (`results/eval_after_tuning.json`):

| Metric | Baseline | Post-tuning |
|---|---|---|
| Overall pass rate | 36.7% | <!-- TODO --> |
| Quality survived? | — | <!-- YES / NO + commentary --> |

---

## 6. Agent Value

<!-- One paragraph. Did the verify→revise loop actually help? By how much? Cite per-iteration pass rates.
     Be honest: if the gain was small, say so and explain why (e.g. verifier too permissive).
     TODO on H100: write after seeing final eval numbers. -->

---

## 7. What I'd Do With More Time

<!-- Be specific — "add Kubernetes" does not count.
     Examples of valid directions:
     - Stronger verifier prompt: current false-accept rate ~73%; a chain-of-thought verify step or
       a separate schema-grounding check (do returned columns exist in the schema?) would catch more errors.
     - Schema pruning: sending the full schema for each DB wastes tokens on irrelevant tables;
       a table-selector step before generate_sql would shorten prompts and cut TTFT.
     - Speculative decoding: SQL outputs are short and structured; draft tokens from a small model
       could increase generation throughput without touching quality.
     - Structured output / grammar sampling: constrain vLLM output to valid SQL tokens to eliminate
       malformed-SQL errors and remove the need for regex extraction in _extract_sql.
     TODO: fill in 3-4 specific bullets. -->
