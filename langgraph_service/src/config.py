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

    # Go data service (gRPC)
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

    # Claude (Anthropic API). Used when LLM_PROVIDER=anthropic.
    # SecretStr keeps the key out of logs and error messages. Left empty, the
    # Anthropic client looks for an ANTHROPIC_API_KEY environment variable itself.
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5-5"
    # How hard Claude thinks before answering: more effort = better answers on
    # hard questions, but slower and more tokens (= cost).
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    anthropic_max_tokens: int = 16000  # cap on one reply (thinking included)

    # The longest one model call may take before it fails with an error. Without
    # it, a model that isn't running (vLLM not started) makes /chat hang for many
    # minutes instead of failing. Generous, because Claude may think before answering.
    llm_timeout_s: float = 120.0

    # Graph safety valve: max agent<->tool hops before LangGraph aborts
    graph_recursion_limit: int = 12


# Created once at import time; other modules do `from config import settings`.
settings = Settings()
