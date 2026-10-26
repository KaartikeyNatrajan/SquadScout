# Run every target from WSL, at the repo root.
# Recipes start with ">" instead of a tab, so Windows editors can't break them.
.RECIPEPREFIX = >
SHELL := /bin/bash

DATA_SVC := fpl_data_service
PY_SVC   := langgraph_service
PROTO    := proto/squadscout/v1/fpl.proto

# Files on /mnt/d don't emit Linux file-change events, so reloaders must poll.
export WATCHFILES_FORCE_POLLING := true

.DEFAULT_GOAL := help
.PHONY: help setup proto run-data run-py vllm chat chat-stream grpc-list test fmt clean

help: ## Show this help
> @grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## One-time: install deps and generate the gRPC code
> cd $(DATA_SVC) && uv sync
> cd $(PY_SVC) && uv sync
> [ -f $(PY_SVC)/.env ] || cp $(PY_SVC)/.env.example $(PY_SVC)/.env
> $(MAKE) proto

proto: ## Regenerate the Python gRPC code (both services) after editing the .proto
> cd $(DATA_SVC) && uv run python -m grpc_tools.protoc -I ../proto \
>   --python_out=src --pyi_out=src --grpc_python_out=src ../$(PROTO)
> cd $(DATA_SVC) && uv run python -m grpc_tools.protoc -I ../proto \
>   --python_out=../$(PY_SVC)/src --pyi_out=../$(PY_SVC)/src --grpc_python_out=../$(PY_SVC)/src ../$(PROTO)
> @echo "generated: $(DATA_SVC)/src/squadscout/ and $(PY_SVC)/src/squadscout/"

run-data: ## Start the data service (gRPC) on :50051
> cd $(DATA_SVC) && uv run python -m fpl_data

run-py: ## Start FastAPI on :8080 with auto-reload
> cd $(PY_SVC) && uv run uvicorn server:app --app-dir src --host 0.0.0.0 --port 8080 --reload --reload-dir src

vllm: ## Start vLLM on :8000 (see infrastructure/vllm/vllm_start.sh)
> bash infrastructure/vllm/vllm_start.sh

chat: ## Send a test question: make chat Q="How is Saka doing?"
> curl -s localhost:8080/chat -H 'content-type: application/json' \
>   -d '{"message": "$(or $(Q),How has Saka been playing?)"}' | python3 -m json.tool

chat-stream: ## Same, streamed as server-sent events
> curl -sN localhost:8080/chat/stream -H 'content-type: application/json' \
>   -d '{"message": "$(or $(Q),How has Saka been playing?)"}'

grpc-list: ## List gRPC methods (needs grpcurl)
> grpcurl -plaintext localhost:50051 list squadscout.v1.FplDataService

test: ## Run all tests (no GPU, servers or network needed)
> cd $(DATA_SVC) && uv run pytest -q
> cd $(PY_SVC) && uv run pytest -q

fmt: ## Format and lint both Python services
> cd $(DATA_SVC) && uv run ruff format src tests && uv run ruff check --fix src tests
> cd $(PY_SVC) && uv run ruff format src tests && uv run ruff check --fix src tests

clean: ## Delete generated code and local data
> rm -rf $(DATA_SVC)/src/squadscout $(PY_SVC)/src/squadscout $(DATA_SVC)/data
