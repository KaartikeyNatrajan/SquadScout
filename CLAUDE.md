# Squad Scout: context for Claude Code

AI agent for Fantasy Premier League. See README.md for the overview and layout, docs/architecture.md for diagrams, docs/walkthrough.md for a beginner's tour.

```
client --HTTP/SSE :8080--> langgraph_service (FastAPI + LangGraph, Python)
                              |-- gRPC :50051 --> fpl_data_service (Python + SQLite) --> FPL public API
                              '-- OpenAI API :8000 --> vLLM (local model)
```

## Environment (important)

- Windows laptop; everything builds and runs in **WSL (Ubuntu)**. Repo is at `/mnt/d/Projects/squad_scout`.
- Never install tooling on Windows. C: is low on disk; the WSL distro lives on D:.
- Files on `/mnt/d` don't emit inotify events: reloaders must poll (the Makefile sets `WATCHFILES_FORCE_POLLING`).
- Line endings must stay LF (`.gitattributes`, `.editorconfig`). The Makefile uses `.RECIPEPREFIX = >` instead of tabs.
- Toolchain: uv (one project per service: `fpl_data_service/`, `langgraph_service/`), grpcurl. System Python is 3.14. Everything is Python; the original Go data service lives in `legacy/` for reference only (not built or run).
- GPU: **RTX 2060 (Turing, sm_75), ~4-5 GB of VRAM free.** No bfloat16 (use `--dtype half`), no FP8 checkpoints; use AWQ/GPTQ 4-bit. FlashAttention is unavailable.
- vLLM lives in its own venv, `~/.venvs/vllm` (not in langgraph_service). Model: `Qwen/Qwen2.5-3B-Instruct-AWQ`, served as `squad-scout-llm`, `--tool-call-parser hermes`. The settings are in `infrastructure/vllm/vllm_start.sh`.

## Commands (run from repo root in WSL)

| | |
|---|---|
| `make setup` / `make proto` | install deps / regenerate gRPC code after editing the `.proto` |
| `make run-data` | data service (gRPC) :50051; restart it after editing its code (no auto-reload) |
| `make run-py` | FastAPI :8080, auto-reloads |
| `make vllm` | vLLM :8000; check with `bash infrastructure/vllm/smoke_test.sh` |
| `make chat Q="..."` / `make chat-stream Q="..."` | end-to-end test; the stream shows tool_start/tool_end events |
| `make test` / `make fmt` | all tests (no GPU, servers or network needed) / format + lint both services |
| `cd fpl_data_service && uv run pytest tests/test_<topic>.py -v` | one test module |
| `grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers` | call the data service directly (more examples at the top of `src/fpl_data/server.py`) |
| `sqlite3 fpl_data_service/data/fpl.db "select * from sync_log"` | inspect the cache |

## Conventions

- `proto/squadscout/v1/fpl.proto` is the contract and the single source of truth. Edit it, then run `make proto`. Generated code (`fpl_data_service/src/squadscout/`, `langgraph_service/src/squadscout/`) is git-ignored; `make proto` writes both. Only ever add fields/RPCs with new field numbers; the Python side must keep working.
- Data service: gRPC only (`grpc.aio`, no HTTP server), health + reflection registered; stdlib `sqlite3`; `httpx` for the FPL API; Pydantic models for the FPL JSON. Handlers are plain `async` methods on `FplService` that raise `ServiceError`; `server.py` maps them to gRPC status codes. All database access happens on the event loop thread with no `await` inside a transaction, so one connection is enough.
- **Schema changes:** `src/fpl_data/db/schema.sql` is applied on startup (must be idempotent). After changing it, bump `SCHEMA_VERSION` in `db/store.py`: `Store.open` then drops and rebuilds every table (it's a re-downloadable cache). Add renamed/removed tables to `_ALL_TABLES`.
- Data service layering (see `docs/architecture.md`): `handlers` → `manager` / `projection` / `squad` / `lineup` / `optimizer` → `db` / `fpl` (`refresh` is run by `main.py`'s timer). `db` returns its own row types (never protobuf); `squad`, `lineup`, `projection` and `optimizer` are pure (no I/O), so the optimizer can run in a worker thread (`asyncio.to_thread`). Money is stored in tenths of £m (`now_cost` 95 = £9.5m) and converted to £m only in `handlers/convert.py`.
- Do arithmetic in the data service, not in the model: budgets, hits, totals, rule checks. Rule breaks in `EvaluateTransfers` are returned as plain-word `violations`, not gRPC errors.
- A transfer is worth the change in the projected points of the **best starting eleven** (`lineup.py`), not the sum of 15 players. `EvaluateTransfers.projected_points_net` and `SuggestTransfers` use the same rule and agree (a test checks it). Projections are never negative (the optimizer's pruning relies on it).
- gRPC error codes: InvalidArgument (bad request), NotFound (with a recovery hint), FailedPrecondition, Unavailable (FPL down or rate-limiting, and a manager's team isn't saved yet), Internal.
- Tests never hit the network: `tests/fake_fpl.py` is a fake FPL API (an `httpx.MockTransport`) serving the saved responses in `tests/testdata/` (bootstrap, fixtures, `/event/{1..5}/live/`, manager teams 1 and 6). Tests that check exact values depend on that snapshot.
- Python: all addresses and settings live in `src/config.py` (env / `.env`). Tools catch `grpc.aio.AioRpcError` and return an `ERROR: ...` string so the model can say "data unavailable" instead of crashing.
- Tool docstrings are written for the model. Keep the tool list small and the arguments simple.

## FPL API gotchas

- Send a browser-like User-Agent (403 otherwise). It rate-limits with **429** after many requests, especially around deadlines; a failed refresh retries after 1 minute.
- Many decimals arrive as strings (`"form": "6.2"`, `"expected_goals"`), and some numbers are null (scores, `chance_of_playing_next_round`: null = no concerns, 0 = out). Type nullable fields as `X | None` in the Pydantic models. Python's `round()` rounds halves to even; use `handlers/convert.round2` for anything sent to the caller.
- A manager's picks are only public after the deadline (404 before). After a Free Hit the squad reverts, so the service shows the previous gameweek's squad.
- Free transfers aren't published: `manager/rules.py` rebuilds them from the history. **No free transfer is gained in a Wildcard/Free Hit week** (checked against 343 recorded gameweeks of 61 real teams: 100% match; the alternative got 6 wrong). Purchase prices aren't published either: for players held since the start we use their price in the team's first gameweek (`/element-summary/{id}/`, stored forever in `player_prices`); only if that download fails is a selling price an estimate.
- FPL only shows a squad as of the last deadline, so the manager's later transfers are invisible. `EvaluateTransfers`/`SuggestTransfers` accept `current_squad_ids`, `current_bank` and `free_transfers_available` to override that.
- Rate limiting: the client allows 4 requests at once, honours `Retry-After` on 429 (waits up to 10 s, 2 retries), and otherwise raises `RateLimitedError`; `main.refresh_all` then waits as long as FPL asked before the next refresh.
- Transfer sentiment: `transfers_in/out_event` are per gameweek (reset at each deadline); the season totals feed `transfer_snapshots` (one per refresh, kept 8 days) so `net_transfers_24h` works across a deadline and stays unset until a snapshot 24-30 h old exists. `price_change_percent` is undocumented by FPL; we read it as progress towards the next price change (-100 fall .. +100 rise, ~0 after a change, sign follows net transfers) and its projections as that progress after the next 1-3 daily price updates (`sentiment.py` documents this). Thresholds: 0.5% of all managers = "heavily", 0.1% = "being" bought/sold (about the top 2.5% / 10% of players).
- FPL only publishes expected points (`ep_next`) for the next gameweek; `projection.py` estimates several (recent minutes and points shrunk towards the position average, availability, fixture difficulty). Constants were backtested on gameweeks 4-6: MAE 1.19 vs 1.25 for "his recent average" (2.62 vs 2.74 for regular starters); modest, football is noisy.

## Current state (2026-10-26)

- The data service was ported from Go to Python (2026-10-14). Port verified by sending 45 requests (all 10 RPCs, errors included) to both services against live FPL data: responses were identical. Go version: `legacy/fpl_data_service_go/`.
- Data service: all 10 RPCs (see README) work with live data, including `SuggestTransfers` (1-3 transfers, ~0.1-0.4 s). 297 pytest tests. Schema version 6.
- Requests are answered from SQLite. The background timer (every 10 min) refreshes bootstrap and fixtures when > 1 h old, and per-gameweek stats from `/event/{gw}/live/` (one download covers every player; the current and previous gameweeks refresh hourly, older ones are downloaded once). Freshness is tracked in `sync_log`.
- The only FPL call during a request: a manager's team (4-5 calls) the first time it's asked about in a gameweek. It's then saved in `manager_teams` until the next deadline (picks are hidden until then), survives restarts, and is served stale with a note if FPL is down.
- Player stats are per gameweek (FPL's totals); FPL only splits minutes and points per match. `GetPlayerRecentForm.last_n` counts gameweeks.
- Agent: still only `search_players` and `get_fixtures` in `TOOLS`. `get_player_gameweek_stats` is deliberately removed (the model can't know gameweek numbers). The other RPCs have no tools yet.
- The graph has a `nudge` node: a reply with no tool calls that promises a lookup ("let me fetch...") is sent back once (`MAX_NUDGES = 1`). It doesn't reliably help: Qwen2.5-3B tends to repeat the promise, and `/chat/stream` streams both drafts. Likely cause was `status: "a"` being opaque; `Player.availability` and `news` now spell it out, but the agent hasn't been re-tested with them.
- Regression tests for the agent: "How has Saka been playing?" and "How is Odegaard doing?".

## Next tasks (in order)

1. **Agent tools** for `GetManagerTeam`, `FindPlayers`, `EvaluateTransfers`, `SuggestTransfers`, `GetTransferTrends`, `GetPlayerRecentForm`, `GetPlayerFixtures` (and `team_name` for `get_fixtures`). Format results compactly instead of dumping JSON: a `GetManagerTeam` response is far too big for the 4096-token context (15 players x ~30 fields + fixtures + projection). Raise `GRPC_TIMEOUT_S` (5 s): a team's first `GetManagerTeam` takes ~2-3 s live (6-9 FPL requests, in parallel), later ones are instant until the deadline.
2. **Fix the nudge**: buffer each turn's tokens in `/chat/stream` and only send the final one; strip a trailing "let me ..." sentence as a fallback.
3. **Eval set**: 10-20 questions (the five manager questions in README, player questions, an off-topic one) run against the live stack. Check: the right tools and arguments; numbers in the answer appear in tool output; nothing invented; off-topic refused.
4. **Data service follow-ups**: a lineup-aware captain pick (who to captain, using projections); a Dockerfile; season rollover (player ids change each summer); a test snapshot with a real double gameweek; consider a PuLP/OR-Tools model if the search ever needs more than 3 transfers.
5. Try `Qwen/Qwen3-4B-AWQ` (`VLLM_MAX_MODEL_LEN=3072`), passing `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to `ChatOpenAI`. Compare with the eval set.
6. Later: streaming UI, resilience tests (kill the data service mid-query), docker-compose (vLLM stays on the host).

## Working style

- Test changes yourself: run the servers in the background, run `make test` and `make chat-stream`, and read the logs before reporting back.
- Don't hammer the FPL API while testing (429s); use the saved testdata and the fake API where possible.
- The owner is learning. Briefly explain the why behind non-obvious changes. When they ask for instructions rather than code, give step-by-step instructions (file, place, what to write) and don't write the code.
