# Next steps

Everything pending, in priority order (updated 2026-11-01). This is the only task list;
CLAUDE.md points here. Tiers: **P1** = next up; **P2** = needed for a usable product;
**P3** = improvements; **P4** = later or optional.

No UI is built in this repo: a separate person builds the frontend. This repo's job is a
backend whose API, data formats and streamed events are complete and documented, so the
frontend can be built against them.

## Priority overview

| # | Item | Tier | Where |
|---|---|---|---|
| 1 | App backend: users, conversations, API contract and UI events | P1 | langgraph_service (new app DB) |
| 2 | Cost: usage logging, prompt caching, smaller prompts | P1 | langgraph_service |
| 3 | Agent tools for the existing RPCs | P2 | langgraph_service |
| 4 | Eval set for the agent | P2 | langgraph_service |
| 5 | Mini league (one per user) | P2 | both services |
| 6 | Remove or rethink the nudge node | P2 | langgraph_service |
| 7 | Real login (replaces anonymous users) | P2 | langgraph_service |
| 8 | Lineup-aware captain pick | P3 | fpl_data_service |
| 9 | Season rollover | P3 | fpl_data_service |
| 10 | Dockerfiles + docker-compose for both services and the app DB | P3 | infrastructure |
| 11 | Resilience tests (kill the data service mid-query) | P3 | both |
| 12 | Test snapshot with a real double gameweek | P3 | fpl_data_service |
| 13 | `Player.news_items` count; agent `get_team_news` tool | P4 | both |
| 14 | PuLP/OR-Tools optimiser (only if > 3 transfers needed) | P4 | fpl_data_service |
| 15 | Local model track: Qwen3-4B-AWQ trial vs the eval set | P4 | infrastructure |

Why this order: 1 is the base everything else stands on (follow-ups, remembered team,
history, the frontend's contract, mini league, login). 2 because spend is already
noticeable: a single simple question costs $0.0006 on Haiku 5.5 but $0.025 on Opus 5.5,
which is the code's default model, and real conversations (long history, big team
payloads, several tool calls) cost many times more. 3 adds the tools that make the agent
useful; 1 and 2 first means each new tool is built on the final shapes (events, caching).

---

## 1. App backend: users, conversations, API contract, UI events (P1)

`langgraph_service` becomes the app backend, with its **own** database (Postgres; SQLite
in dev). Never put user data in the data service's SQLite: that is a disposable cache,
dropped on every schema bump. Use Alembic migrations here (user data must survive schema
changes).

```
frontend (someone else) --HTTPS/SSE--> app backend (FastAPI: users, conversations, agent) --gRPC--> fpl_data_service
                                         |
                                         v
                                      app database (permanent)
```

Layout inside `langgraph_service/src/`: `api/` (thin HTTP routes), `services/` (logic),
`db/` (tables, queries, migrations), `agent/` (as now), `config.py`.

### Tables

| Table | Columns | Notes |
|---|---|---|
| `users` | id, login id (anonymous id for now), `fpl_team_id` (nullable), `mini_league_id` (nullable), created_at | One FPL team and one mini league per user, so both live on `users`; no profile table |
| `conversations` | id, user_id, title, created_at, updated_at | |
| `messages` | conversation_id, role, text, ui_events (JSON), created_at | What the user *saw*; the frontend replays the last N |
| LangGraph checkpoints | per conversation (thread_id = conversation id) | What the *model* needs: full history incl. tool results and thinking |

`messages` and the checkpoints are deliberately separate: one is small and ready to
redraw, the other is complete.

### Behaviour

- **Team remembered:** `fpl_team_id` is saved the first time the user gives it (`PATCH
  /me`, or the agent spots it in a message). Each chat turn loads the user row into the
  graph state, so the agent never asks again. The squad itself isn't stored here: the
  data service caches it until the deadline and refetches after.
- **Follow-ups:** compile the graph with a checkpointer (`MemorySaver` in dev ->
  `langgraph-checkpoint-sqlite` -> Postgres). History is append-only: never edit earlier
  messages (breaks Claude's thinking blocks and the prompt cache).
- **Returning after days:** `GET /conversations/{id}/messages?limit=50` returns the last N
  messages with their UI events, so the frontend redraws text *and* the pitch. For the
  model, start a new conversation (the old one stays readable) since squads and gameweeks
  will have changed.
- **User context for the model:** at the start of a conversation the backend adds a short
  message: "team 123456, mini league 7890". Not in the system prompt (keeps it frozen
  for caching, item 2).
- **Play style comes from the conversation, not a stored setting.** The user says "I'm
  happy to take risks"; the system prompt tells the model to adapt (e.g. `max_hit`, safer
  or differential picks). It lasts for the conversation because it's in the history.
- **Login for now:** an anonymous user id the frontend generates and sends as a header
  (`X-User-Id`). Real login is item 7 and replaces only this part.

### HTTP API

```
GET    /me                PATCH /me                  (fpl_team_id, mini_league_id)
GET    /conversations     POST /conversations
GET    /conversations/{id}/messages?limit=50
POST   /conversations/{id}/chat                      (SSE stream, events below)
DELETE /conversations/{id}
```

### UI events (the SSE stream)

The model doesn't write the data the frontend draws; the server sends typed events:

| Event | Payload | Produced by |
|---|---|---|
| `thinking` | reasoning summary text | the model (`thinking: {"type": "adaptive", "display": "summarized"}`; currently dropped by `/chat/stream`) |
| `status` | e.g. "Loading your team" | server, from tool start/end |
| `token` | answer text | the model |
| `squad` | the full team: 15 players, positions, captain, bench order, prices, availability, next fixtures; `changes` for proposed transfers | server, straight from a `GetManagerTeam` / `SuggestTransfers` result |
| `highlight` | player ids + a short note | a UI tool `highlight_players(player_ids, note)` the model calls; the server only emits the event |
| `done` / `error` | message id / error text | server |

The frontend keeps the current squad: `squad` replaces it, `highlight` refers to players
in it, other turns leave it alone. UI events are saved in `messages.ui_events`, so a
reloaded conversation redraws the same way.

### Deliverables for the frontend developer

- `docs/api.md`: every endpoint, request and response JSON, error format, the user-id
  header, and the SSE event list with a field-by-field schema and an example stream for
  each kind of turn (text only, new squad, highlights).
- FastAPI's `/docs` (OpenAPI) generated from the Pydantic models, so the REST shapes are
  machine-readable; SSE events also get Pydantic models so their schema can't drift from
  the code.
- Tests: endpoint tests with a fake graph; SSE tests asserting event order and payload
  shapes; persistence tests (restart -> conversation and team id still there).

## 2. Cost (P1)

1. **Measure first.** Log every model call's input, output, cache-read and cache-write
   tokens and the estimated cost (from `usage_metadata`), per conversation; sum them per
   turn in the log. Without this nothing below can be verified.
2. **Default model.** `config.py` and `.env.example` default to `claude-opus-5-5` at
   effort `medium` (40x Haiku's price for the same question). Decide the default
   deliberately (Haiku 5.5 at `low` for development).
3. **Prompt caching.** `cache_control: {"type": "ephemeral"}` on the system prompt and on
   the last message (`langchain-anthropic` passes `cache_control` on content blocks
   through). Reads ~0.1x input price, writes 1.25x (5-min TTL) or 2x (1-hour); minimum
   512 tokens on Haiku 5.5. Keep the system prompt frozen (no dates or ids in it), don't
   change the tool list mid-conversation, history append-only. Verify with
   `cache_read_input_tokens`.
4. **Smaller tool results.** Compact formatters instead of JSON dumps (a `search_players`
   result is ~3,700 characters today; `GetManagerTeam` is far bigger).
5. **Fewer round trips.** Preload the user's team at conversation start (the server calls
   `GetManagerTeam` itself and adds a compact summary to the context message); a
   system-prompt rule to reuse earlier tool results unless fresh data is asked for.
6. **Long conversations.** Trim or summarise old tool results once a conversation grows.

## 3. Agent tools (P2)

Today `TOOLS` in `src/agent/tools.py` has only `search_players` and `get_fixtures`.

How to add one (repeat per tool, test each before the next):

1. Find the RPC's request/response messages in `proto/squadscout/v1/fpl.proto`. Use only
   the fields the model can fill sensibly (club *names*, not ids).
2. In `tools.py`, add an `@tool async def name(...) -> str:` with typed arguments and a
   docstring written for the model: what it returns and **when to use it**.
3. Body, copied from `search_players`: build the `pb.<Rpc>Request`, `await
   _stub().<Rpc>(request, timeout=settings.grpc_timeout_s)`, return text; catch
   `grpc.aio.AioRpcError` and return `_error(...)`.
4. Add the function to `TOOLS` (the model and `ToolNode` both read that list).
5. Add routing rules to `SYSTEM_PROMPT` in `graph.py` where useful.
6. Check with `make chat-stream`: `tool_start` shows the right tool and arguments.

Planned tools:

| Tool | RPC | Output |
|---|---|---|
| `get_head_to_head(team, opponent)` | GetHeadToHead | JSON (start here: simplest) |
| `get_fixtures(team, next_n)` | GetFixtures, switch to `team_name` | JSON |
| `get_player_form(player_id, last_n)` | GetPlayerRecentForm | JSON |
| `get_manager_team(team_id)` | GetManagerTeam | compact formatter + `squad` UI event |
| `find_players(position, max_price, sort_by)` | FindPlayers | formatter / top 5 |
| `evaluate_transfers(team_id, out_ids, in_ids, max_hit)` | EvaluateTransfers | JSON or formatter |
| `suggest_transfers(team_id, max_transfers, max_hit)` | SuggestTransfers | formatter + `squad` UI event with `changes` |
| `get_transfer_trends(direction)` | GetTransferTrends | formatter |
| `get_player_news(player_id)` | GetPlayerNews | JSON |

Details:

- Big responses get a formatter in a new `src/agent/format.py` that keeps only what the
  model needs (keep player ids). Rule of thumb: more than ~40 lines of JSON -> write a
  formatter. Unit-test formatters in `tests/test_format.py` with hand-built protobuf
  messages.
- Enums: the model passes words (`Literal["goalkeeper", ...]`), the tool maps them to
  `pb.POSITION_*` / `pb.PLAYER_SORT_*` / `pb.TRANSFER_DIRECTION_*`; unknown word ->
  `ERROR: ...` string.
- `max_hit` is `optional` in the proto (unset = no limit, 0 = free transfers only): only
  pass it when not `None`.
- Tools needing the user's team id or league take it from the graph state (the `users`
  row, item 1), not from the model.
- Leave out `GetPlayerGameweekStats`, `GetPlayerFixtures`, `GetTeamNews` and the
  `current_squad_ids` overrides for now.
- Fix stale comments in `tools.py` ("Go service", "get_fixtures unimplemented").

## 4. Eval set (P2)

10-20 questions run against the live stack: the five manager questions in README,
player questions, a head-to-head question, a follow-up pair (tests memory), a
play-style variation ("I'm happy to take a hit"), an off-topic one. Check: the right
tools and arguments; numbers in the answer appear in tool output; nothing invented;
off-topic refused. Record tokens and cost per question (item 2) so quality and cost are
compared together across changes.

## 5. Mini league, one per user (P2)

- **Data service:** new RPC, e.g. `GetMiniLeague(league_id, team_id)`. Reads FPL's
  `/leagues-classic/{id}/standings/` plus the picks of the rivals near the user. Returns
  standings, point gaps, and differentials (players rivals own that the user doesn't, and
  the reverse). Cache like manager teams; cap the rivals loaded (leagues can be huge, FPL
  rate-limits). The comparison arithmetic lives here.
- **Backend:** `users.mini_league_id`, saved once. An agent tool `get_mini_league()` that
  takes no id: the backend fills in the user's league (asks if unset).

## 6. Nudge node (P2)

Built for Qwen2.5-3B, which promised lookups it never made. Re-test with Claude: if it no
longer fires, delete it (simpler graph, no double drafts in the stream). If kept: buffer
each turn's tokens in `/chat/stream` and only send the final one; strip a trailing
"let me ..." sentence as a fallback.

## 7. Real login (P2)

A hosted provider (Google sign-in or email magic link via Supabase Auth / Clerk /
Auth0). The frontend gets a token, the backend verifies it and maps it to a `users` row;
anonymous users can be upgraded. Don't build password handling.

## 8-12. Data service and infrastructure (P3)

- **Captain pick:** lineup-aware, using projections (who to captain, with the vice).
- **Season rollover:** player and team ids change each summer; the history tables already
  use lasting `code`s, the rest of the cache needs a clean switch-over.
- **Docker:** Dockerfiles for both services; docker-compose with the app DB (vLLM, if
  used, stays on the host).
- **Resilience tests:** kill the data service mid-query; the agent should say "data
  unavailable".
- **Double gameweek snapshot** for the tests.

## 13-15. Later / optional (P4)

- `Player.news_items` count; an agent `get_team_news` tool.
- PuLP/OR-Tools optimiser if the search ever needs more than 3 transfers.
- Local model track: `Qwen/Qwen3-4B-AWQ` (`VLLM_MAX_MODEL_LEN=3072`, pass
  `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` to `ChatOpenAI`),
  compared with Claude on the eval set.
