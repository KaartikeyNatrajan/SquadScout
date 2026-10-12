# Squad Scout architecture

How the system fits together, with most of the detail on `fpl_data_service`.
State as of 2026-10-10. If this disagrees with the code, the code wins.

> **Viewing the diagrams.** They're written in [Mermaid](https://mermaid.js.org/).
> GitHub renders them automatically. In VS Code, install the "Markdown Preview
> Mermaid Support" extension and open the preview (Ctrl+Shift+V).

Contents:

1. [The whole system](#1-the-whole-system)
2. [Inside the data service: packages and layers](#2-inside-the-data-service-packages-and-layers)
3. [Where each piece of data comes from, and how fresh it is](#3-where-each-piece-of-data-comes-from-and-how-fresh-it-is)
4. [Startup and the background refresh](#4-startup-and-the-background-refresh)
5. [Request flows](#5-request-flows)
6. [The database](#6-the-database)
7. [The verifier and the optimizer](#7-the-verifier-and-the-optimizer)
8. [Which RPCs answer which questions](#8-which-rpcs-answer-which-questions)
9. [Errors](#9-errors)
10. [Testing without the internet](#10-testing-without-the-internet)

---

## 1. The whole system

Three programs on one machine, plus the public FPL API.

```mermaid
flowchart LR
    user(["You<br/>curl / make chat"])

    subgraph py["langgraph_service (Python)"]
        api["FastAPI<br/>:8080"]
        agentgraph["LangGraph agent<br/>agent / tools / nudge"]
        tools["tools.py<br/>gRPC client"]
        api --> agentgraph --> tools
    end

    llm["vLLM :8000<br/>Qwen2.5-3B on the GPU"]

    subgraph go["fpl_data_service (Go)"]
        grpc["gRPC server<br/>:50051"]
        sqlite[("SQLite<br/>data/fpl.db")]
        mem[["in-memory<br/>manager teams"]]
        grpc --- sqlite
        grpc --- mem
    end

    fplapi(["FPL public API<br/>fantasy.premierleague.com/api"])

    user -- "HTTP / SSE" --> api
    agentgraph -- "OpenAI-compatible HTTP" --> llm
    tools -- "gRPC (protobuf)" --> grpc
    go -- "HTTPS GET" --> fplapi
```

| Program | Job | Talks |
|---|---|---|
| `langgraph_service` | Runs the conversation: asks the model what to do, runs the tools it asks for | HTTP in, gRPC and HTTP out |
| vLLM | Runs the language model | OpenAI-style HTTP |
| `fpl_data_service` | Owns all FPL data: downloads it, caches it, answers questions about it, checks transfers | gRPC in, HTTPS out |

The contract between Python and Go is
[`proto/squadscout/v1/fpl.proto`](../proto/squadscout/v1/fpl.proto).
`make proto` generates the Go server interface and the Python client from it.

**Current gap:** the agent's tools only use `SearchPlayers` and `GetFixtures`.
The other eight RPCs exist and are tested, but the agent can't call them yet.

---

## 2. Inside the data service: packages and layers

Each box is a Go package under `fpl_data_service/internal/` (or `cmd/`).
Arrows mean "imports / calls". The layers only call downwards.

```mermaid
flowchart TB
    main["cmd/api<br/>main.go<br/>wiring, timer, shutdown"]

    subgraph l1["gRPC layer"]
        handlers["handlers<br/>one method per RPC<br/>validate, fetch, convert"]
    end

    subgraph l2["Domain logic (no I/O except where noted)"]
        refresh["refresh<br/>is the data stale?<br/>then download + save"]
        manager["manager<br/>load a manager's team<br/>free transfers, chips, selling prices"]
        squad["squad<br/>THE VERIFIER<br/>squad rules, transfers, hits"]
        optimizer["optimizer<br/>best transfers<br/>NOT IMPLEMENTED"]
        names["names<br/>lowercase + strip accents"]
    end

    subgraph l3["Data access"]
        db["db<br/>SQLite: schema, upserts, queries"]
        fpl["fpl<br/>HTTP client for the FPL API"]
    end

    gen["gen<br/>generated from fpl.proto"]
    fpltest["fpltest<br/>fake FPL API<br/>tests only"]

    main --> handlers
    main --> refresh
    main --> manager
    handlers --> refresh
    handlers --> manager
    handlers --> squad
    handlers --> optimizer
    handlers --> db
    handlers --> gen
    handlers --> names
    refresh --> fpl
    refresh --> db
    manager --> fpl
    manager --> db
    manager --> squad
    optimizer --> squad
    db --> names
    db --> fpl
    fpltest -.->|"serves saved JSON<br/>in every package's tests"| fpl
```

What lives where:

| Package | Files | Responsibility |
|---|---|---|
| `cmd/api` | `main.go` | Opens the database, builds the refresher and manager loader, registers the gRPC service, runs the refresh timer, shuts down cleanly |
| `handlers` | `service.go` shared helpers, `players.go`, `fixtures.go`, `stats.go`, `manager.go`, `transfers.go`, `outlook.go`, `convert.go` | The 10 RPCs. Validate input, call the layers below, convert rows to protobuf, map failures to gRPC codes |
| `refresh` | `refresh.go` | The single freshness rule (`ifStale`) for bootstrap, fixtures and per-player history |
| `manager` | `manager.go` downloading + cache, `rules.go` pure FPL rules | A manager's team: Free Hit revert, free-transfer estimate, chips left, selling prices |
| `squad` | `squad.go` | The verifier: FPL squad rules, applying transfers, hit cost. Pure functions |
| `optimizer` | `optimizer.go` | Types and design for transfer search; `Suggest` returns `ErrNotImplemented` |
| `names` | `names.go` | `Normalize`: "Ødegaard" → "odegaard" |
| `db` | one file per kind of data | SQLite schema (versioned), upserts, queries. Returns its own row types, never protobuf |
| `fpl` | `client.go`, `entry.go` | Downloads and decodes FPL JSON. No storage, no decisions |
| `fpltest` | `fpltest.go` | Fake FPL API for tests, serving `internal/fpl/testdata/*.json` |

Two design rules hold throughout:

- **`db` never imports gRPC code** and **`squad` / `optimizer` never do I/O.** Each
  layer can be tested on its own, and the verifier is cheap enough for the
  optimizer to call thousands of times.
- **Arithmetic happens in Go, not in the model.** Budgets, hits, selling prices,
  totals and rule checks are all computed here, because a 3B model gets sums wrong.

---

## 3. Where each piece of data comes from, and how fresh it is

There are three caches with different lifetimes.

```mermaid
flowchart LR
    subgraph api["FPL API"]
        bs["GET bootstrap-static"]
        fx["GET fixtures"]
        es["GET element-summary/id"]
        en["GET entry/id<br/>+ history, picks, transfers"]
    end

    subgraph cache["fpl_data_service"]
        direction TB
        t1[("SQLite<br/>teams, events, chips, players")]
        t2[("SQLite<br/>fixtures")]
        t3[("SQLite<br/>player_match_stats")]
        m1[["memory<br/>manager.Team per team id"]]
        log[("sync_log<br/>when each dataset was saved")]
    end

    bs -- "timer: if older than 1 h" --> t1
    fx -- "timer: if older than 1 h" --> t2
    es -- "on request: if older than 1 h" --> t3
    en -- "on request: if older than 5 min" --> m1
    t1 & t2 & t3 -.-> log
```

| Data | Source | Stored in | Refreshed | If the FPL API is down |
|---|---|---|---|---|
| Teams, gameweeks, chip rules, players (prices, form, news, expected points, transfer counts) | `/bootstrap-static/` (~3 MB) | SQLite | Timer checks every 10 min, downloads if > 1 h old | Old copy served; retry in 1 min |
| All 380 fixtures with difficulty | `/fixtures/` | SQLite | Same timer, right after bootstrap (it needs the teams) | Old copy served |
| One player's match history | `/element-summary/{id}/` | SQLite | When a request needs it and it's > 1 h old | Old copy served; `Unavailable` if never downloaded |
| One manager's team | `/entry/{id}/` + `/history/` + `/picks/` + `/transfers/` | Memory | When requested and > 5 min old | `Unavailable` |

`sync_log` holds one timestamp per dataset (`bootstrap`, `fixtures`,
`element-summary/12`, …). It's written **in the same transaction** as the data,
so it can never claim a download that didn't save.

**Schema versions.** `db.go` has `schemaVersion`. When `schema.sql` changes,
bump it: on the next start `Open` sees the old number in SQLite's
`PRAGMA user_version`, drops every table and rebuilds them. That's safe because
everything is re-downloadable. Until the first refresh succeeds, the service has
no data; that's why failed refreshes retry after 1 minute.

---

## 4. Startup and the background refresh

```mermaid
sequenceDiagram
    autonumber
    participant M as main.go
    participant DB as db.Store
    participant R as refresh.Refresher
    participant F as fpl.Client
    participant API as FPL API
    participant G as gRPC server

    M->>DB: Open(data/fpl.db)
    Note over DB: schema version differs?<br/>drop all tables, rebuild
    M->>R: refreshAll() with 1-minute timeout
    R->>DB: LastSynced("bootstrap")
    alt older than 1 hour, or never
        R->>F: FetchBootstrap
        F->>API: GET /bootstrap-static/
        API-->>F: teams, events, chips, players
        R->>DB: UpsertBootstrap (one transaction + sync_log)
    end
    R->>DB: LastSynced("fixtures")
    alt older than 1 hour, or never
        R->>F: FetchFixtures
        F->>API: GET /fixtures/
        R->>DB: UpsertFixtures (one transaction + sync_log)
    end
    M->>G: Serve on :50051
    loop every 10 min (every 1 min after a failure)
        M->>R: refreshAll()
    end
    Note over M,G: Ctrl+C cancels the context:<br/>timer loop stops, GracefulStop<br/>lets in-flight requests finish
```

---

## 5. Request flows

### 5a. A player question: `GetPlayerRecentForm`

Histories are downloaded per player, the first time someone asks about him.

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (stats.go)
    participant DB as db.Store
    participant R as refresh.Refresher
    participant API as FPL API

    A->>H: GetPlayerRecentForm(player_id=12, last_n=3)
    H->>DB: GetPlayer(12)
    alt no such player
        H-->>A: NotFound + "use SearchPlayers"
    end
    H->>R: PlayerHistory(12)
    R->>DB: LastSynced("element-summary/12")
    opt older than 1 hour, or never
        R->>API: GET /element-summary/12/
        R->>DB: UpsertPlayerHistory (replace all rows + sync_log)
    end
    alt download failed and nothing cached
        H-->>A: Unavailable
    else download failed but a copy exists
        Note over H: log a warning, use the copy
    end
    H->>DB: RecentMatches(12, 3)
    Note over H: summarize(): totals, appearances,<br/>points per match (rounded)
    H-->>A: matches (most recent first) + summary
```

### 5b. A manager question: `GetManagerTeam`

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (manager.go)
    participant L as manager.Loader
    participant API as FPL API
    participant DB as db.Store

    A->>H: GetManagerTeam(team_id=6)
    H->>L: Load(6)
    alt cached less than 5 min ago
        L-->>H: cached Team
    else
        L->>API: GET /entry/6/
        Note over L: 404 = ErrTeamNotFound<br/>current_event null = ErrNoSquadYet
        L->>API: GET /entry/6/event/{last}/picks/
        opt a Free Hit was played last gameweek
            Note over L: the squad reverts afterwards
            L->>API: GET /entry/6/event/{last-1}/picks/
        end
        L->>API: GET /entry/6/history/
        L->>API: GET /entry/6/transfers/
        L->>DB: Chips(), PlayersByID(15 ids)
        Note over L: rules.go:<br/>FreeTransfers, ChipsAvailable,<br/>PurchasePrices, SellingPrice
        L-->>H: Team (+ cache it)
    end
    H->>DB: PlayersByID, UpcomingFixtures per club
    H-->>A: 15 SquadPlayers + bank, free transfers,<br/>chips, deadline, notes
```

### 5c. Checking transfers: `EvaluateTransfers`

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (transfers.go)
    participant L as manager.Loader
    participant DB as db.Store
    participant V as squad (verifier)

    A->>H: EvaluateTransfers(team 6, out [124], in [12], max_hit 4)
    H->>L: Load(6)
    H->>DB: PlayersByID(squad + outs + ins)
    alt an unknown player id
        H-->>A: NotFound
    end
    H->>V: Apply(squad at selling prices, outs, ins at current prices)
    V-->>H: list of broken rules (maybe empty)
    Note over H: + chip checks (wildcard / freehit available?)<br/>+ HitCost vs max_hit<br/>+ expected points in vs out
    H-->>A: valid, violations, hit, bank before/after,<br/>expected points, both players' outlooks
```

Rule breaks are **answers, not errors**: "you'd have 4 Arsenal players" comes back
as `valid: false` with a violation the agent can explain. gRPC errors are kept for
requests that can't be evaluated at all.

---

## 6. The database

`internal/db/schema.sql`, schema version 3.

```mermaid
erDiagram
    teams ||--o{ players : "plays for"
    teams ||--o{ fixtures : "home team"
    teams ||--o{ fixtures : "away team"
    players ||--o{ player_match_stats : "one row per match"
    teams ||--o{ player_match_stats : "opponent"

    teams {
        int id PK "FPL team id, Arsenal = 1"
        text name "Man Utd"
        text short_name "MUN"
    }
    events {
        int id PK "gameweek 1-38"
        text deadline_time
        int finished
        int is_current
        int is_next
    }
    chips {
        int id PK
        text name "wildcard, freehit, bboost, 3xc"
        int start_event
        int stop_event
    }
    players {
        int id PK "FPL element id"
        text web_name
        text search_name "normalised, for LIKE"
        int team_id FK
        int position "1 GK to 4 FWD"
        int now_cost "tenths of a pound million"
        real form
        text status "a d i s u n"
        text news
        int chance_of_playing_next_round "NULL = no concerns"
        real ep_next "FPL expected points"
        int transfers_in_event
        int transfers_out_event
    }
    fixtures {
        int id PK
        int gameweek "NULL if unscheduled"
        int home_team_id FK
        int away_team_id FK
        text kickoff_time
        int home_difficulty "1-5"
        int away_difficulty "1-5"
        int finished
        int home_score "NULL until played"
    }
    player_match_stats {
        int player_id PK
        int fixture_id PK "no FK on purpose"
        int gameweek
        int opponent_team_id FK
        int was_home
        int team_score "from his side"
        int minutes
        int total_points
        real expected_goals
    }
    sync_log {
        text dataset PK "bootstrap, fixtures, element-summary/12"
        text synced_at "RFC 3339 UTC"
    }
```

(Only the main columns are shown; `players` and `player_match_stats` have
more stats.) Notes:

- `player_match_stats` is keyed by **(player, fixture)**, not (player, gameweek),
  because a team can play twice in a double gameweek.
- `events`, `chips` and `sync_log` stand alone: they're looked up, not joined.
- Foreign keys are enforced, so teams must be saved before players and fixtures.
  Each upsert runs in one transaction: all of it saves, or none of it.

---

## 7. The verifier and the optimizer

### The verifier (`internal/squad`): implemented

```mermaid
flowchart TD
    start["Apply(squad, outIDs, ins)"]
    c1{"same number<br/>out and in?"}
    c2{"every player out<br/>is in the squad?"}
    c3{"no player in is<br/>already owned?"}
    stop["return the problems<br/>(squad unchanged)"]
    build["build the new squad<br/>bank += selling prices of outs<br/>bank -= prices of ins"]
    v["Validate(new squad)"]
    r1["15 players?"]
    r2["2 GK, 5 DEF, 5 MID, 3 FWD?"]
    r3["at most 3 per club?"]
    r4["bank not negative?"]
    result["new squad + every rule broken,<br/>in plain words"]

    start --> c1 --> c2 --> c3
    c1 -- no --> stop
    c2 -- no --> stop
    c3 -- no --> stop
    c3 -- yes --> build --> v
    v --> r1 & r2 & r3 & r4 --> result
```

`HitCost(transfers, free, chip)` is separate: 4 points per transfer beyond the
free ones, or 0 with a Wildcard or Free Hit.

### The optimizer (`internal/optimizer`): wired, not implemented

```mermaid
flowchart LR
    rpc["SuggestTransfers RPC<br/>(handlers/transfers.go)"]
    req["optimizer.Request<br/>squad, free transfers, max hit,<br/>expected points, candidates"]
    sug["optimizer.Suggest"]
    todo["TODO: enumerate swap<br/>combinations"]
    ver["squad.Apply<br/>(the verifier)"]
    score["score: expected gain<br/>minus HitCost"]
    resp["best suggestions"]
    unimpl["today: ErrNotImplemented<br/>= gRPC Unimplemented"]

    rpc -- "gathers squad + top 10<br/>candidates per position" --> req --> sug
    sug -.-> todo -.-> ver -.-> score -.-> resp
    sug --> unimpl
```

Dashed arrows are the planned path (described in `optimizer.go`); the solid
path is what runs today. Everything before `Suggest` is already built, so
implementing the optimizer means writing that one function.

---

## 8. Which RPCs answer which questions

```mermaid
flowchart LR
    q1["Who should I captain?"]
    q2["What transfers should I make?"]
    q3["Is Saka worth a hit?"]
    q4["Injury news for my team?"]
    q5["Most transferred in, and why?"]
    q6["How has X been playing?"]
    q7["X's upcoming fixtures?"]

    mt["GetManagerTeam"]
    fp["FindPlayers"]
    et["EvaluateTransfers"]
    sp["SearchPlayers"]
    tt["GetTransferTrends"]
    rf["GetPlayerRecentForm"]
    pf["GetPlayerFixtures"]
    gf["GetFixtures"]

    q1 --> mt
    q1 -. optional .-> rf
    q2 --> mt --> fp --> et
    q3 --> mt
    q3 --> sp --> et
    q4 --> mt
    q5 --> tt
    q6 --> sp --> rf
    q7 --> pf
    q7 --> gf
```

All 10 RPCs:

| RPC | Answers | Data from |
|---|---|---|
| `SearchPlayers` | Players by name, best first, with season stats, availability, news, expected points | SQLite (bootstrap) |
| `FindPlayers` | Players by position / price / availability, sorted by a measure, with next fixtures | SQLite (bootstrap, fixtures) |
| `GetTransferTrends` | Most transferred in or out since the last deadline, with next fixtures | SQLite (bootstrap, fixtures) |
| `GetPlayerRecentForm` | Last N matches + totals | SQLite (history, on demand) |
| `GetPlayerGameweekStats` | One gameweek's stats (totals + each match) | SQLite (history, on demand) |
| `GetPlayerFixtures` | Next fixtures from his team's side + expected points next gameweek | SQLite (fixtures, bootstrap) |
| `GetFixtures` | A team's next fixtures, by id or name | SQLite (fixtures) |
| `GetManagerTeam` | Squad, bank, free transfers, chips, deadline, caveats | Memory (manager) + SQLite |
| `EvaluateTransfers` | Is this set of transfers allowed, what it costs, expected gain | Memory + SQLite + verifier |
| `SuggestTransfers` | Not implemented yet | (wired to the optimizer) |

---

## 9. Errors

Every handler returns one of these gRPC codes. The Python tools turn any error
into an `ERROR: ...` string so the model can tell the user what went wrong.
Messages include a hint for recovering where one exists.

| Code | Meaning | Example |
|---|---|---|
| `InvalidArgument` | The request itself is malformed | empty name, `player_id` 0, negative `max_price` |
| `NotFound` | Well-formed, but no such thing | unknown player or team id, unknown team name, a gameweek he didn't play |
| `FailedPrecondition` | Exists, but can't be used yet | a team created after the last deadline |
| `Unavailable` | FPL API down and nothing cached | first request for a player's history while FPL is down |
| `Internal` | Our bug or a database failure | (logged with details) |
| `Unimplemented` | Not built yet | `SuggestTransfers` |

Squad-rule breaks in `EvaluateTransfers` are **not** errors; see 5c.

---

## 10. Testing without the internet

```mermaid
flowchart LR
    td[("internal/fpl/testdata<br/>saved real responses:<br/>bootstrap, fixtures,<br/>Saka's history,<br/>teams 1 and 6")]
    fake["fpltest.Server<br/>fake FPL API<br/>counts hits, can go down"]
    client["fpl.Client<br/>pointed at the fake"]
    real["the real code:<br/>refresh, manager, db, handlers"]
    tmp[("temporary SQLite<br/>per test")]

    td --> fake --> client --> real --> tmp
```

- `make test` runs about 100 Go tests and the Python tests, with no network,
  GPU or servers needed.
- The saved data was chosen to cover tricky cases: team 1 played a **Free Hit**
  the week before; team 6 has **3 Arsenal and 3 Chelsea players** (so buying
  Saka breaks the club limit); Saka's history includes away games, so the
  score must be flipped to his side.
- `squad`, `names` and `manager/rules.go` are pure functions, tested with
  made-up inputs (table-driven tests).
- To refresh the saved data, download the same URLs again into
  `internal/fpl/testdata/`. The expected values in the tests will change with it.
