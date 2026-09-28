# Squad Scout

An AI agent for Fantasy Premier League. It answers questions like "Should I keep Saka for the next 3 gameweeks?" by pulling real FPL data instead of guessing.

```
            HTTP / SSE                 gRPC :50051                 HTTPS
 client ───────────────► langgraph_service ─────────► fpl_data_service ─────► FPL API
          :8080 FastAPI    (LangGraph agent)            (Go + SQLite cache)
                                │
                                │ OpenAI-compatible HTTP :8000
                                ▼
                              vLLM (local model on GPU)
```

| Service | Language | Talks | Port |
|---|---|---|---|
| `fpl_data_service` | Go | gRPC server (no HTTP) | 50051 |
| `langgraph_service` | Python | HTTP for clients, gRPC client to Go | 8080 |
| vLLM | Python | OpenAI-compatible HTTP | 8000 |

The contract between the two services is `proto/squadscout/v1/fpl.proto`. Edit it, then run `make proto` to regenerate both sides.

## Working setup

- Edit files from Windows (`D:\Projects\squad_scout`).
- Run **every** command from WSL at `/mnt/d/Projects/squad_scout`. Nothing gets installed on Windows.
- `.gitattributes` and `.editorconfig` force LF line endings. `make run-py` polls for file changes because edits made on Windows don't trigger Linux file-change events.

## One-time WSL setup

```bash
# 1. Base tools
sudo apt update && sudo apt install -y build-essential git curl golang-go

# 3. uv (manages Python and the Python deps)
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc

# 4. Optional: grpcurl, which is curl for gRPC and useful for poking the Go service
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# 5. Git: keep LF endings and init the repo
git config --global core.autocrlf false
cd /mnt/d/Projects/squad_scout
git init -b main

# 6. Project deps + generated code
make setup
make test
```

You don't need to install `protoc` separately. `make proto` uses the copy bundled with the `grpcio-tools` Python package and generates both the Go and Python code.

## Running it (three WSL terminals)

```bash
make run-go     # terminal 1: Go gRPC server on :50051
make run-py     # terminal 2: FastAPI on :8080, docs at http://localhost:8080/docs
make vllm       # terminal 3: vLLM on :8000 (one-time install steps are in the script)

make chat Q="How has Saka been playing?"
make chat-stream Q="How has Saka been playing?"
make grpc-list
```

Until vLLM is running, `/chat` fails at the LLM call. You can still test the Go side on its own:

```bash
grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
```

`make help` lists every command.

## Layout

```
proto/squadscout/v1/fpl.proto     gRPC contract (source of truth for both services)
fpl_data_service/
  cmd/api/main.go                 gRPC server: health check, reflection, graceful shutdown
  internal/handlers/              gRPC method implementations (the "routes")
  internal/fpl/                   FPL API client
  internal/db/                    SQLite cache; schema.sql is embedded and applied on startup
  gen/                            generated, git-ignored
langgraph_service/
  src/server.py                   FastAPI: /health, /chat, /chat/stream (SSE)
  src/agent/graph.py              agent <-> tools loop
  src/agent/state.py              graph state
  src/agent/tools.py              @tool functions = gRPC client calls
  src/config.py                   all addresses and settings (reads .env)
  src/squadscout/                 generated, git-ignored
infrastructure/vllm/vllm_start.sh
```

## Status

- [x] Scaffold, gRPC contract, stub `SearchPlayers` returning canned data
- [ ] Load `bootstrap-static` into SQLite; real `SearchPlayers` with accent-insensitive names
- [ ] `GetFixtures`, `GetPlayerGameweekStats`
- [ ] vLLM tool-calling smoke test
- [ ] End-to-end: "How has Saka been playing?"
- [ ] Streaming UI, resilience tests, docker-compose
