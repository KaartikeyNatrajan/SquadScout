#!/usr/bin/env bash
# Boot vLLM with an OpenAI-compatible API on :8000.
#
# One-time install (in WSL, in its own venv, NOT inside langgraph_service):
#   uv venv ~/.venvs/vllm --python 3.12
#   source ~/.venvs/vllm/bin/activate && uv pip install vllm
#
# Override any setting per run, e.g.:
#   VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct VLLM_TOOL_PARSER=hermes make vllm
set -euo pipefail

VENV="${VLLM_VENV:-$HOME/.venvs/vllm}"
[[ -f "$VENV/bin/activate" ]] && source "$VENV/bin/activate"

MODEL="${VLLM_MODEL:-Qwen/Qwen2.5-7B-Instruct-AWQ}"   # 4-bit, fits ~8-12 GB VRAM
SERVED_NAME="${VLLM_SERVED_NAME:-squad-scout-llm}"    # must match LLM_MODEL in langgraph_service/.env
MAX_LEN="${VLLM_MAX_MODEL_LEN:-8192}"                 # lower this first if you hit CUDA OOM
GPU_UTIL="${VLLM_GPU_UTIL:-0.85}"                     # share of VRAM vLLM may claim
TOOL_PARSER="${VLLM_TOOL_PARSER:-hermes}"             # hermes for Qwen, llama3_json for Llama 3.x
PORT="${VLLM_PORT:-8000}"

nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader || {
  echo "nvidia-smi failed: GPU not visible inside WSL" >&2; exit 1; }

# Without --enable-auto-tool-choice + --tool-call-parser, tool calls come back
# as plain text and the LangGraph agent never routes to the tools node.
exec vllm serve "$MODEL" \
  --served-model-name "$SERVED_NAME" \
  --host 0.0.0.0 --port "$PORT" \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization "$GPU_UTIL" \
  --enable-auto-tool-choice \
  --tool-call-parser "$TOOL_PARSER"
