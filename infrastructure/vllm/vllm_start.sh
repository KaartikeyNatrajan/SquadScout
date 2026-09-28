#!/usr/bin/env bash
# Boot vLLM with an OpenAI-compatible API on :8000.
#
# Defaults are tuned for a 6 GB Turing card (RTX 2060) with ~4-5 GB free.
#
# One-time install (in WSL, in its own venv, NOT inside langgraph_service):
#   uv venv ~/.venvs/vllm --python 3.12
#   source ~/.venvs/vllm/bin/activate && uv pip install vllm
#
# Override any setting per run, e.g.:
#   VLLM_MODEL=Qwen/Qwen3-4B-AWQ VLLM_MAX_MODEL_LEN=3072 make vllm
set -euo pipefail

VENV="${VLLM_VENV:-$HOME/.venvs/vllm}"
[[ -f "$VENV/bin/activate" ]] && source "$VENV/bin/activate"

MODEL="${VLLM_MODEL:-Qwen/Qwen2.5-3B-Instruct-AWQ}"  # ~2 GB of 4-bit weights
SERVED_NAME="${VLLM_SERVED_NAME:-squad-scout-llm}"   # must match LLM_MODEL in langgraph_service/.env
MAX_LEN="${VLLM_MAX_MODEL_LEN:-4096}"                # prompt + tool results + answer; lower first on OOM
GPU_UTIL="${VLLM_GPU_UTIL:-0.75}"                    # share of TOTAL VRAM vLLM may claim (Windows uses the rest)
MAX_SEQS="${VLLM_MAX_NUM_SEQS:-4}"                   # concurrent requests; small = less memory reserved
TOOL_PARSER="${VLLM_TOOL_PARSER:-hermes}"            # hermes for Qwen, llama3_json for Llama 3.x
PORT="${VLLM_PORT:-8000}"

nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader || {
  echo "nvidia-smi failed: GPU not visible inside WSL" >&2; exit 1; }

# --dtype half        Turing has no bfloat16 support, so use float16.
# --enforce-eager     skips CUDA graph capture: slower, but saves ~0.5 GB of VRAM.
# --enable-auto-tool-choice + --tool-call-parser
#                     without these, tool calls come back as plain text and the
#                     LangGraph agent never routes to the tools node.
exec vllm serve "$MODEL" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 --port "$PORT" \
  --dtype half \
  --max-model-len "$MAX_LEN" \
  --max-num-seqs "$MAX_SEQS" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --enforce-eager \
  --enable-auto-tool-choice \
  --tool-call-parser "$TOOL_PARSER"
