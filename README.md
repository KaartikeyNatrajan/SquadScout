# Squad Scout

An AI agent for Fantasy Premier League. It answers questions like "Who should I captain this week?" or "Is Saka worth a points hit?" from real FPL data instead of guessing.

```
            HTTP / SSE                 gRPC :50051                 HTTPS
 client ───────────────► langgraph_service ─────────► fpl_data_service ─────► FPL API
          :8080 FastAPI    (LangGraph agent)            (Python + SQLite cache)
                                │
                                │ OpenAI-compatible HTTP :8000
                                ▼
                              vLLM (local model on GPU)
```

| Service | Job | Port |
|---|---|---|
| `fpl_data_service` | Downloads and caches FPL data, answers questions about it, checks transfers against FPL's squad rules. gRPC only. | 50051 |
| `langgraph_service` | The agent: asks the model what to do, runs the tools it picks. HTTP for clients, gRPC client to the data service. | 8080 |
| vLLM | Runs `Qwen2.5-3B-Instruct-AWQ` with an OpenAI-compatible API | 8000 |

Everything is Python (each service is its own `uv` project). The contract between the two services is `proto/squadscout/v1/fpl.proto`. Edit it, then run `make proto` to regenerate the code on both sides. (The data service was first written in Go; that version is kept in [legacy/](legacy/) for reference.)

**More documentation:**

- [docs/architecture.md](docs/architecture.md): diagrams of the whole system and the data service (packages, data freshness, request flows, database, verifier, errors).
- [docs/walkthrough.md](docs/walkthrough.md): a beginner-friendly tour of the code, file by file.
- [CLAUDE.md](CLAUDE.md): conventions, gotchas and the task list.

## What the data service can do

| RPC | What it answers |
|---|---|
| `SearchPlayers` | Find players by name ("odegaard" finds Ødegaard): price, form, season stats, availability, injury news, FPL's expected points |
| `FindPlayers` | Transfer targets: filter by position, price and availability; sort by expected points, form, total points or points per game. Each comes with his next fixtures and a **projection** of his points over the next 5 gameweeks |
| `GetPlayerRecentForm` | A player's last few gameweeks (opponent, score, minutes, points, xG…), with totals |
| `GetPlayerFixtures` | A player's next fixtures from his team's side, with difficulty, plus expected points for the next gameweek |
| `GetPlayerGameweekStats` | A player's stats in one gameweek |
| `GetFixtures` | A team's next fixtures, by team id or name ("Arsenal", "Man Utd", "ARS") |
| `GetTransferTrends` | The most transferred in or out players this gameweek, with a plain-word explanation of the crowd's behaviour |
| `GetManagerTeam` | A manager's squad by FPL team id: every player's outlook and projection, selling prices, bank, free transfers, chips left |
| `EvaluateTransfers` | Checks a set of transfers against every squad rule and reports the points hit, money left, and the projected gain over several gameweeks. The manager can say what their squad, bank and free transfers are *now*, since FPL only shows the last deadline |
| `SuggestTransfers` | The best 1-3 transfers: searches the options, checks each against the squad rules, and ranks them by the projected gain in the best starting eleven minus the points hit |

**What other managers are doing.** Every player comes with transfer sentiment: net transfers as a share of all managers, his rank among the most bought and sold, the last 24 hours (once the service has that much history), a sentence like *"heavily bought: net +3.15% of all managers this gameweek (the most bought player)"*, and whether his price is about to move (*"likely to rise within 2 price updates"*).

**Projections.** FPL only publishes expected points for the next gameweek. We also estimate points over the next few gameweeks from a player's recent minutes and points, his availability, and how hard each opponent is (double gameweeks count twice, blank ones zero). It is a rough guide: in a backtest on real gameweeks it was unbiased and modestly better than "his recent average" (details in `src/fpl_data/projection.py`).

The agent currently uses only `SearchPlayers` and `GetFixtures`; giving it tools for the rest is the next step.

## Working setup

- Edit files from Windows (`D:\Projects\squad_scout`).
- Run **every** command from WSL at `/mnt/d/Projects/squad_scout`. Nothing gets installed on Windows.
- `.gitattributes` and `.editorconfig` force LF line endings. `make run-py` polls for file changes because edits made on Windows don't trigger Linux file-change events. For the same reason, VS Code sometimes shows a stale copy of a file changed from WSL: close and reopen the tab.

## One-time WSL setup

```bash
# 1. Base tools (sqlite3 and jq are handy for poking at the cache and FPL JSON)
sudo apt update && sudo apt install -y build-essential git curl sqlite3 jq

# 2. uv (manages Python and the Python deps)
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc

# 3. Optional: grpcurl, which is curl for gRPC and useful for calling the data service.
#    Download a release from https://github.com/fullstorydev/grpcurl/releases
#    (or, if you have Go: go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest)

# 4. Git: keep LF endings
git config --global core.autocrlf false

# 5. Project deps + generated code
cd /mnt/d/Projects/squad_scout
make setup
make test
```

You don't need to install `protoc` separately. `make proto` uses the copy bundled with the `grpcio-tools` Python package.

vLLM lives in its own virtualenv (`~/.venvs/vllm`); the one-time install steps are at the top of `infrastructure/vllm/vllm_start.sh`.

## Running it (three WSL terminals)

```bash
make run-data   # terminal 1: data service (gRPC) on :50051; downloads FPL data on startup, then refreshes in the background
make run-py     # terminal 2: FastAPI on :8080, docs at http://localhost:8080/docs
make vllm       # terminal 3: vLLM on :8000; check it with: bash infrastructure/vllm/smoke_test.sh

make chat Q="How has Saka been playing?"
make chat-stream Q="How has Saka been playing?"   # also shows tool_start / tool_end events
```

`make chat` puts the question inside single quotes, so avoid apostrophes ("Arsenal's") in `Q`.

You can call the data service directly, without the agent or the GPU:

```bash
make grpc-list
grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
grpcurl -plaintext -d '{"player_id":12,"last_n":3}' localhost:50051 squadscout.v1.FplDataService/GetPlayerRecentForm
grpcurl -plaintext -d '{"team_name":"Arsenal","next_n":3}' localhost:50051 squadscout.v1.FplDataService/GetFixtures
grpcurl -plaintext -d '{"team_id":6}' localhost:50051 squadscout.v1.FplDataService/GetManagerTeam
grpcurl -plaintext -d '{"team_id":6,"player_out_ids":[124],"player_in_ids":[12],"max_hit":4}' localhost:50051 squadscout.v1.FplDataService/EvaluateTransfers
```

grpcurl leaves out fields that are 0 or false, so a missing `valid` means `false`. It prints field names in snake_case (`web_name`).

`make help` lists every command.

## Layout

```
proto/squadscout/v1/fpl.proto     gRPC contract (source of truth for both services)
fpl_data_service/
  src/fpl_data/
    main.py                       wiring, startup + timed refresh, graceful shutdown
    server.py                     the gRPC server (CamelCase RPC names -> FplService)
    handlers/                     the 10 RPCs, one module per topic (start with service.py)
    refresh.py                    "is this data stale? then download and save it"
    manager/                      a manager's team (free transfers, chips, selling prices), saved until the deadline
    squad.py                      the verifier: FPL squad rules and transfer checks
    lineup.py                     the best starting eleven of a squad (only 11 of 15 score)
    projection.py                 expected points over the next few gameweeks
    sentiment.py                  transfer activity and price-change outlook, in plain words
    optimizer.py                  transfer search: tries 1-3 swaps, checks each with the verifier
    db/                           SQLite cache; schema.sql is versioned
    fpl/                          FPL API client and the models for its JSON
    names.py                      accent-insensitive name matching
    src/squadscout/               generated, git-ignored
  tests/                          pytest; testdata/ holds saved real FPL responses, fake_fpl.py serves them
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
legacy/                           the original Go data service (not built or run; see its README)
```

## Status

- [x] Scaffold, gRPC contract, vLLM tool-calling smoke test
- [x] End to end: client → agent → data service → FPL data → answer
- [x] Data service ported from Go to Python (one language); responses checked identical on live data
- [x] Real FPL data cached in SQLite, refreshed in the background; accent-insensitive player search
- [x] Requests answered from SQLite; managers' teams saved until the next deadline
- [x] Player form, fixtures, gameweek stats, transfer trends, filtered player search
- [x] Manager teams (free transfers, chips, selling prices) and a transfer verifier
- [x] Multi-gameweek projections, best-eleven scoring, and the transfer optimizer (`SuggestTransfers`)
- [x] Real purchase prices for original-squad players; the manager can override the squad/bank; the free-transfer rule checked against 61 real teams
- [x] Rate-limit handling (honours Retry-After), parallel fetching, one log line per RPC
- [ ] Agent tools for the new RPCs, with compact output for the 4k-token model
- [ ] Eval set of 10-20 questions against the live stack
- [ ] Try `Qwen3-4B-AWQ`; streaming UI; resilience tests; docker-compose
