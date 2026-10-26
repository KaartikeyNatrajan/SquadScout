-- Local cache of FPL data. Applied on every startup, so every statement must
-- be idempotent ("running it twice has the same effect as once"; that is
-- what IF NOT EXISTS gives us).
--
-- IF NOT EXISTS never changes a table that already exists. So whenever you
-- change this file, bump SCHEMA_VERSION in db/store.py: on the next start, Open sees
-- the old version, drops every table and creates them again from this file.
-- That is safe because everything here can be downloaded again.
--
-- Where each table's data comes from (see refresh.py):
--   teams, events, players,
--   chips                   <- /bootstrap-static/        (refreshed hourly)
--   fixtures                <- /fixtures/                (refreshed hourly)
--   player_gameweeks,
--   player_gameweek_matches <- /event/{gw}/live/         (one download per gameweek)
--   player_prices           <- /element-summary/{id}/    (once per player, when a manager's
--                                                         purchase price is needed)
--   manager_teams           <- /entry/{id}/...           (per manager, until the next deadline)
--   sync_log                <- written by every refresh above

CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,  -- FPL's own team id (Arsenal = 1)
    name        TEXT NOT NULL,
    short_name  TEXT NOT NULL
);

-- Gameweeks. FPL calls them "events".
CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY,  -- the gameweek number, 1-38
    name           TEXT NOT NULL,        -- "Gameweek 6"
    deadline_time  TEXT NOT NULL,        -- RFC 3339
    finished       INTEGER NOT NULL,     -- SQLite has no BOOLEAN: 0 or 1
    is_current     INTEGER NOT NULL,
    is_next        INTEGER NOT NULL
);

-- Chip allowances: each manager may play each row's chip once between
-- start_event and stop_event. Two rows per chip name, one per half-season.
CREATE TABLE IF NOT EXISTS chips (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,     -- "wildcard", "freehit", "bboost", "3xc"
    start_event  INTEGER NOT NULL,
    stop_event   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id                            INTEGER PRIMARY KEY,  -- FPL element id
    web_name                      TEXT NOT NULL,
    first_name                    TEXT NOT NULL,
    second_name                   TEXT NOT NULL,
    search_name                   TEXT NOT NULL,        -- lowercased, accents stripped
    team_id                       INTEGER NOT NULL REFERENCES teams (id),
    position                      INTEGER NOT NULL,     -- 1 GK .. 4 FWD
    now_cost                      INTEGER NOT NULL,     -- tenths of £m
    total_points                  INTEGER NOT NULL DEFAULT 0,
    form                          REAL NOT NULL DEFAULT 0,
    selected_by_percent           REAL NOT NULL DEFAULT 0,
    status                        TEXT NOT NULL DEFAULT 'a',
    news                          TEXT NOT NULL DEFAULT '',
    chance_of_playing_next_round  INTEGER,              -- NULL = no concerns; 0 = definitely out
    minutes                       INTEGER NOT NULL DEFAULT 0,
    starts                        INTEGER NOT NULL DEFAULT 0,
    goals_scored                  INTEGER NOT NULL DEFAULT 0,
    assists                       INTEGER NOT NULL DEFAULT 0,
    clean_sheets                  INTEGER NOT NULL DEFAULT 0,
    bonus                         INTEGER NOT NULL DEFAULT 0,
    expected_goals                REAL NOT NULL DEFAULT 0,
    expected_assists              REAL NOT NULL DEFAULT 0,
    expected_goal_involvements    REAL NOT NULL DEFAULT 0,
    points_per_game               REAL NOT NULL DEFAULT 0,
    ep_next                       REAL NOT NULL DEFAULT 0,  -- FPL's expected points, next gameweek
    news_added                    TEXT,                     -- when news was updated; NULL if no news
    transfers_in_event            INTEGER NOT NULL DEFAULT 0,  -- by all managers since the last deadline
    transfers_out_event           INTEGER NOT NULL DEFAULT 0,
    cost_change_event             INTEGER NOT NULL DEFAULT 0,  -- tenths of £m since the last deadline
    updated_at                    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- An index makes lookups by search_name fast (like a book index).
CREATE INDEX IF NOT EXISTS idx_players_search_name ON players (search_name);

CREATE TABLE IF NOT EXISTS fixtures (
    id               INTEGER PRIMARY KEY,
    gameweek         INTEGER,                           -- NULL if not scheduled yet
    home_team_id     INTEGER NOT NULL REFERENCES teams (id),
    away_team_id     INTEGER NOT NULL REFERENCES teams (id),
    kickoff_time     TEXT,                              -- NULL if not scheduled yet
    home_difficulty  INTEGER NOT NULL,                  -- FDR 1 (easy) - 5 (hard)
    away_difficulty  INTEGER NOT NULL,
    finished         INTEGER NOT NULL DEFAULT 0,
    home_score       INTEGER,                           -- NULL until played
    away_score       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_fixtures_home ON fixtures (home_team_id, gameweek);
CREATE INDEX IF NOT EXISTS idx_fixtures_away ON fixtures (away_team_id, gameweek);

-- One row per player per gameweek his club played in: FPL's totals for the
-- week. Every player at a club that played gets a row, including ones who
-- didn't get on the pitch (minutes = 0).
CREATE TABLE IF NOT EXISTS player_gameweeks (
    player_id                   INTEGER NOT NULL REFERENCES players (id),
    gameweek                    INTEGER NOT NULL,
    minutes                     INTEGER NOT NULL DEFAULT 0,
    starts                      INTEGER NOT NULL DEFAULT 0,
    goals_scored                INTEGER NOT NULL DEFAULT 0,
    assists                     INTEGER NOT NULL DEFAULT 0,
    clean_sheets                INTEGER NOT NULL DEFAULT 0,
    goals_conceded              INTEGER NOT NULL DEFAULT 0,
    saves                       INTEGER NOT NULL DEFAULT 0,
    bonus                       INTEGER NOT NULL DEFAULT 0,
    yellow_cards                INTEGER NOT NULL DEFAULT 0,
    red_cards                   INTEGER NOT NULL DEFAULT 0,
    total_points                INTEGER NOT NULL DEFAULT 0,
    expected_goals              REAL NOT NULL DEFAULT 0,
    expected_assists            REAL NOT NULL DEFAULT 0,
    expected_goal_involvements  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, gameweek)
);

-- The matches behind each player_gameweeks row: one, or two in a double
-- gameweek. FPL only splits minutes and points by match; everything else is
-- a weekly total.
-- team_id is the player's club when the gameweek was saved; it tells us
-- which side of the fixture he was on. (A player who later changes club keeps
-- his old club here, which is what we want.)
-- fixture_id has no REFERENCES so the two downloads stay independent; the
-- queries simply join on it.
CREATE TABLE IF NOT EXISTS player_gameweek_matches (
    player_id     INTEGER NOT NULL,
    gameweek      INTEGER NOT NULL,
    fixture_id    INTEGER NOT NULL,
    team_id       INTEGER NOT NULL REFERENCES teams (id),
    minutes       INTEGER NOT NULL DEFAULT 0,
    total_points  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, fixture_id),
    FOREIGN KEY (player_id, gameweek) REFERENCES player_gameweeks (player_id, gameweek)
);

-- Managers' teams (see internal/manager), stored as JSON. A squad can only
-- change visibly when a deadline passes (FPL hides new picks until then), so
-- each row is valid until the next deadline.
CREATE TABLE IF NOT EXISTS manager_teams (
    team_id      INTEGER PRIMARY KEY,
    data         TEXT NOT NULL,  -- manager.Team as JSON
    fetched_at   TEXT NOT NULL,  -- RFC 3339, UTC
    valid_until  TEXT NOT NULL   -- RFC 3339, UTC
);

-- A player's price in each gameweek. Past prices never change, so a player is
-- downloaded once and kept for good. Used to find out what a manager paid for
-- a player who has been in the squad since the start (his price when it began).
CREATE TABLE IF NOT EXISTS player_prices (
    player_id  INTEGER NOT NULL REFERENCES players (id),
    gameweek   INTEGER NOT NULL,
    price      INTEGER NOT NULL,  -- tenths of £m
    PRIMARY KEY (player_id, gameweek)
);

-- Tracks when each dataset was last pulled from the FPL API (cache freshness).
-- dataset is "bootstrap", "fixtures" or "event-live/<gameweek>".
CREATE TABLE IF NOT EXISTS sync_log (
    dataset    TEXT PRIMARY KEY,
    synced_at  TEXT NOT NULL  -- RFC 3339, UTC
);
