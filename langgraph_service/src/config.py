"""Service configuration, read from environment variables or langgraph_service/.env.

Every address lives here so switching from localhost to docker-compose service
names later is a config change, not a code change.
"""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# pydantic-settings fills each field from, in order of priority: an environment
# variable with the same name in UPPER CASE (e.g. FPL_GRPC_ADDR), then the .env
# file, then the default written here. It also converts types ("5" -> 5.0).
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The data service (gRPC)
    fpl_grpc_addr: str = "localhost:50051"
    grpc_timeout_s: float = 5.0

    # Which model answers: "vllm" (local, settings below) or "anthropic" (Claude
    # over the Anthropic API, settings further down).
    llm_provider: Literal["vllm", "anthropic"] = "vllm"

    # vLLM (OpenAI-compatible API)
    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "EMPTY"  # vLLM ignores it, but the OpenAI client requires one
    llm_model: str = "squad-scout-llm"  # must match vLLM --served-model-name
    llm_temperature: float = 0.0  # 0 = always pick the likeliest token (repeatable output)
    # How many tokens of conversation (system prompt + messages) we send to vLLM at
    # most; older tool results are trimmed to fit (agent/trim.py). vLLM's window
    # is 4,096 tokens and must also hold the tool list (~600) and the answer, so
    # this leaves about 1,000 for those. Lower it if vLLM reports a too-long prompt.
    vllm_context_budget_tokens: int = 2500

    # Claude (Anthropic API). Used when LLM_PROVIDER=anthropic.
    # SecretStr keeps the key out of logs and error messages. Left empty, the
    # Anthropic client looks for an ANTHROPIC_API_KEY environment variable itself.
    anthropic_api_key: SecretStr = SecretStr("")
    # The cheapest current Claude model by default: the same simple question cost
    # $0.0006 on Haiku 5.5 at low effort and $0.025 (40x) on Opus 5.5 at medium.
    # Pick a bigger model deliberately, in .env, after comparing on the eval set.
    anthropic_model: str = "claude-haiku-5-5"
    # How hard Claude thinks before answering: more effort = better answers on
    # hard questions, but slower and more tokens (= cost).
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    anthropic_max_tokens: int = 16000  # cap on one reply (thinking included)
    # Once a request's input passes this many tokens, Anthropic clears old tool
    # results on its side (keeping the newest few). High enough that ordinary
    # conversations never reach it: clearing also invalidates the prompt cache
    # from that point on, so it should happen rarely and save a lot when it does.
    claude_clear_tool_results_at_tokens: int = 20000

    # The longest one model call may take before it fails with an error. Without
    # it, a model that isn't running (vLLM not started) makes /chat hang for many
    # minutes instead of failing. Generous, because Claude may think before answering.
    llm_timeout_s: float = 120.0

    # Graph safety valve: max agent<->tool hops before LangGraph aborts
    graph_recursion_limit: int = 12


# Created once at import time; other modules do `from config import settings`.
settings = Settings()
