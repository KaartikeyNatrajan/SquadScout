# Squad Scout: how the code works

A beginner-friendly walkthrough of the codebase, written 2026-09-27 and
updated 2026-10-26 (the data service is Python, with projections, a transfer optimizer and transfer sentiment). Things change; if this disagrees with the
code, the code wins. For diagrams, see [architecture.md](architecture.md).

## 1. The big picture

Squad Scout is a chatbot for Fantasy Premier League. You ask "How has Saka been
playing?" and get an answer based on real data rather than guesses.

It runs as **three separate programs**. Each one does one job, and they talk to
each other over the network (all on your own machine):

```
 You (curl / make chat)
   │  HTTP  (port 8080)
   ▼
┌─────────────────────────┐   gRPC (port 50051)   ┌────────────────────────┐
│ langgraph_service       │ ────────────────────▶ │ fpl_data_service       │
│ Python. The "brain":    │                       │ Python. The "librarian"│
│ runs the conversation,  │ ◀──────────────────── │ looks up player data   │
│ decides which tools     │                       │ (SQLite, FPL website)  │
│ to use                  │                       └────────────────────────┘
│                         │   HTTP (port 8000)    ┌────────────────────────┐
│                         │ ────────────────────▶ │ vLLM                   │
│                         │ ◀──────────────────── │ runs the AI model      │
└─────────────────────────┘                       │ (Qwen 3B) on your GPU  │
                                                  └────────────────────────┘
```

A restaurant is a good way to picture it:

- **vLLM** is a clever chef who knows nothing about today's stock. It can only talk.
- **fpl_data_service** is the storeroom clerk. It knows the facts (points,
  prices) but can't hold a conversation.
- **langgraph_service (Python)** is the head waiter. It takes your order, asks
  the chef what to do, fetches ingredients from the clerk when the chef asks for
  them, and brings you the final dish.

### The technologies in one line each

| Technology | What it is | Used for |
|---|---|---|
| **gRPC** | A way for one program to call a function in another program over the network, as if it were local | Agent → data service calls |
| **Protocol Buffers (proto)** | A file that defines the *shape* of the data and functions for gRPC. Tools turn it into Python code | `fpl.proto` |
| **SQLite** | A database stored in a single file, with no server needed | Caching FPL data (not filled in yet) |
| **Python + FastAPI** | A web framework: turns Python functions into HTTP endpoints | `/chat`, `/chat/stream` |
| **LangChain / LangGraph** | Libraries for building AI agents. LangGraph describes the agent as a flowchart (a "graph") | The agent loop |
| **vLLM** | A program that loads an AI model onto the GPU and serves it with the same API as OpenAI | Running Qwen locally |
| **Qwen2.5-3B-Instruct-AWQ** | A small open AI model, compressed to 4-bit so it fits in 6 GB of VRAM | The "chef" |
| **uv** | A fast Python package manager | Installs Python dependencies |
| **Make** | Runs named shortcuts (`make run-data`) | Every command |

---

## 2. What happens when you ask a question (step by step)

Take `make chat Q="How has Saka been playing?"`:

1. **curl sends HTTP** `POST localhost:8080/chat` with
   `{"message": "How has Saka been playing?"}`.
2. **FastAPI** ([server.py](../langgraph_service/src/server.py)) checks the JSON
   and builds the starting **state**: a list of messages that holds just your
   question.
3. **The graph starts** at the `agent` node
   ([graph.py](../langgraph_service/src/agent/graph.py)). It sends three things
   to vLLM:
   - the **system prompt** (rules such as "never guess stats"),
   - your message,
   - a **description of the available tools** (`search_players`,
     `get_fixtures`), generated from the Python functions.
4. **The model replies with a tool call** instead of text. In effect it says
   "please run `search_players(name="saka")`". It can't run code itself; it can
   only *ask*.
5. **The router** (`route_after_agent`) sees the tool call and sends the flow to
   the `tools` node.
6. **The tools node** runs the Python `search_players` function
   ([tools.py](../langgraph_service/src/agent/tools.py)). That function makes a
   **gRPC call** to the data service.
7. **The data service** ([players.py](../fpl_data_service/src/fpl_data/handlers/players.py))
   receives `SearchPlayers(name="saka")`, looks the name up in its SQLite copy
   of the FPL data, and returns the matching players: price, points, form,
   availability, news, expected points and more.
8. **Back in Python**, the result is converted to JSON text and added to the
   message list as a "tool message".
9. **Back to `agent`**: the model now sees the question *and* the data, and
   writes a text answer.
10. **The router checks the answer.** No tool calls, so it's probably final. But
    if it contains a broken promise like "Let me check…", it goes to `nudge`
    (once), which adds "you said you'd fetch data but didn't…" and loops back to
    `agent`.
11. **END.** FastAPI returns the last message as `{"reply": "..."}`.

The core idea of an "agent" is this loop:
**model → (maybe tools → model)\* → answer**. The model decides; the code
carries it out.

---

## 3. Directory by directory

```
squad_scout/
├── Makefile                 shortcuts for every command
├── CLAUDE.md, README.md     docs
├── docs/                    this walkthrough + architecture diagrams
├── proto/                   the contract between the agent and the data service
├── fpl_data_service/        Python: the data service
├── legacy/                  the original Go version of the data service (reference only)
├── langgraph_service/       Python: the agent + web API
├── infrastructure/vllm/     scripts to start and test the AI model server
├── docker-compose.yml       all commented out; for later
└── .gitattributes, .editorconfig   keep line endings as LF (Linux style)
```

### `proto/`: the contract

**[fpl.proto](../proto/squadscout/v1/fpl.proto)** is the single source of truth
for what the agent can ask the data service.

- `service FplDataService { rpc SearchPlayers(...) returns (...) }` declares a
  **remote function**. There are ten (listed in the README), from
  `SearchPlayers` to `EvaluateTransfers`.
- `message Player { ... }` declares a **data shape**, like a class that only has
  fields.
- The `= 1`, `= 2` after each field **are not default values**. They're field IDs
  used in the compact binary format. Never change existing ones.
- `repeated` means a list. `enum Position` is a fixed set of choices
  (goalkeeper, defender, …).

`make proto` reads this file and **generates** code in two places, both ignored
by git:

- `fpl_data_service/src/squadscout/`: Python classes plus a "servicer" base class
  that the data service implements.
- `langgraph_service/src/squadscout/`: Python classes plus a "stub" (client)
  Python calls.

That's why the proto is the single source of truth: if you change it, both sides
are regenerated to match, and they can't drift apart.

### `fpl_data_service/`: the data service

This is the "librarian". It downloads data from the FPL website, keeps a copy in
SQLite, and answers questions about it. It also knows FPL's squad rules, so it
can check whether a set of transfers is allowed.

```
fpl_data_service/
├── pyproject.toml           dependencies (uv reads this)
├── src/fpl_data/
│   ├── __main__.py          `python -m fpl_data` starts here
│   ├── main.py              wiring, refresh timer, shutdown
│   ├── server.py            the gRPC server: CamelCase RPC names -> FplService
│   ├── config.py            settings from environment variables
│   ├── handlers/            the 10 RPCs, one module per topic (start with service.py)
│   ├── refresh.py           "is this data stale? then download and save it"
│   ├── manager/             a manager's team: free transfers, chips, selling prices
│   ├── squad.py             the verifier: FPL's squad rules
│   ├── lineup.py            the best starting eleven (only 11 of 15 score)
│   ├── sentiment.py         what other managers are doing with a player, in words
│   ├── projection.py        expected points over the next few gameweeks
│   ├── optimizer.py         transfer search: tries 1-3 swaps, checks each
│   ├── db/                  SQLite: tables, saving, queries
│   ├── fpl/                 HTTP client for the FPL website + models for its JSON
│   └── names.py             "Ødegaard" -> "odegaard"
├── src/squadscout/          generated from proto (don't edit)
├── tests/                   pytest; testdata/ = saved real FPL responses
└── data/fpl.db              the SQLite file (created on start)
```

The modules form layers, and each layer only imports the ones below it:

```
server.py, handlers/                            talk gRPC; validate, convert
   │
refresh   manager/   squad  lineup  projection  optimizer  names   the logic
   │         │
  db/       fpl/                                storage and downloading
```

[architecture.md](architecture.md) has this as a proper diagram, plus diagrams
of each request.

**[main.py](../fpl_data_service/src/fpl_data/main.py)**: startup, in order:

1. Read settings from environment variables (`GRPC_ADDR`, default `[::]:50051`;
   `DB_PATH`, default `data/fpl.db`; `FPL_BASE_URL`, default the real website).
2. `Store.open(...)`: create or open the SQLite file and create the tables.
3. Build the FPL client, the **refresher** (keeps the data fresh) and the
   **manager loader** (fetches managers' teams).
4. `refresh_all()`: download players, teams, fixtures and gameweek stats if the
   saved copy is more than an hour old.
5. `start_server(...)`: start the gRPC server, with a **health check** and
   **reflection** (so `grpcurl` can list the functions without the proto file).
6. A background task re-runs `refresh_all()` every 10 minutes (every minute
   after a failure).
7. Wait for Ctrl+C, then shut down cleanly.

Python patterns you'll see here:

- `async def` / `await`: the server handles many requests at once. While one
  waits for the FPL website, another can run. Everything that waits
  (downloading) is `async`; the SQLite calls are quick and plain.
- `with store.transaction() as conn:` is a **context manager**: it starts a
  database transaction and, when the block ends, commits it, or undoes
  everything if an error was raised. That's how saving is all-or-nothing.
- `@dataclass` is a class that's just fields (like a proto message, but for
  our own code). Database queries return these ("rows").
- `X | None` means "an X, or nothing". Where the FPL JSON has `null`, the
  models use it, so "no data" can't be mistaken for 0.

**`handlers/`**: the "routes". With gRPC, each RPC in the proto is a method, and
every method has the same shape: **check the request → get the data → convert it
to protobuf**. They are plain async methods on `FplService` that raise
`ServiceError` for failures; `server.py` turns those into gRPC status codes
(`NOT_FOUND`, `UNAVAILABLE`, …).

- `players.py`: `search_players` (by name) and `find_players` (by position,
  price and so on).
- `fixtures.py`: `get_fixtures` (accepts a team name too) and
  `get_player_fixtures`.
- `stats.py`: `get_player_recent_form` and `get_player_gameweek_stats`.
- `manager_team.py`: `get_manager_team`.
- `transfers.py`: `get_transfer_trends`, `evaluate_transfers`,
  `suggest_transfers`.
- `convert.py`: turns database rows into protobuf messages, including £m
  conversions (FPL stores 95 for £9.5m).
- `base.py`: helpers shared by all of them (limits, looking up a player or
  team, attaching fixtures to players).
- `service.py`: combines the groups into one `FplService`.

**`refresh.py`**: the background downloads, run by `main.py`'s timer. One rule
covers every download: *if `sync_log` says this dataset was saved less than an
hour ago, skip; otherwise download and save it*. There are three: players
(bootstrap), fixtures, and every player's stats per gameweek. A single
`/event/{gw}/live/` download has all ~700 players' stats for one week, so the
whole season is at most 38 downloads; finished weeks are kept for good. Because
of this, requests about players never wait on the FPL website.

**`db/`**: the database, one module per kind of data (`players.py`,
`fixtures.py`, `player_gameweeks.py`, `manager_teams.py`, …).

- `schema.sql` is run on every start. It's safe to repeat because every
  statement says `IF NOT EXISTS`.
- `store.py` has a **schema version**. Change `schema.sql` → bump the version →
  on the next start the old tables are dropped and rebuilt. That's fine because
  it's only a cache.
- Saving uses **transactions** (all of it saves, or none of it) and **upserts**
  (insert, or update if the row exists).
- Queries return plain dataclasses (`PlayerRow`, `FixtureRow`, …), never
  protobuf, so this package doesn't depend on gRPC.

**`fpl/`**: downloads JSON from the FPL website (`client.py`) and decodes it
into **Pydantic models** (`models.py`). A model is a class whose fields say which
JSON keys we use and what type they should be: Pydantic converts FPL's `"6.2"`
strings into floats and turns `null` into `None`, and complains clearly if FPL
changes something we depend on. The tests' `testdata/` folder holds saved real
responses.

**`manager/`**: loads a manager's team. FPL doesn't publish everything we need,
so `rules.py` works out the rest: how many **free transfers** the team has
(rebuilt from its history; the rules were checked against 61 real teams), which
**chips** are left, and what each player would **sell for** (FPL keeps half of
any price rise, so we need what was paid; for players held since the start that
is their price in the team's first gameweek, from `/element-summary/`). It also
handles the Free Hit case: after a Free Hit the squad goes back to the previous
week's, so that's the one shown. A team is downloaded the first time it's asked
about (its history, picks and transfers are fetched at the same time), then
**saved in SQLite until the next deadline**: FPL hides new picks until then, so
nothing visible can change. That's the only time a request waits on the FPL
website.

**`squad.py`**: the **verifier**. Given a squad and some transfers, it builds the
new squad and lists every rule it breaks: 15 players, 2/5/5/3 by position, at
most 3 per club, within budget. It only does arithmetic on what it's given (no
database, no network), which makes it easy to test and fast.

**`projection.py`**: FPL only says how many points a player is expected to score
*next* week. Transfer decisions look further ahead, so this estimates the next
several weeks: how many minutes he usually plays, how many points he scores per
90 minutes (pulled towards the average for his position when there isn't much
evidence), whether he's fit, and how hard each opponent is. A double gameweek
counts twice and a blank one zero. The numbers in it were chosen by
**backtesting**: predict each past gameweek from the ones before it and compare
with what really happened. It beat the naive guess ("his recent average") by a
small margin, which is typical because football points are very noisy, so treat
projections as a rough guide. `handlers/projector.py` gathers a player's recent
gameweeks and fixtures from the database and calls it.

**`sentiment.py`**: what are other managers doing with this player? Every week hundreds of thousands of transfers happen; heavy buying usually means form or a good run of fixtures, heavy selling means injury or lost form. The raw counts mean little to a small language model, so this turns them into a sentence ("heavily bought: net +3.15% of all managers this gameweek (the most bought player)") and says whether a price rise or fall is coming. The database keeps a snapshot of everyone's transfer totals at each refresh, so the service can also say how many net transfers happened in the last 24 hours, even across a deadline (FPL's weekly counts restart at every deadline).

**`lineup.py`**: only 11 of a squad's 15 players score. This picks the best valid
starting eleven (one of eight formations) from the players' projected points. A
transfer is worth the change in that eleven, so upgrading someone who would sit
on the bench is worth nothing.

**`optimizer.py`**: finds the best transfers. It tries every way of swapping 1 to
3 players for same-position candidates, throws out any combination the verifier
(`squad.apply`) rejects, and ranks the rest by *best eleven after − best eleven
before − the points hit*. Two tricks keep it fast (about a tenth of a second):
candidates that are both dearer and worse than another are dropped, and a set of
players to sell is skipped when even the best imaginable replacements couldn't
make the top five. A test checks it finds the same best answer as a plain
try-everything search. It does no database or network work, so the handler runs
it in a worker thread.

**Tests**: `make test` runs 297 tests for this service without the internet.
`tests/fake_fpl.py` is a fake FPL website (an `httpx.MockTransport`) that serves
the saved files from `tests/testdata/`; the real code is pointed at it. Each
test gets a fresh temporary database. `@pytest.mark.parametrize` runs one test
body over a table of inputs and expected outputs.

**History**: this service was first written in Go and then ported to Python so
the whole project is one language. The Go version is kept in `legacy/`, and
[architecture.md](architecture.md#11-porting-notes-go-to-python) lists how each
piece maps.

### `langgraph_service/`: the Python agent

```
langgraph_service/
├── src/server.py          FastAPI app: HTTP endpoints
├── src/config.py          all settings (addresses, model name, ...)
├── src/agent/graph.py     the agent flowchart + system prompt + nudge
├── src/agent/state.py     what data flows through the flowchart
├── src/agent/tools.py     the tools the model can call (gRPC clients)
├── src/squadscout/        generated from proto (don't edit)
├── tests/test_graph.py    tests that run without any server
├── pyproject.toml         dependencies (uv reads this)
└── .env / .env.example    local settings
```

**[config.py](../langgraph_service/src/config.py)**: one `Settings` class. Each
field is read from an environment variable of the same name in capitals, then
from `.env`, then falls back to the default in the code. Holding every address
here means that moving to Docker later is a config change, not a code change.
The key values are the data service address `localhost:50051`, the vLLM URL
`localhost:8000/v1`, the model name `squad-scout-llm`, temperature 0 (repeatable
answers) and the recursion limit 12 (maximum number of graph steps).

**[server.py](../langgraph_service/src/server.py)**: the web API.

- `lifespan`: at startup it builds the graph once; at shutdown it closes the
  gRPC connection.
- `ChatRequest` / `ChatResponse` are **Pydantic models**, i.e. JSON schemas.
  FastAPI rejects bad input automatically.
- `GET /health` returns `{"status": "ok"}`.
- `POST /chat` runs the whole graph (`ainvoke`) and returns only the last
  message.
- `POST /chat/stream` uses `astream_events` to send **Server-Sent Events** as
  things happen: `tool_start`, `tool_end`, one `token` per chunk of text, then
  `done`. That's why its output arrives split into many small pieces.
- `async def` / `await`: while one request waits for the model or for the data service, Python
  can serve other requests.

**[state.py](../langgraph_service/src/agent/state.py)**: the "memory" of one
conversation run:

- `messages`: the full conversation. `add_messages` means that when a node
  returns new messages, they're **appended**, not substituted.
- `nudges`: how many times we've pushed the model back.
- `fpl_team_id`: reserved for future "analyse my team" features.

**[tools.py](../langgraph_service/src/agent/tools.py)**: the model's "hands".

- `@tool` turns a Python function into something the model can call. The
  **function name, argument types and docstring** are sent to the model as a
  description. That's why the docstrings are written *for the model*.
- Each tool: calls the data service over gRPC → converts the protobuf response to JSON text →
  returns it.
- On a gRPC failure (the data service is down, Unimplemented, …) it returns the text
  `"ERROR while ...: UNIMPLEMENTED - ..."` instead of crashing, so the model can
  tell you "data unavailable".
- `TOOLS = [search_players, get_fixtures]`: `get_player_gameweek_stats` exists
  but is deliberately **hidden** from the model, because the model doesn't know
  the current gameweek.
- One gRPC connection ("channel") is shared by all calls.

**[graph.py](../langgraph_service/src/agent/graph.py)**: the heart of the
project.

- `SYSTEM_PROMPT`: the rules the model sees on every call (only discuss FPL,
  never guess numbers, don't say "let me fetch").
- `build_graph()` creates the flowchart:
  - **node `agent`**: sends system prompt + messages to vLLM and appends the
    reply.
  - **node `tools`**: LangGraph's prebuilt `ToolNode`. It runs whatever tools
    the last reply asked for and appends the results. `handle_tool_errors=True`
    turns crashes into messages.
  - **node `nudge`**: appends a "you promised but didn't call a tool" message
    and increments `nudges`.
  - **edges**: START → agent; agent → (router decides); tools → agent;
    nudge → agent.
- `route_after_agent()` is the router: tool calls go to `tools`; a text reply
  that matches `PROMISE_PATTERN` (a regex for phrases like "let me … check") with
  fewer than 1 nudge so far goes to `nudge`; anything else goes to END.
- `ChatOpenAI(base_url=vLLM)`: vLLM copies OpenAI's API, so the standard OpenAI
  client works. `.bind_tools(TOOLS)` attaches the tool descriptions to every
  request.

**[test_graph.py](../langgraph_service/tests/test_graph.py)**: checks that the
graph builds and `/health` works. Neither needs the model or the data service running
(building the graph doesn't contact vLLM).

### `infrastructure/vllm/`

**[vllm_start.sh](../infrastructure/vllm/vllm_start.sh)** starts the model
server with settings tuned for an RTX 2060:

- `--dtype half`: this GPU generation can't do bfloat16.
- `--gpu-memory-utilization 0.75`: leaves some VRAM for Windows.
- `--max-model-len 4096`: the maximum conversation length in tokens.
- `--enforce-eager`: slower, but saves about 0.5 GB of VRAM.
- `--enable-auto-tool-choice --tool-call-parser hermes`: **essential**. Qwen
  writes tool calls as special text, and this parser turns that into proper
  `tool_calls`. Without it the graph never reaches the tools node.
- `--served-model-name squad-scout-llm` must match `LLM_MODEL` in Python's
  config.

Any setting can be overridden per run, e.g. `VLLM_MAX_MODEL_LEN=3072 make vllm`.

**[smoke_test.sh](../infrastructure/vllm/smoke_test.sh)** sends one raw request
with a tool definition straight to vLLM and prints PASS if a real tool call
comes back.

### `Makefile`

[Makefile](../Makefile) holds the shortcuts: `make setup` (install everything),
`make proto` (regenerate code), `make run-data` / `make run-py` / `make vllm`
(start each server), `make chat` / `make chat-stream` (test questions),
`make test`, `make fmt`. Recipes start with `>` instead of a tab, so Windows
editors can't break them, and it sets `WATCHFILES_FORCE_POLLING` because
file-change events don't work on `/mnt/d`.

---

## 4. What's real and what's placeholder (as of 2026-10-10)

| Part | Status |
|---|---|
| Model server, tool calling | ✅ Works |
| Agent → data service over gRPC | ✅ Works |
| Data service: players, fixtures, form, trends, managers' teams, transfer checks | ✅ Real FPL data, cached in SQLite, refreshed hourly |
| Multi-gameweek projections and the transfer optimizer (`SuggestTransfers`) | ✅ Works; projections are a rough, backtested guide |
| Agent tools | ⚠️ Only `search_players` and `get_fixtures`; the other RPCs have no tools yet |
| Agent loop + nudge | ⚠️ Runs, but the model sometimes still ends with "Let me check…", and `/chat/stream` shows both drafts when the nudge fires |
| docker-compose | ❌ All commented out |
