# LLM Inference + Observability — Report

Model: `Qwen/Qwen3-30B-A3B-Instruct-2507` · Hardware: 1× H100 80 GB · Stack: vLLM + LangGraph + Langfuse + Prometheus/Grafana

---

## 1. Serving Configuration (Phase 1)

| Flag | Value | Rationale |
|---|---|---|
| `--dtype` | `bfloat16` | H100 native format; no accuracy loss vs fp32 for inference |
| `--max-model-len` | `8192` | Prompts are 1.5-3K tokens + short SQL output; keeping well under default (32K) allocates more KV slots to concurrent requests |
| `--gpu-memory-utilization` | `0.95` | Model weights ~16 GB bf16 on 80 GB H100; 0.95 leaves ~1 GB safety margin and maximises KV cache budget |
| `--max-num-seqs` | `128` | MoE activates only ~3B params per token; cheap per-sequence cost allows large batch size without OOM; tuned up from 64 after load test showed it halved P95 latency (117s → 66s) and reduced timeouts from 423 to 9 |
| `--enable-chunked-prefill` | — | Prevents a long prompt from monopolising the prefill step and spiking TTFT for concurrently queued requests; critical for P95 under 10 RPS |
| `--disable-log-requests` | — | Removes per-request stdout overhead at high throughput |

Note: `--enable-automatic-prefix-caching` was removed — enabled by default in vLLM 0.10.x.

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

All panels confirmed reacting under load on H100 (see `screenshots/grafana_serving_1.png`, `screenshots/grafana_serving_2.png`).

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

**Baseline results** (H100, vLLM, `Qwen3-30B-A3B-Instruct-2507`, 30 questions):

| Metric | Value |
|---|---|
| Overall pass rate | 30.0% |
| Pass rate at iter 0 (generate only) | 30.0% |
| Pass rate at iter 1 (after 1st revise) | 30.0% |
| Pass rate at iter 2 (after 2nd revise) | 30.0% |
| Mean iterations | 1.27 |

**Loop value:** all three iterations identical at 30% — the revise loop triggered on ~27% of questions (inferred from mean_iterations=1.27) but fixed zero of them. The verifier is firing but revise is regenerating structurally similar SQL rather than rethinking the approach. Net gain from the loop: 0 pp on H100.

---

## 5. SLO Diagnosis & Iteration (Phase 6)

**Target SLO:** P95 end-to-end agent latency < 5 s at 10+ RPS sustained over 5 minutes.

**Baseline load test** (`--rps 10 --duration 300`, `--max-num-seqs 64`):

| Metric | Baseline |
|---|---|
| Achieved RPS | 8.33 |
| P50 latency | 54.2s |
| P95 latency | 116.8s |
| P99 latency | 119.8s |
| Timeouts | 423 / 3000 (14%) |

Screenshots: `screenshots/grafana_before_1.png`, `screenshots/grafana_before_2.png`

**Iteration log:**

| # | Saw | Hypothesised | Changed | Result |
|---|---|---|---|---|
| 1 | P95=117s, requests_waiting=0, 423 timeouts | vLLM queue is empty — bottleneck is agent server piling up 2-3 sequential LLM calls (~4s each) per request; increasing batch size may reduce per-call latency | `--max-num-seqs 64 → 128` | P95: 117s → 66s, timeouts: 423 → 9 |

Screenshots: `screenshots/grafana_after_1.png`, `screenshots/grafana_after_2.png`

**Root cause:** The SLO requires P95 < 5s but each agent run chains 2 sequential LLM calls (~4s each on H100) = minimum ~8s per request with zero queuing. No vLLM configuration change can close this gap — it is architectural.

**Final numbers** (post-tuning, `--max-num-seqs 128`):

| Metric | Final | SLO |
|---|---|---|
| Achieved RPS | 8.33 | ≥ 10 |
| P50 latency | 37.7s | — |
| P95 latency | 66.4s | < 5s |
| P99 latency | 76.6s | — |
| Timeouts | 9 / 3000 (0.3%) | — |
| SLO verdict | **MISSED** (gap: +61.4s on P95) | |

**Post-tuning eval** (`results/eval_after_tuning.json`):

| Metric | Baseline | Post-tuning |
|---|---|---|
| Overall pass rate | 30.0% | 33.3% |
| Quality survived? | — | YES — slight improvement (+3.3pp), likely noise at n=30 |

---

## 6. Agent Value

The verify→revise loop triggered on ~27% of questions (mean_iterations=1.27) but produced zero net improvement on H100: pass rate was 30.0% at iter 0, 1, and 2. The verifier is firing on real failures but the revise node regenerates structurally similar SQL rather than rethinking the approach — the fix rate on triggered revisions was 0%. The loop adds 2 extra LLM calls per triggered question (~27% of traffic) and increases latency with no quality gain on this eval set. The architecture is sound — the gap is in the prompts: the verifier needs stronger failure categorization and the revise prompt needs to explicitly forbid repeating the same approach.

---

## 7. What I'd Do With More Time

- **Stronger verifier prompt:** add chain-of-thought reasoning and explicit schema grounding (do returned columns exist in the schema?) to cut the ~100% false-accept rate on H100 and make revisions actually fire on the right failures.
- **Revise prompt rethink instruction:** explicitly tell the revise node "do not repeat the same JOIN/WHERE structure — try a different approach." Currently it regenerates near-identical SQL, which is why the fix rate is 0%.
- **Schema pruning:** send only tables relevant to the question instead of the full DB schema (~1.5K tokens). A lightweight table-selector step before generate_sql would cut prompt tokens by ~50% and reduce TTFT significantly — directly attacking the SLO gap.
- **Structured output / grammar sampling:** constrain vLLM output to valid SQL tokens using `--guided-decoding-backend` to eliminate malformed-SQL errors and remove the regex extraction fallback in `_extract_sql`.
