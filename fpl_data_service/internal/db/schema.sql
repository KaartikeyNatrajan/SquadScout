-- Local cache of FPL data. Applied on every startup, so every statement must
-- be idempotent ("running it twice has the same effect as once"; that is
-- what IF NOT EXISTS gives us).
--
-- IF NOT EXISTS never changes a table that already exists. So whenever you
-- change this file, bump schemaVersion in db.go: on the next start, Open sees
-- the old version, drops every table and creates them again from this file.
-- That is safe because everything here can be downloaded again.
--
-- Where each table's data comes from (see internal/refresh):
--   teams, events, players  <- /bootstrap-static/        (refreshed hourly)
--   fixtures                <- /fixtures/                (refreshed hourly)
--   player_match_stats      <- /element-summary/{id}/    (per player, on demand)
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

-- One row per player per match. The key is (player, fixture), not (player,
-- gameweek): in a "double gameweek" a team plays twice in one gameweek.
-- fixture_id has no REFERENCES on purpose: a player's history can be
-- downloaded before /fixtures/ has been.
CREATE TABLE IF NOT EXISTS player_match_stats (
    player_id                   INTEGER NOT NULL REFERENCES players (id),
    fixture_id                  INTEGER NOT NULL,
    gameweek                    INTEGER NOT NULL,
    opponent_team_id            INTEGER NOT NULL REFERENCES teams (id),
    was_home                    INTEGER NOT NULL,
    kickoff_time                TEXT NOT NULL,
    team_score                  INTEGER,             -- the player's team; NULL if not started
    opponent_score              INTEGER,
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
    PRIMARY KEY (player_id, fixture_id)
);

-- Tracks when each dataset was last pulled from the FPL API (cache freshness).
-- dataset is "bootstrap", "fixtures" or "element-summary/<player id>".
CREATE TABLE IF NOT EXISTS sync_log (
    dataset    TEXT PRIMARY KEY,
    synced_at  TEXT NOT NULL  -- RFC 3339, UTC
);
