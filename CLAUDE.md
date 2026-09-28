# Squad Scout: context for Claude Code

AI agent for Fantasy Premier League. See README.md for the architecture diagram and layout.

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
| `make test` | Go + Python tests (no GPU or servers needed) |
| `grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers` | call Go directly |

## Conventions

- `proto/squadscout/v1/fpl.proto` is the contract and the single source of truth. Edit it, then run `make proto`. Generated code (`fpl_data_service/gen/`, `langgraph_service/src/squadscout/`) is git-ignored.
- Go: gRPC only (no HTTP server), with health + reflection registered; `modernc.org/sqlite` (pure Go, no CGO). The schema in `internal/db/schema.sql` is embedded and applied on startup, so it must be idempotent.
- Python: all addresses and settings live in `src/config.py` (env / `.env`). Tools catch `grpc.aio.AioRpcError` and return an `ERROR: ...` string so the model can say "data unavailable" instead of crashing.
- Tool docstrings are written for the model. Keep the tool list small and the arguments simple.

## Current state (2026-09-27)

- The whole chain works: vLLM tool-calling smoke test passes; FastAPI -> graph -> gRPC -> Go -> back to the model.
- `SearchPlayers` returns a **hardcoded** fake Saka (87 pts, form 6.2, £10.1m). `GetFixtures` and `GetPlayerGameweekStats` return `Unimplemented`.
- `get_player_gameweek_stats` is **deliberately removed** from `TOOLS`: the model can't know the current gameweek, so seeing the tool made it promise lookups it never made.
- The graph has a `nudge` node. If the model's reply has no tool calls but promises a lookup ("let me fetch..."), the graph sends it back once (`MAX_NUDGES = 1`, counted in `state["nudges"]`).
- Observed model behaviour (Qwen2.5-3B): uses the tool data correctly, but tends to end with "let me fetch...". Treat "How has Saka been playing?" as regression test #1.

## Next tasks (in order)

1. **Real data.** On startup (plus periodic refresh), fetch `/bootstrap-static/`, upsert teams and players into SQLite, record the sync in `sync_log`. Replace the stub `SearchPlayers` with a SQLite query on `search_name` (lowercased, accents stripped, so "odegaard" finds Ødegaard). Save a copy of bootstrap JSON for offline tests. Send a browser-like User-Agent (the FPL API may return 403 without one).
2. **`GetPlayerRecentForm(player_id, last_n)`** RPC backed by `/element-summary/{id}/`, so the model never has to pick gameweek numbers. Expose it as a tool, then drop or keep `get_player_gameweek_stats`.
3. **`GetFixtures`** from `/fixtures/`, with team names and difficulty.
4. **Eval set**: 10-20 questions run against the live stack. For each, check: the right tool and arguments were called; the numbers in the answer appear in the tool output; nothing was invented (e.g. fixtures while unimplemented, a second player's stats); off-topic questions are refused.
5. Try `Qwen/Qwen3-4B-AWQ` (`VLLM_MAX_MODEL_LEN=3072`), passing `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to `ChatOpenAI`. Compare with the eval set.
6. Later: streaming UI, resilience tests (kill the Go service mid-query), docker-compose (vLLM stays on the host).

## Working style

- Test changes yourself: run the servers in the background, run `make test` and `make chat-stream`, and read the logs before reporting back.
- The owner is learning. Briefly explain the why behind non-obvious changes.
