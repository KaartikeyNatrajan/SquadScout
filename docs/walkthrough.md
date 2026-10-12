# Squad Scout: how the code works

A beginner-friendly walkthrough of the codebase, written 2026-09-27 and
updated 2026-10-10 (data service). Things change; if this disagrees with the
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
│ Python. The "brain":    │                       │ Go. The "librarian":   │
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
- **fpl_data_service (Go)** is the storeroom clerk. It knows the facts (points,
  prices) but can't hold a conversation.
- **langgraph_service (Python)** is the head waiter. It takes your order, asks
  the chef what to do, fetches ingredients from the clerk when the chef asks for
  them, and brings you the final dish.

### The technologies in one line each

| Technology | What it is | Used for |
|---|---|---|
| **Go** | A compiled, fast, simple language | The data service |
| **gRPC** | A way for one program to call a function in another program over the network, as if it were local | Python → Go calls |
| **Protocol Buffers (proto)** | A file that defines the *shape* of the data and functions for gRPC. Tools turn it into Go and Python code | `fpl.proto` |
| **SQLite** | A database stored in a single file, with no server needed | Caching FPL data (not filled in yet) |
| **Python + FastAPI** | A web framework: turns Python functions into HTTP endpoints | `/chat`, `/chat/stream` |
| **LangChain / LangGraph** | Libraries for building AI agents. LangGraph describes the agent as a flowchart (a "graph") | The agent loop |
| **vLLM** | A program that loads an AI model onto the GPU and serves it with the same API as OpenAI | Running Qwen locally |
| **Qwen2.5-3B-Instruct-AWQ** | A small open AI model, compressed to 4-bit so it fits in 6 GB of VRAM | The "chef" |
| **uv** | A fast Python package manager | Installs Python dependencies |
| **Make** | Runs named shortcuts (`make run-go`) | Every command |

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
   **gRPC call** to the Go service.
7. **Go** ([players.go](../fpl_data_service/internal/handlers/players.go))
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
├── proto/                   the contract between Python and Go
├── fpl_data_service/        Go: the data service
├── langgraph_service/       Python: the agent + web API
├── infrastructure/vllm/     scripts to start and test the AI model server
├── docker-compose.yml       all commented out; for later
└── .gitattributes, .editorconfig   keep line endings as LF (Linux style)
```

### `proto/`: the contract

**[fpl.proto](../proto/squadscout/v1/fpl.proto)** is the single source of truth
for what Python can ask Go.

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

- `fpl_data_service/gen/`: Go types plus a "server interface" that Go must
  implement.
- `langgraph_service/src/squadscout/`: Python classes plus a "stub" (client)
  Python calls.

That's why the proto is the single source of truth: if you change it, both sides
are regenerated to match, and they can't drift apart.

### `fpl_data_service/`: the Go data service

This is the "librarian". It downloads data from the FPL website, keeps a copy in
SQLite, and answers questions about it. It also knows FPL's squad rules, so it
can check whether a set of transfers is allowed.

```
fpl_data_service/
├── cmd/api/main.go          program entry point: wiring, refresh timer, shutdown
├── internal/
│   ├── handlers/            the 10 gRPC functions ("routes"), one file per topic
│   ├── refresh/             "is this data stale? then download and save it"
│   ├── manager/             a manager's team: free transfers, chips, selling prices
│   ├── squad/               the verifier: FPL's squad rules
│   ├── optimizer/           transfer search: structure only, not written yet
│   ├── db/                  SQLite: tables, saving, queries
│   ├── fpl/                 HTTP client for the FPL website (+ testdata/)
│   ├── fpltest/             a fake FPL website for tests
│   └── names/               "Ødegaard" -> "odegaard"
├── gen/                     generated from proto (don't edit)
├── data/fpl.db              the SQLite file (created on start)
└── go.mod / go.sum          dependency list (like requirements.txt)
```

(Go convention: `cmd/` holds programs you can run; `internal/` holds packages
only this project can import.)

The packages form layers, and each layer only calls the ones below it:

```
handlers                                        talks gRPC; validates, converts
   │
refresh   manager   squad   optimizer   names   the logic
   │         │
  db        fpl                                 storage and downloading
```

[architecture.md](architecture.md) has this as a proper diagram, plus diagrams
of each request.

**[main.go](../fpl_data_service/cmd/api/main.go)**: startup, in order:

1. Read settings from environment variables (`GRPC_ADDR`, default `:50051`;
   `DB_PATH`, default `data/fpl.db`; `FPL_BASE_URL`, default the real website).
2. Set up Ctrl+C handling via a **context**. A context is Go's standard "cancel
   signal", passed through the program.
3. `db.Open(...)`: create or open the SQLite file and create the tables.
4. Build the FPL client, the **refresher** (keeps the cache fresh) and the
   **manager loader** (fetches managers' teams).
5. `refreshAll()`: download players, teams and fixtures if the saved copy is
   more than an hour old.
6. `grpc.NewServer()` + `RegisterFplDataServiceServer(...)`: "when a request
   arrives, run *our* method for it". Also a **health check** and
   **reflection** (so `grpcurl` can list the functions without the proto file).
7. A background goroutine re-runs `refreshAll()` every 10 minutes (every
   minute after a failure).
8. Listen on port 50051 and serve until Ctrl+C, then shut down cleanly.

Go patterns you'll see everywhere:

- `x, err := f()` then `if err != nil { ... }`. Go has no exceptions; errors are
  ordinary return values that you check straight away.
- `defer store.Close()` means "run this when the function exits", like
  `finally`.
- `go func() {...}()` runs a function in the background (a "goroutine").

**`handlers/`**: the "routes". With gRPC, each RPC in the proto is a method on
the `FplService` struct, and every method has the same shape: **check the
request → get the data → convert it to protobuf**.

- `players.go`: `SearchPlayers` (by name) and `FindPlayers` (by position, price
  and so on).
- `fixtures.go`: `GetFixtures` (accepts a team name too) and
  `GetPlayerFixtures`.
- `stats.go`: `GetPlayerRecentForm` and `GetPlayerGameweekStats`.
- `manager.go`: `GetManagerTeam`.
- `transfers.go`: `GetTransferTrends`, `EvaluateTransfers`, `SuggestTransfers`.
- `convert.go`: turns database rows into protobuf messages, including £m
  conversions (FPL stores 95 for £9.5m).
- `service.go`: the struct and helpers shared by all of them, including how
  failures become gRPC error codes (`NotFound`, `Unavailable`, …).

**`refresh/`**: one rule, used for every download: *if `sync_log` says this
dataset was saved less than an hour ago, skip; otherwise download and save it*.
Players and fixtures are refreshed on the timer; a player's match history is
downloaded the first time someone asks about him.

**`db/`**: the database, one file per kind of data (`players.go`,
`fixtures.go`, `history.go`, …).

- `schema.sql` is baked into the program (`//go:embed`) and run on every start.
  It's safe to repeat because every statement says `IF NOT EXISTS`.
- `db.go` has a **schema version**. Change `schema.sql` → bump the version → on
  the next start the old tables are dropped and rebuilt. That's fine because
  it's only a cache.
- Saving uses **transactions** (all of it saves, or none of it) and **upserts**
  (insert, or update if the row exists).
- Queries return plain Go structs (`PlayerRow`, `FixtureRow`, …), never
  protobuf, so this package doesn't depend on gRPC.

**`fpl/`**: downloads and decodes JSON from the FPL website. Go structs with
`json:"..."` tags say which JSON key fills which field. `entry.go` covers the
manager endpoints. The `testdata/` folder holds saved real responses for tests.

**`manager/`**: loads a manager's team. FPL doesn't publish everything we need,
so `rules.go` works out the rest: how many **free transfers** the team probably
has, which **chips** are left, and what each player would **sell for** (FPL
keeps half of any price rise). It also handles the Free Hit case: after a Free
Hit the squad goes back to the previous week's, so that's the one shown.

**`squad/`**: the **verifier**. Given a squad and some transfers, it builds the
new squad and lists every rule it breaks: 15 players, 2/5/5/3 by position, at
most 3 per club, within budget. It only does arithmetic on what it's given (no
database, no network), which makes it easy to test and fast.

**`optimizer/`**: will search for the best transfers by trying combinations and
checking each with the verifier. For now it has the types and a written-up
design; the RPC replies "not implemented".

**Tests**: `make test` runs about 100 Go tests without the internet.
`fpltest/` is a fake FPL website that serves the saved files from
`fpl/testdata/`; the real code is pointed at it. Each test gets a fresh
temporary database.

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
The key values are the Go address `localhost:50051`, the vLLM URL
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
- `async def` / `await`: while one request waits for the model or for Go, Python
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
- Each tool: calls Go over gRPC → converts the protobuf response to JSON text →
  returns it.
- On a gRPC failure (Go is down, Unimplemented, …) it returns the text
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
graph builds and `/health` works. Neither needs the model or Go running
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
`make proto` (regenerate code), `make run-go` / `make run-py` / `make vllm`
(start each server), `make chat` / `make chat-stream` (test questions),
`make test`, `make fmt`. Recipes start with `>` instead of a tab, so Windows
editors can't break them, and it sets `WATCHFILES_FORCE_POLLING` because
file-change events don't work on `/mnt/d`.

---

## 4. What's real and what's placeholder (as of 2026-10-10)

| Part | Status |
|---|---|
| Model server, tool calling | ✅ Works |
| Python → Go over gRPC | ✅ Works |
| Data service: players, fixtures, form, trends, managers' teams, transfer checks | ✅ Real FPL data, cached in SQLite, refreshed hourly |
| Transfer optimizer (`SuggestTransfers`) | ⚠️ Structure and wiring only; replies "not implemented" |
| Agent tools | ⚠️ Only `search_players` and `get_fixtures`; the other RPCs have no tools yet |
| Agent loop + nudge | ⚠️ Runs, but the model sometimes still ends with "Let me check…", and `/chat/stream` shows both drafts when the nudge fires |
| docker-compose | ❌ All commented out |
