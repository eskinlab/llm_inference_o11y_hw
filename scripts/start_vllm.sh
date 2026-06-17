#!/usr/bin/env bash
#
# Start vLLM with your chosen configuration.
# Reference: https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html
#
# Usage:
#   bash scripts/start_vllm.sh           # remote (H100, default)
#   bash scripts/start_vllm.sh remote    # same as above
#   bash scripts/start_vllm.sh local     # conservative defaults for consumer GPU
#
# Override model via env var:
#   VLLM_MODEL=Qwen/Qwen3-30B-A3B-FP8 bash scripts/start_vllm.sh local

set -euo pipefail

PROFILE="${1:-remote}"
MODEL="${VLLM_MODEL:-Qwen/Qwen3-30B-A3B-Instruct-2507}"

case "$PROFILE" in
  remote)
    # H100 80 GB — model weights ~60 GB bf16, ~20 GB left for KV cache
    exec uv run python -m vllm.entrypoints.openai.api_server \
        --model "$MODEL" \
        --host 0.0.0.0 \
        --port 8000 \
        --dtype bfloat16 \
        --max-model-len 8192 \
        --gpu-memory-utilization 0.95 \
        --max-num-seqs 64 \
        --enable-chunked-prefill \
        --enable-automatic-prefix-caching \
        --disable-log-requests
    ;;

  local)
    # Consumer GPU — use a quantized model if weights don't fit, e.g.:
    #   VLLM_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507-AWQ bash scripts/start_vllm.sh local
    exec uv run python -m vllm.entrypoints.openai.api_server \
        --model "$MODEL" \
        --host 0.0.0.0 \
        --port 8000 \
        --dtype auto \
        --max-model-len 4096 \
        --gpu-memory-utilization 0.85 \
        --max-num-seqs 16 \
        --enable-chunked-prefill \
        --enable-automatic-prefix-caching \
        --disable-log-requests
    ;;

  *)
    echo "Unknown profile: $PROFILE. Use 'local' or 'remote'." >&2
    exit 1
    ;;
esac
