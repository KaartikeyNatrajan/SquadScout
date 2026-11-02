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
| 1 | Cost: usage logging, prompt caching, smaller prompts (partly done) | P1 | langgraph_service |
| 2 | App backend: users, conversations, API contract and UI events | P1 | langgraph_service (new app DB) |
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

Why this order: 1 because spend was already noticeable (a simple question cost $0.025 on
Opus 5.5, then the default model, vs $0.0006 on Haiku 5.5) and real conversations (long
history, big team payloads, several tool calls) cost many times more; the same work also
makes a small local model usable (its 4,096-token context is the limit there). 2 is the
base everything else stands on (follow-ups, remembered team, history, the frontend's
contract, mini league, login). 3 adds the tools that make the agent useful; 1 and 2
first means each new tool is built on the final shapes (formatters, caching, events).

---

## 1. Cost (P1)

Done (2026-11-01):

- **Measuring.** Every model call's input, cache-read, cache-write and output tokens and
  its cost are logged, plus a total per chat request (`agent/usage.py`; the server
  passes a `UsageTracker` to the graph as a callback). Prices are in `PRICES`; models
  not listed (vLLM) log tokens without a cost.
- **Default model.** `claude-haiku-5-5` at effort `low` (`config.py`, `.env.example`).
  Choose a bigger model deliberately after comparing on the eval set (item 4).
- **Prompt caching.** Claude: a cache marker on the system prompt (caches tools + system
  prompt) and top-level automatic caching (moves a marker to the end of the conversation
  on each call), `CACHE_MARKER` in `graph.py`. vLLM: `--enable-prefix-caching` in
  `vllm_start.sh`. Measured: the second model call of a question reads ~60% of its input
  from the cache; a repeated question within 5 minutes reads 99.8%.
- **Smaller tool results.** `agent/format.py`: compact text instead of JSON for
  `search_players` (~1,400 characters per player instead of ~3,700) and `get_fixtures`.

Measured on "How has Saka been playing?" (Haiku 5.5, low effort): $0.00058 before,
$0.00034 now (cold cache), $0.00018-0.00021 with a warm cache. Most of what's left is
output tokens (~300-400 per answer, at 5x the input price).

Also done (2026-11-01, second round):

- **Preloaded team.** When a request has `fpl_team_id`, the server calls `GetManagerTeam`
  itself and puts a compact summary (`format.manager_team`, ~3,400 characters instead of
  ~41,000 of JSON; each player with next-GW and 5-GW projections) in front of the
  question (`agent/context.py`). "Who should I captain?" is one model call, ~$0.0005.
  With conversations (item 2) this happens once, at the start.
- **Trimming.** vLLM: `agent/trim.py` swaps the oldest tool results for a short note
  when the request would pass `VLLM_CONTEXT_BUDGET_TOKENS` (2,500); the current
  question's results are never removed. Claude: Anthropic's context editing clears old
  tool results once input passes `CLAUDE_CLEAR_TOOL_RESULTS_AT_TOKENS` (20,000), keeping
  the newest 3 (checked live on Haiku 5.5). Both only matter once conversations exist.
- **Shorter answers.** Length rules in `SYSTEM_PROMPT`. On four test questions: answer
  words -36%, output tokens -25%, cost -12% (output also counts hidden thinking, which the
  rule doesn't shorten).

Still to do:

- **Reuse earlier results** in follow-ups: a system-prompt rule, once conversations
  exist (item 2).
- **Thinking tokens.** Even at effort `low`, some answers spend most output tokens on
  hidden thinking (531 output tokens for a 73-word answer). Measure on the eval set
  before changing anything: less thinking may cost answer quality.
- **New tools get formatters** from the start (item 3).
- **Check the quality** of the shorter answers on the eval set (item 4).

## 2. App backend: users, conversations, API contract, UI events (P1)

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
  for caching, item 1).
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
  row, item 2), not from the model.
- Leave out `GetPlayerGameweekStats`, `GetPlayerFixtures`, `GetTeamNews` and the
  `current_squad_ids` overrides for now.
- Fix stale comments in `tools.py` ("Go service", "get_fixtures unimplemented").

## 4. Eval set (P2)

10-20 questions run against the live stack: the five manager questions in README,
player questions, a head-to-head question, a follow-up pair (tests memory), a
play-style variation ("I'm happy to take a hit"), an off-topic one. Check: the right
tools and arguments; numbers in the answer appear in tool output; nothing invented;
off-topic refused. Record tokens and cost per question (item 1) so quality and cost are
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
