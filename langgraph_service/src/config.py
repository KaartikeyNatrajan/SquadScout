"""Service configuration, read from environment variables or langgraph_service/.env.

Every address lives here so switching from localhost to docker-compose service
names later is a config change, not a code change.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


# pydantic-settings fills each field from, in order of priority: an environment
# variable with the same name in UPPER CASE (e.g. FPL_GRPC_ADDR), then the .env
# file, then the default written here. It also converts types ("5" -> 5.0).
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Go data service (gRPC)
    fpl_grpc_addr: str = "localhost:50051"
    grpc_timeout_s: float = 5.0

    # vLLM (OpenAI-compatible API)
    llm_base_url: str = "http://localhost:8000/v1"
    llm_api_key: str = "EMPTY"  # vLLM ignores it, but the OpenAI client requires one
    llm_model: str = "squad-scout-llm"  # must match vLLM --served-model-name
    llm_temperature: float = 0.0  # 0 = always pick the likeliest token (repeatable output)

    # Graph safety valve: max agent<->tool hops before LangGraph aborts
    graph_recursion_limit: int = 12


# Created once at import time; other modules do `from config import settings`.
settings = Settings()
