# Squad Scout architecture

How the system fits together, with most of the detail on `fpl_data_service`.
State as of 2026-10-25. The data service is Python (it was ported from Go; the Go version is in `legacy/`). If this disagrees with the code, the code wins.

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
11. [Porting notes: Go to Python](#11-porting-notes-go-to-python)

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

    subgraph dataservice["fpl_data_service (Python)"]
        grpc["gRPC server<br/>:50051"]
        sqlite[("SQLite<br/>data/fpl.db<br/>all FPL data + saved teams")]
        timer["refresh timer<br/>every 10 min"]
        grpc --- sqlite
        timer --> sqlite
    end

    fplapi(["FPL public API<br/>fantasy.premierleague.com/api"])

    user -- "HTTP / SSE" --> api
    agentgraph -- "OpenAI-compatible HTTP" --> llm
    tools -- "gRPC (protobuf)" --> grpc
    dataservice -- "HTTPS GET" --> fplapi
```

| Program | Job | Talks |
|---|---|---|
| `langgraph_service` | Runs the conversation: asks the model what to do, runs the tools it asks for | HTTP in, gRPC and HTTP out |
| vLLM | Runs the language model | OpenAI-style HTTP |
| `fpl_data_service` | Owns all FPL data: downloads it in the background, keeps it in SQLite, answers questions about it, checks transfers | gRPC in, HTTPS out |

The contract between the agent and the data service is
[`proto/squadscout/v1/fpl.proto`](../proto/squadscout/v1/fpl.proto).
`make proto` generates the server code (data service) and the client code (agent) from it.

**Requests are answered from SQLite.** The only FPL call made while answering
a request is downloading a manager's team, the first time it's asked about in
a gameweek (see [section 3](#3-where-each-piece-of-data-comes-from-and-how-fresh-it-is)).

**Current gap:** the agent's tools only use `SearchPlayers` and `GetFixtures`.
The other eight RPCs exist and are tested, but the agent can't call them yet.

---

## 2. Inside the data service: packages and layers

Each box is a module or package under `fpl_data_service/src/fpl_data/`.
Arrows mean "imports / calls". The layers only call downwards.

```mermaid
flowchart TB
    main["main.py<br/>wiring, timer, shutdown<br/>server.py: gRPC adapter"]

    subgraph l1["gRPC layer"]
        handlers["handlers<br/>one method per RPC<br/>validate, fetch, convert"]
    end

    subgraph l2["Domain logic (no I/O except where noted)"]
        refresh["refresh<br/>background downloads:<br/>stale? download + save"]
        manager["manager<br/>a manager's team, saved<br/>until the next deadline"]
        squad["squad<br/>THE VERIFIER<br/>squad rules, transfers, hits"]
        lineup["lineup<br/>best starting eleven"]
        projection["projection<br/>points over the next<br/>few gameweeks"]
        optimizer["optimizer<br/>best 1-3 transfers"]
        names["names<br/>lowercase + strip accents"]
    end

    subgraph l3["Data access"]
        db["db<br/>SQLite: schema, upserts, queries"]
        fpl["fpl<br/>HTTP client for the FPL API"]
    end

    gen["squadscout<br/>generated from fpl.proto"]
    fpltest["fpltest<br/>fake FPL API<br/>tests only"]

    main --> handlers
    main --> refresh
    main --> manager
    handlers --> manager
    handlers --> squad
    handlers --> optimizer
    handlers --> lineup
    handlers --> projection
    handlers --> db
    handlers --> gen
    handlers --> names
    refresh --> fpl
    refresh --> db
    manager --> fpl
    manager --> db
    manager --> squad
    optimizer --> squad
    optimizer --> lineup
    db --> names
    db --> fpl
    fpltest -.->|"serves saved JSON<br/>in every package's tests"| fpl
```

What lives where:

| Module / package | Files | Responsibility |
|---|---|---|
| `main.py`, `server.py` | | `main.py` opens the database, builds the refresher and manager loader, starts the gRPC server, runs the refresh timer, shuts down cleanly. `server.py` is the thin gRPC adapter: CamelCase RPC names → `FplService` methods, `ServiceError` → gRPC status, plus health check and reflection |
| `handlers/` | `service.py` (start here), `base.py` shared helpers, `players.py`, `fixtures.py`, `stats.py`, `manager_team.py`, `transfers.py`, `projector.py` (gathers a player's data for the projection), `fixture_cache.py`, `convert.py`, `errors.py` | The 10 RPCs. Plain async methods: validate input, call the layers below, convert rows to protobuf, raise `ServiceError` with a gRPC code |
| `refresh.py` | | Background downloads of bootstrap, fixtures and gameweek stats, with one freshness rule (`_if_stale`) |
| `manager/` | `loader.py` downloading + saving, `rules.py` pure FPL rules | A manager's team: Free Hit revert, free-transfer count, chips left, selling prices (from price history); saved in SQLite until the next deadline |
| `squad.py` | | The verifier: FPL squad rules, applying transfers, hit cost. Pure functions |
| `lineup.py` | | The best starting eleven of a squad by projected points (only 11 of 15 score). Pure |
| `projection.py` | | Expected points over the next few gameweeks from recent minutes and points, availability and fixture difficulty. Pure; backtested constants |
| `optimizer.py` | | Tries every 1-3 swap, checks each with the verifier, ranks by the change in the best eleven minus the hit. Pure, so it runs in a worker thread |
| `names.py` | | `normalize`: "Ødegaard" → "odegaard" |
| `db/` | one module per kind of data | SQLite schema (versioned), upserts, queries. Returns its own row dataclasses, never protobuf |
| `fpl/` | `client.py`, `models.py` | Downloads FPL JSON and decodes it into Pydantic models (which also convert FPL's `"6.2"` strings and nulls). Limits requests in flight and obeys 429 `Retry-After`. No storage, no decisions |
| `tests/fake_fpl.py` | | Fake FPL API for tests (an `httpx.MockTransport`), serving `tests/testdata/*.json` |

Two design rules hold throughout:

- **`db` never imports gRPC code** and **`squad` / `optimizer` never do I/O.** Each
  layer can be tested on its own, and the verifier is cheap enough for the
  optimizer to call thousands of times.
- **Arithmetic happens in the data service, not in the model.** Budgets, hits, selling prices,
  totals and rule checks are all computed here, because a 3B model gets sums wrong.

---

## 3. Where each piece of data comes from, and how fresh it is

Everything lives in SQLite. Three kinds of data are downloaded by the
background timer; managers' teams are downloaded when first asked for.

```mermaid
flowchart LR
    subgraph api["FPL API"]
        bs["GET bootstrap-static"]
        fx["GET fixtures"]
        ev["GET event/gw/live<br/>one per gameweek"]
        en["GET entry/id<br/>+ history, picks, transfers"]
        es["GET element-summary/id<br/>price history"]
    end

    subgraph store["SQLite (data/fpl.db)"]
        direction TB
        t1[("teams, events,<br/>chips, players")]
        t2[("fixtures")]
        t3[("player_gameweeks,<br/>player_gameweek_matches")]
        t4[("manager_teams")]
        t5[("player_prices")]
        log[("sync_log<br/>when each dataset was saved")]
    end

    bs -- "timer: if older than 1 h" --> t1
    fx -- "timer: if older than 1 h" --> t2
    ev -- "timer: current + previous week<br/>if older than 1 h; older weeks once" --> t3
    en -- "on request: until the<br/>next deadline" --> t4
    es -- "on request, once per player,<br/>kept forever" --> t5
    t1 & t2 & t3 -.-> log
```

| Data | Source | Refreshed | If the FPL API is down |
|---|---|---|---|
| Teams, gameweeks, chip rules, players (prices, form, news, expected points, transfer counts) | `/bootstrap-static/` (~3 MB) | Timer checks every 10 min, downloads if > 1 h old | Old copy served; retry in 1 min |
| All 380 fixtures with difficulty | `/fixtures/` | Same timer, right after bootstrap (it needs the teams) | Old copy served |
| Every player's stats, per gameweek | `/event/{gw}/live/` (~0.5 MB each, all players) | Same timer. The current and previous gameweeks every hour (points, bonus and corrections still change); older ones once, then never again | Old copy served |
| One manager's team | `/entry/{id}/` + `/history/` + `/picks/` + `/transfers/` (the last three fetched at the same time) | When first requested, then saved until the next deadline (FPL hides new picks until then) | Saved copy served with a warning note; `Unavailable` if never downloaded |
| A player's price in each gameweek | `/element-summary/{id}/` | Only for players a manager has held since the start (to learn their purchase price); a past price never changes, so each is kept forever | That player's selling price falls back to an estimate (his current price), flagged |

`sync_log` holds one timestamp per downloaded dataset (`bootstrap`, `fixtures`,
`event-live/5`, …). It's written **in the same transaction** as the data, so it
can never claim a download that didn't save. Each gameweek is a separate
dataset, so an interrupted first run carries on where it stopped.

Manager teams track freshness themselves: each row in `manager_teams` has a
`valid_until` (the next deadline). A first request for a team makes 6 or more
FPL requests, and takes about 2-3 seconds live; every later request that
gameweek is instant, even after a restart.

**Being polite to FPL.** The client never has more than 4 requests in flight. On
HTTP 429 ("too many requests") it waits as long as the `Retry-After` header
says (up to 10 seconds, at most twice). If FPL asks for longer, it raises
`RateLimitedError`, and the background refresh then waits that long before its
next attempt.

**Why per gameweek, not per player?** `/event/{gw}/live/` returns every player's
stats for one week in one response. A whole season so far is at most 38
downloads, instead of one per player (~700), and finished weeks never need
downloading again. FPL only splits minutes and points per match; other stats
are weekly totals, which matters only in a double gameweek.

**Schema versions.** `db/store.py` has `SCHEMA_VERSION`. When `schema.sql` changes,
bump it: on the next start `Open` sees the old number in SQLite's
`PRAGMA user_version`, drops every table and rebuilds them. That's safe because
everything is re-downloadable (saved manager teams are simply downloaded again).
Until the first refresh succeeds, the service has no data; that's why failed
refreshes retry after 1 minute.

---

## 4. Startup and the background refresh

```mermaid
sequenceDiagram
    autonumber
    participant M as main.py
    participant DB as db.Store
    participant R as refresh.Refresher
    participant F as fpl.Client
    participant API as FPL API
    participant G as gRPC server

    M->>DB: Store.open(data/fpl.db)
    Note over DB: schema version differs?<br/>drop all tables, rebuild
    M->>R: refresh_all() with 2-minute timeout
    R->>DB: last_synced("bootstrap")
    alt older than 1 hour, or never
        R->>F: fetch_bootstrap
        F->>API: GET /bootstrap-static/
        API-->>F: teams, events, chips, players
        R->>DB: upsert_bootstrap (one transaction + sync_log)
    end
    R->>DB: last_synced("fixtures")
    alt older than 1 hour, or never
        R->>F: fetch_fixtures
        F->>API: GET /fixtures/
        R->>DB: upsert_fixtures (one transaction + sync_log)
    end
    loop each played gameweek 1..current
        R->>DB: last_synced("event-live/gw")
        alt never, or (one of the last two weeks and older than 1 hour)
            R->>F: fetch_event_live(gw)
            F->>API: GET /event/gw/live/
            R->>DB: upsert_gameweek_stats (replace that week + sync_log)
        end
    end
    M->>G: Serve on :50051
    loop every 10 min (after a failure: 1 min, or as long as FPL asked)
        M->>R: refresh_all()
    end
    Note over M,G: Ctrl+C cancels the context:<br/>timer loop stops, GracefulStop<br/>lets in-flight requests finish
```

---

## 5. Request flows

### 5a. A player question: `GetPlayerRecentForm`

Answered from SQLite alone; the background refresh has already downloaded
every gameweek.

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (stats.py)
    participant DB as db.Store

    A->>H: GetPlayerRecentForm(player_id=12, last_n=3)
    H->>DB: get_player(12)
    alt no such player
        H-->>A: NotFound + "use SearchPlayers"
    end
    H->>DB: recent_gameweeks(12, 3)
    Note over DB: player_gameweeks (weekly totals)<br/>+ player_gameweek_matches joined with<br/>fixtures and teams (opponent, score)
    Note over H: summarize(): totals, appearances,<br/>points per gameweek (rounded)
    H-->>A: gameweeks (most recent first),<br/>each with its match results, + summary
```

### 5b. A manager question: `GetManagerTeam`

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (manager_team.py)
    participant L as manager.Loader
    participant API as FPL API
    participant DB as db.Store

    A->>H: GetManagerTeam(team_id=6)
    H->>L: load(6)
    L->>DB: load_manager_team(6)
    alt saved, and the next deadline hasn't passed
        DB-->>L: saved Team (JSON)
        L-->>H: saved Team
    else
        L->>API: GET /entry/6/
        Note over L: 404 = TeamNotFoundError<br/>current_event null = NoSquadYetError
        par at the same time
            L->>API: GET /entry/6/event/{last}/picks/
        and
            L->>API: GET /entry/6/history/
        and
            L->>API: GET /entry/6/transfers/
        end
        opt a Free Hit was played last gameweek
            Note over L: the squad reverts afterwards
            L->>API: GET /entry/6/event/{last-1}/picks/
        end
        L->>DB: chips(), players_by_id(15 ids)
        opt players held since the start whose price history isn't stored
            L->>API: GET /element-summary/{id}/ (up to 4 at a time)
            L->>DB: save_prices
        end
        Note over L: rules.py:<br/>free_transfers, chips_available,<br/>purchase_prices, selling_price
        L->>DB: save_manager_team(valid until the next deadline)
        L-->>H: Team
    end
    Note over L: if FPL is down but a saved copy<br/>exists: return it with a warning note
    H->>DB: players_by_id, upcoming_fixtures per club,<br/>recent gameweeks (for each projection)
    H-->>A: 15 SquadPlayers (each with fixtures and a projection)<br/>+ bank, free transfers, chips, deadline, notes
```

### 5c. Checking transfers: `EvaluateTransfers`

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (transfers.py)
    participant L as manager.Loader
    participant DB as db.Store
    participant V as squad (verifier)

    A->>H: EvaluateTransfers(team 6, out [124], in [12], max_hit 4)
    H->>L: load(6)
    Note over H: current_squad_ids / current_bank /<br/>free_transfers_available, if given,<br/>replace the last deadline's squad
    H->>DB: players_by_id(squad + outs + ins)
    alt an unknown player id
        H-->>A: NotFound
    end
    H->>V: apply(squad at selling prices, outs, ins at current prices)
    V-->>H: list of broken rules (maybe empty)
    Note over H: + chip checks (wildcard / freehit available?)<br/>+ hit_cost vs max_hit<br/>+ projections over the horizon,<br/>scored as the change in the best eleven (lineup.py)
    H-->>A: valid, violations, hit, bank before/after,<br/>expected + projected points, both players' outlooks
```

Rule breaks are **answers, not errors**: "you'd have 4 Arsenal players" comes back
as `valid: false` with a violation the agent can explain. gRPC errors are kept for
requests that can't be evaluated at all.

### 5d. Finding the best transfers: `SuggestTransfers`

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (Python)
    participant H as handlers (transfers.py)
    participant L as manager.Loader
    participant DB as db.Store
    participant O as optimizer (worker thread)

    A->>H: SuggestTransfers(team 6, max_transfers 2)
    H->>L: load(6)
    Note over H: current_squad_ids etc. override the squad if given
    H->>DB: find_players: 25 best by expected points per position,<br/>not already owned
    H->>DB: project every squad player and candidate<br/>(recent gameweeks + fixtures)
    H->>O: suggest(squad, projections, candidates)
    Note over O: prune candidates, try every 1-3 swap,<br/>verify with squad.apply, score with lineup.py
    O-->>H: legal transfer sets that gain points, best first
    H-->>A: suggestions with players out/in, prices,<br/>hit, bank after, projected gain
```

Everything the optimizer needs is handed to it as plain values, and it does no
database or network calls, which is why it can safely run in a worker thread
(`asyncio.to_thread`) without blocking other requests.

---

## 6. The database

`src/fpl_data/db/schema.sql`, schema version 5.

```mermaid
erDiagram
    teams ||--o{ players : "plays for"
    teams ||--o{ fixtures : "home team"
    teams ||--o{ fixtures : "away team"
    players ||--o{ player_prices : "price per gameweek"
    players ||--o{ player_gameweeks : "one row per gameweek"
    player_gameweeks ||--|{ player_gameweek_matches : "one or two matches"
    fixtures ||--o{ player_gameweek_matches : "joined on fixture_id"

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
    player_gameweeks {
        int player_id PK
        int gameweek PK
        int minutes "weekly totals"
        int goals_scored
        int total_points
        real expected_goals
    }
    player_gameweek_matches {
        int player_id PK
        int fixture_id PK
        int gameweek FK
        int team_id "his club, to know his side"
        int minutes
        int total_points
    }
    player_prices {
        int player_id PK
        int gameweek PK
        int price "tenths of a pound million"
    }
    manager_teams {
        int team_id PK
        text data "manager.Team as JSON"
        text fetched_at
        text valid_until "the next deadline"
    }
    sync_log {
        text dataset PK "bootstrap, fixtures, event-live/5"
        text synced_at "RFC 3339 UTC"
    }
```

(Only the main columns are shown; `players` and `player_gameweeks` have more
stats.) Notes:

- `player_gameweeks` holds FPL's **weekly totals**; `player_gameweek_matches`
  holds the one or two matches behind each week (FPL only splits minutes and
  points per match). The opponent and score come from joining `fixtures`.
- `fixture_id` deliberately has no foreign key, so the gameweek and fixture
  downloads stay independent.
- `events`, `chips`, `manager_teams` and `sync_log` stand alone: they're looked
  up, not joined.
- Foreign keys are enforced, so teams must be saved before players and fixtures.
  Each upsert runs in one transaction: all of it saves, or none of it.

---

## 7. The verifier and the optimizer

### The verifier (`squad.py`): implemented

```mermaid
flowchart TD
    start["apply(squad, out_ids, ins)"]
    c1{"same number<br/>out and in?"}
    c2{"every player out<br/>is in the squad?"}
    c3{"no player in is<br/>already owned?"}
    stop["return the problems<br/>(squad unchanged)"]
    build["build the new squad<br/>bank += selling prices of outs<br/>bank -= prices of ins"]
    v["validate(new squad)"]
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

`hit_cost(transfers, free, chip)` is separate: 4 points per transfer beyond the
free ones, or 0 with a Wildcard or Free Hit.

### Scoring: the best starting eleven (`lineup.py`)

Only 11 of a squad's 15 players score, so a transfer is worth the change in the
**best eleven's** projected points, not in the sum of 15. A better substitute
adds nothing; a new player good enough to start gains the difference to whoever
he displaces. The eleven is the best valid formation (1 GK, at least 3 DEF, 2
MID and 1 FWD): 3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-2-3, 5-3-2 or 5-4-1.

### The optimizer (`optimizer.py`)

```mermaid
flowchart TD
    start["Request: squad (selling prices, bank), free transfers,<br/>max transfers, projections, candidate players"]
    prune["prune candidates per position:<br/>drop anyone another candidate beats on both<br/>price and points (keep the top 4 as fallbacks)"]
    sell["for 1, 2, 3 transfers: every set of players to sell"]
    bound{"even the best possible<br/>replacements can't make<br/>the top 5?"}
    buy["every way to replace them<br/>with same-position candidates"]
    cheap{"affordable, and can it<br/>still make the top 5?"}
    xi["gain = best eleven after - best eleven before - hit"]
    ver["squad.apply: all squad rules<br/>(15 players, positions, 3 per club, budget)"]
    keep["keep the best 5, best first"]

    start --> prune --> sell --> bound
    bound -- yes, skip --> sell
    bound -- no --> buy --> cheap
    cheap -- no --> buy
    cheap -- yes --> xi --> ver --> keep
```

Two safeguards: the pruning can't lose the best answer (a test compares the
optimizer with a plain brute force over 12 random squads and finds the same
best gain), and every suggestion is re-checked by `EvaluateTransfers` in the
tests, which must agree on validity, hit and gain. Typical run time is
0.1 to 0.4 seconds.

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
    st["SuggestTransfers"]
    sp["SearchPlayers"]
    tt["GetTransferTrends"]
    rf["GetPlayerRecentForm"]
    pf["GetPlayerFixtures"]
    gf["GetFixtures"]

    q1 --> mt
    q1 -. optional .-> rf
    q2 --> st
    q2 -. "or by hand" .-> mt --> fp --> et
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
| `FindPlayers` | Players by position / price / availability, sorted by a measure, with next fixtures and a projection | SQLite (bootstrap, fixtures, gameweek stats) |
| `GetTransferTrends` | Most transferred in or out since the last deadline, with next fixtures | SQLite (bootstrap, fixtures) |
| `GetPlayerRecentForm` | Last N gameweeks + totals | SQLite (gameweek stats) |
| `GetPlayerGameweekStats` | One gameweek's stats, with each match's minutes and points | SQLite (gameweek stats) |
| `GetPlayerFixtures` | Next fixtures from his team's side + expected points next gameweek | SQLite (fixtures, bootstrap) |
| `GetFixtures` | A team's next fixtures, by id or name | SQLite (fixtures) |
| `GetManagerTeam` | Squad, bank, free transfers, chips, deadline, caveats | FPL on first request per gameweek, then SQLite |
| `EvaluateTransfers` | Is this set of transfers allowed, what it costs, expected and projected gain (optionally for a squad/bank the manager states) | Same, + the verifier |
| `SuggestTransfers` | The best 1-3 transfers, each already checked against every squad rule | Same + the optimizer |

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
| `Unavailable` | FPL API down or rate-limiting, and nothing saved | first request for a manager's team while FPL is down |
| `Internal` | Our bug or a database failure | (logged with details) |

Squad-rule breaks in `EvaluateTransfers` are **not** errors; see 5c.

---

## 10. Testing without the internet

```mermaid
flowchart LR
    td[("tests/testdata<br/>saved real responses:<br/>bootstrap, fixtures,<br/>gameweeks 1-5 live,<br/>teams 1 and 6")]
    fake["FakeFpl (tests/fake_fpl.py)<br/>fake FPL API<br/>counts hits, can go down"]
    client["FplClient<br/>pointed at the fake"]
    real["the real code:<br/>refresh, manager, db, handlers"]
    tmp[("temporary SQLite<br/>per test")]

    td --> fake --> client --> real --> tmp
```

- `make test` runs 255 data-service tests and the 2 agent tests, with no network,
  GPU or servers needed.
- The saved data was chosen to cover tricky cases: team 1 played a **Free Hit**
  the week before; team 6 has **3 Arsenal and 3 Chelsea players** (so buying
  Saka breaks the club limit); Saka's gameweeks include away games, so the
  score must be flipped to his side.
- The manager loader's clock is a field, so tests can move "now" past a deadline
  and check that the saved team is replaced.
- `squad`, `names` and `manager/rules.py` are pure functions, tested with
  made-up inputs (parametrized tests).
- To refresh the saved data, download the same URLs again into
  `tests/testdata/`. The expected values in the tests will change with it.

---

## 11. Porting notes: Go to Python

The data service was first written in Go and then ported (2026-10-14) so the
whole project is one language. The original is in `legacy/fpl_data_service_go/`.
The contract (`fpl.proto`) did not change, so the agent was untouched.

How the pieces map:

| Go | Python |
|---|---|
| `net/http` client, `json:"..."` structs, `parse.go` | `httpx` + Pydantic models (they also convert `"6.2"` strings and nulls) |
| `database/sql` + `modernc.org/sqlite` | stdlib `sqlite3`; `schema.sql` and the SQL queries carried over almost unchanged |
| methods on `*Store`, one file per topic | functions taking the `Store`, one module per topic (`db.players.search(store, ...)`) |
| `(value, ok, err)` returns | `X | None`, and exceptions |
| `status.Error(codes.NotFound, ...)` | `raise ServiceError(StatusCode.NOT_FOUND, ...)`; `server.py` converts |
| goroutine + `time.Ticker`, `context` | an `asyncio` task, `asyncio.timeout` |
| `*int` for nullable numbers | `int | None` (and `HasField()` for protobuf `optional` fields) |
| table-driven tests, `httptest` fake server | `pytest.mark.parametrize`, a fake `httpx.MockTransport` serving the same saved JSON |

How it was checked: the same saved responses and the same expected values in
the tests (161 tests), and 45 requests covering all 10 RPCs and their errors
sent to both services against live FPL data, with the responses compared: all
identical.

Differences worth knowing:

- `grpcurl` shows field names in snake_case (`web_name`) from the Python server
  and camelCase (`webName`) from the Go one; the data is the same.
- Python's `round()` rounds halves to even; `handlers/convert.round2` rounds
  them away from zero like Go's `math.Round`, so sums come out the same.
- All database calls are synchronous and happen on the event loop thread (they
  take milliseconds). That is why one connection is enough, and why no
  transaction may contain an `await`.
