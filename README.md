# Squad Scout

An AI agent for Fantasy Premier League. It answers questions like "Who should I captain this week?" or "Is Saka worth a points hit?" from real FPL data instead of guessing.

```
            HTTP / SSE                 gRPC :50051                 HTTPS
 client ───────────────► langgraph_service ─────────► fpl_data_service ─────► FPL API
          :8080 FastAPI    (LangGraph agent)            (Go + SQLite cache)
                                │
                                │ OpenAI-compatible HTTP :8000
                                ▼
                              vLLM (local model on GPU)
```

| Service | Language | Job | Port |
|---|---|---|---|
| `fpl_data_service` | Go | Downloads and caches FPL data, answers questions about it, checks transfers against FPL's squad rules. gRPC only. | 50051 |
| `langgraph_service` | Python | The agent: asks the model what to do, runs the tools it picks. HTTP for clients, gRPC client to Go. | 8080 |
| vLLM | Python | Runs `Qwen2.5-3B-Instruct-AWQ` with an OpenAI-compatible API | 8000 |

The contract between the two services is `proto/squadscout/v1/fpl.proto`. Edit it, then run `make proto` to regenerate both sides.

**More documentation:**

- [docs/architecture.md](docs/architecture.md): diagrams of the whole system and the data service (packages, data freshness, request flows, database, verifier, errors).
- [docs/walkthrough.md](docs/walkthrough.md): a beginner-friendly tour of the code, file by file.
- [CLAUDE.md](CLAUDE.md): conventions, gotchas and the task list.

## What the data service can do

| RPC | What it answers |
|---|---|
| `SearchPlayers` | Find players by name ("odegaard" finds Ødegaard): price, form, season stats, availability, injury news, FPL's expected points |
| `FindPlayers` | Transfer targets: filter by position, price and availability; sort by expected points, form, total points or points per game |
| `GetPlayerRecentForm` | A player's last few matches, with totals |
| `GetPlayerFixtures` | A player's next fixtures from his team's side, with difficulty, plus expected points for the next gameweek |
| `GetPlayerGameweekStats` | A player's stats in one gameweek |
| `GetFixtures` | A team's next fixtures, by team id or name ("Arsenal", "Man Utd", "ARS") |
| `GetTransferTrends` | The most transferred in or out players this gameweek |
| `GetManagerTeam` | A manager's squad by FPL team id: every player's outlook, bank, free transfers, chips left |
| `EvaluateTransfers` | Checks a set of transfers against every squad rule and reports the points hit, money left and expected points gained |
| `SuggestTransfers` | Wired up but **not implemented yet** (the optimizer) |

The agent currently uses only `SearchPlayers` and `GetFixtures`; giving it tools for the rest is the next step.

## Working setup

- Edit files from Windows (`D:\Projects\squad_scout`).
- Run **every** command from WSL at `/mnt/d/Projects/squad_scout`. Nothing gets installed on Windows.
- `.gitattributes` and `.editorconfig` force LF line endings. `make run-py` polls for file changes because edits made on Windows don't trigger Linux file-change events. For the same reason, VS Code sometimes shows a stale copy of a file changed from WSL: close and reopen the tab.

## One-time WSL setup

```bash
# 1. Base tools (sqlite3 and jq are handy for poking at the cache and FPL JSON)
sudo apt update && sudo apt install -y build-essential git curl golang-go sqlite3 jq

# 2. uv (manages Python and the Python deps)
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc

# 3. Optional: grpcurl, which is curl for gRPC and useful for calling the Go service
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# 4. Git: keep LF endings
git config --global core.autocrlf false

# 5. Project deps + generated code
cd /mnt/d/Projects/squad_scout
make setup
make test
```

You don't need to install `protoc` separately. `make proto` uses the copy bundled with the `grpcio-tools` Python package and generates both the Go and Python code.

vLLM lives in its own virtualenv (`~/.venvs/vllm`); the one-time install steps are at the top of `infrastructure/vllm/vllm_start.sh`.

## Running it (three WSL terminals)

```bash
make run-go     # terminal 1: Go gRPC server on :50051 (downloads FPL data on startup)
make run-py     # terminal 2: FastAPI on :8080, docs at http://localhost:8080/docs
make vllm       # terminal 3: vLLM on :8000; check it with: bash infrastructure/vllm/smoke_test.sh

make chat Q="How has Saka been playing?"
make chat-stream Q="How has Saka been playing?"   # also shows tool_start / tool_end events
```

`make chat` puts the question inside single quotes, so avoid apostrophes ("Arsenal's") in `Q`.

You can call the Go service directly, without the agent or the GPU:

```bash
make grpc-list
grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
grpcurl -plaintext -d '{"player_id":12,"last_n":3}' localhost:50051 squadscout.v1.FplDataService/GetPlayerRecentForm
grpcurl -plaintext -d '{"team_name":"Arsenal","next_n":3}' localhost:50051 squadscout.v1.FplDataService/GetFixtures
grpcurl -plaintext -d '{"team_id":6}' localhost:50051 squadscout.v1.FplDataService/GetManagerTeam
grpcurl -plaintext -d '{"team_id":6,"player_out_ids":[124],"player_in_ids":[12],"max_hit":4}' localhost:50051 squadscout.v1.FplDataService/EvaluateTransfers
```

grpcurl leaves out fields that are 0 or false, so a missing `valid` means `false`.

`make help` lists every command.

## Layout

```
proto/squadscout/v1/fpl.proto     gRPC contract (source of truth for both services)
fpl_data_service/
  cmd/api/main.go                 wiring, startup + timed refresh, graceful shutdown
  internal/handlers/              the 10 RPCs, one file per topic
  internal/refresh/               "is this data stale? then download and save it"
  internal/manager/               a manager's team: free transfers, chips, selling prices
  internal/squad/                 the verifier: FPL squad rules and transfer checks
  internal/optimizer/             transfer search (structure only, not implemented)
  internal/db/                    SQLite cache; schema.sql is embedded and versioned
  internal/fpl/                   FPL API client; testdata/ holds saved real responses
  internal/fpltest/               fake FPL API for tests
  internal/names/                 accent-insensitive name matching
  gen/                            generated, git-ignored
  data/fpl.db                     the cache, git-ignored
langgraph_service/
  src/server.py                   FastAPI: /health, /chat, /chat/stream (SSE)
  src/agent/graph.py              agent <-> tools loop, system prompt, nudge
  src/agent/state.py              graph state
  src/agent/tools.py              @tool functions = gRPC client calls
  src/config.py                   all addresses and settings (reads .env)
  src/squadscout/                 generated, git-ignored
infrastructure/vllm/              vllm_start.sh, smoke_test.sh
docs/                             architecture.md, walkthrough.md
```

## Status

- [x] Scaffold, gRPC contract, vLLM tool-calling smoke test
- [x] End to end: client → agent → Go → FPL data → answer
- [x] Real FPL data cached in SQLite, refreshed hourly; accent-insensitive player search
- [x] Player form, fixtures, gameweek stats, transfer trends, filtered player search
- [x] Manager teams (free transfers, chips, selling prices) and a transfer verifier
- [ ] Agent tools for the new RPCs, with compact output for the 4k-token model
- [ ] Transfer optimizer (`SuggestTransfers`)
- [ ] Eval set of 10-20 questions against the live stack
- [ ] Try `Qwen3-4B-AWQ`; streaming UI; resilience tests; docker-compose
