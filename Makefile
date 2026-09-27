# Run every target from WSL, at the repo root.
# Recipes start with ">" instead of a tab, so Windows editors can't break them.
.RECIPEPREFIX = >
SHELL := /bin/bash

GO_SVC   := fpl_data_service
PY_SVC   := langgraph_service
PROTO    := proto/squadscout/v1/fpl.proto
GOBIN    := $(shell go env GOPATH 2>/dev/null)/bin

# Files on /mnt/d don't emit Linux file-change events, so reloaders must poll.
export WATCHFILES_FORCE_POLLING := true

.DEFAULT_GOAL := help
.PHONY: help setup tools proto run-go run-py vllm chat chat-stream grpc-list test fmt clean

help: ## Show this help
> @grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: tools ## One-time: install deps, generate proto code, tidy Go module
> cd $(PY_SVC) && uv sync
> [ -f $(PY_SVC)/.env ] || cp $(PY_SVC)/.env.example $(PY_SVC)/.env
> $(MAKE) proto
> cd $(GO_SVC) && go mod tidy

tools: ## Install protoc plugins for Go (into ~/go/bin)
> go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
> go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

proto: ## Regenerate Go + Python code from proto/ (after editing the .proto)
> cd $(PY_SVC) && PATH="$(GOBIN):$$PATH" uv run python -m grpc_tools.protoc \
>   -I ../proto \
>   --go_out=../$(GO_SVC) --go_opt=module=squad_scout/$(GO_SVC) \
>   --go-grpc_out=../$(GO_SVC) --go-grpc_opt=module=squad_scout/$(GO_SVC) \
>   --python_out=src --pyi_out=src --grpc_python_out=src \
>   ../$(PROTO)
> @echo "generated: $(GO_SVC)/gen/ and $(PY_SVC)/src/squadscout/"

run-go: ## Start the Go gRPC server on :50051
> cd $(GO_SVC) && go run ./cmd/api

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

test: ## Run Go and Python tests
> cd $(GO_SVC) && go test ./...
> cd $(PY_SVC) && uv run pytest -q

fmt: ## Format Go and Python code
> cd $(GO_SVC) && gofmt -w .
> cd $(PY_SVC) && uv run ruff format src tests && uv run ruff check --fix src tests

clean: ## Delete generated code and local data
> rm -rf $(GO_SVC)/gen $(PY_SVC)/src/squadscout $(GO_SVC)/data
