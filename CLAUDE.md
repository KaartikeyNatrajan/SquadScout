# Squad Scout: context for Claude Code

AI agent for Fantasy Premier League. See README.md for the overview and layout, docs/architecture.md for diagrams, docs/walkthrough.md for a beginner's tour.

```
client --HTTP/SSE :8080--> langgraph_service (FastAPI + LangGraph, Python)
                              |-- gRPC :50051 --> fpl_data_service (Go + SQLite) --> FPL public API
                              '-- OpenAI API :8000 --> vLLM (local model)
```

## Environment (important)

- Windows laptop; everything builds and runs in **WSL (Ubuntu)**. Repo is at `/mnt/d/Projects/squad_scout`.
- Never install tooling on Windows. C: is low on disk; the WSL distro lives on D:.
- Files on `/mnt/d` don't emit inotify events: reloaders must poll (the Makefile sets `WATCHFILES_FORCE_POLLING`).
- Line endings must stay LF (`.gitattributes`, `.editorconfig`). The Makefile uses `.RECIPEPREFIX = >` instead of tabs.
- Toolchain: Go (in `/usr/local/go`, plugins in `~/go/bin`), uv, grpcurl. System Python is 3.14.
- GPU: **RTX 2060 (Turing, sm_75), ~4-5 GB of VRAM free.** No bfloat16 (use `--dtype half`), no FP8 checkpoints; use AWQ/GPTQ 4-bit. FlashAttention is unavailable.
- vLLM lives in its own venv, `~/.venvs/vllm` (not in langgraph_service). Model: `Qwen/Qwen2.5-3B-Instruct-AWQ`, served as `squad-scout-llm`, `--tool-call-parser hermes`. The settings are in `infrastructure/vllm/vllm_start.sh`.

## Commands (run from repo root in WSL)

| | |
|---|---|
| `make setup` / `make proto` | install deps / regenerate gRPC code after editing the `.proto` |
| `make run-go` | Go gRPC server :50051 (restart after Go edits; `go run` recompiles) |
| `make run-py` | FastAPI :8080, auto-reloads |
| `make vllm` | vLLM :8000; check with `bash infrastructure/vllm/smoke_test.sh` |
| `make chat Q="..."` / `make chat-stream Q="..."` | end-to-end test; the stream shows tool_start/tool_end events |
| `make test` | Go + Python tests (no GPU, servers or network needed) |
| `cd fpl_data_service && go test ./internal/<pkg>/ -v` | one Go package |
| `grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers` | call Go directly (more examples at the top of `cmd/api/main.go`) |
| `sqlite3 fpl_data_service/data/fpl.db "select * from sync_log"` | inspect the cache |

## Conventions

- `proto/squadscout/v1/fpl.proto` is the contract and the single source of truth. Edit it, then run `make proto`. Generated code (`fpl_data_service/gen/`, `langgraph_service/src/squadscout/`) is git-ignored. Only ever add fields/RPCs with new field numbers; the Python side must keep working.
- Go: gRPC only (no HTTP server), with health + reflection registered; `modernc.org/sqlite` (pure Go, no CGO).
- **Schema changes:** `internal/db/schema.sql` is embedded and applied on startup (must be idempotent). After changing it, bump `schemaVersion` in `internal/db/db.go`: `Open` then drops and rebuilds every table (it's a re-downloadable cache). Add renamed/removed tables to `allTables`.
- Data service layering (see `docs/architecture.md`): `handlers` → `refresh` / `manager` / `squad` / `optimizer` → `db` / `fpl`. `db` returns its own row types (never protobuf); `squad` and `optimizer` are pure (no I/O). Money is stored in tenths of £m (`now_cost` 95 = £9.5m) and converted to £m only in `handlers/convert.go`.
- Do arithmetic in Go, not in the model: budgets, hits, totals, rule checks. Rule breaks in `EvaluateTransfers` are returned as plain-word `violations`, not gRPC errors.
- gRPC error codes: InvalidArgument (bad request), NotFound (with a recovery hint), FailedPrecondition, Unavailable (FPL down, nothing cached), Internal, Unimplemented (`SuggestTransfers`).
- Tests never hit the network: `internal/fpltest` is a fake FPL API serving the saved responses in `internal/fpl/testdata/` (bootstrap, fixtures, Saka's history, manager teams 1 and 6). Tests that check exact values depend on that snapshot.
- Python: all addresses and settings live in `src/config.py` (env / `.env`). Tools catch `grpc.aio.AioRpcError` and return an `ERROR: ...` string so the model can say "data unavailable" instead of crashing.
- Tool docstrings are written for the model. Keep the tool list small and the arguments simple.

## FPL API gotchas

- Send a browser-like User-Agent (403 otherwise). It rate-limits with **429** after many requests, especially around deadlines; a failed refresh retries after 1 minute.
- Many decimals arrive as strings (`"form": "6.2"`, `"expected_goals"`), and some numbers are null (scores, `chance_of_playing_next_round`: null = no concerns, 0 = out). Use pointers for nullable fields.
- A manager's picks are only public after the deadline (404 before). After a Free Hit the squad reverts, so the service shows the previous gameweek's squad.
- Free transfers and purchase prices aren't published: `manager/rules.go` estimates them. One untested assumption (a free transfer is still gained in a Wildcard/Free Hit week) is documented there.
- FPL only publishes expected points (`ep_next`) for the next gameweek.

## Current state (2026-10-10)

- Data service: all 10 RPCs (see README) work with live data except `SuggestTransfers`, which is wired to `internal/optimizer` and returns Unimplemented. ~100 Go tests.
- Caching: bootstrap + fixtures in SQLite, refreshed hourly (timer checks every 10 min); player histories in SQLite on demand (1 h); manager teams in memory (5 min). Freshness is tracked in `sync_log`.
- Agent: still only `search_players` and `get_fixtures` in `TOOLS`. `get_player_gameweek_stats` is deliberately removed (the model can't know gameweek numbers). The other RPCs have no tools yet.
- The graph has a `nudge` node: a reply with no tool calls that promises a lookup ("let me fetch...") is sent back once (`MAX_NUDGES = 1`). It doesn't reliably help: Qwen2.5-3B tends to repeat the promise, and `/chat/stream` streams both drafts. Likely cause was `status: "a"` being opaque; `Player.availability` and `news` now spell it out, but the agent hasn't been re-tested with them.
- Regression tests for the agent: "How has Saka been playing?" and "How is Odegaard doing?".

## Next tasks (in order)

1. **Agent tools** for `GetManagerTeam`, `FindPlayers`, `EvaluateTransfers`, `GetTransferTrends`, `GetPlayerRecentForm`, `GetPlayerFixtures` (and `team_name` for `get_fixtures`). Format results compactly instead of dumping JSON: a `GetManagerTeam` response is far too big for the 4096-token context. Raise `GRPC_TIMEOUT_S` (5 s) or warm caches: a first `GetManagerTeam` makes 4-5 FPL requests.
2. **Fix the nudge**: buffer each turn's tokens in `/chat/stream` and only send the final one; strip a trailing "let me ..." sentence as a fallback.
3. **Eval set**: 10-20 questions (the five manager questions in README, player questions, an off-topic one) run against the live stack. Check: the right tools and arguments; numbers in the answer appear in tool output; nothing invented; off-topic refused.
4. **Optimizer**: implement `optimizer.Suggest` (design in `internal/optimizer/optimizer.go`), checking every candidate with `squad.Apply`.
5. Try `Qwen/Qwen3-4B-AWQ` (`VLLM_MAX_MODEL_LEN=3072`), passing `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to `ChatOpenAI`. Compare with the eval set.
6. Later: streaming UI, resilience tests (kill the Go service mid-query), docker-compose (vLLM stays on the host).

## Working style

- Test changes yourself: run the servers in the background, run `make test` and `make chat-stream`, and read the logs before reporting back.
- Don't hammer the FPL API while testing (429s); use the saved testdata and the fake API where possible.
- The owner is learning. Briefly explain the why behind non-obvious changes. When they ask for instructions rather than code, give step-by-step instructions (file, place, what to write) and don't write the code.
