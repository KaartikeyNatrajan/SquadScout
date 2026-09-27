-- Applied on every startup, so every statement must be idempotent.

CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    short_name  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id                   INTEGER PRIMARY KEY,           -- FPL element id
    web_name             TEXT NOT NULL,
    first_name           TEXT NOT NULL,
    second_name          TEXT NOT NULL,
    search_name          TEXT NOT NULL,                 -- lowercased, accents stripped
    team_id              INTEGER NOT NULL REFERENCES teams(id),
    position             INTEGER NOT NULL,              -- 1 GK .. 4 FWD
    now_cost             INTEGER NOT NULL,              -- tenths of £m
    total_points         INTEGER NOT NULL DEFAULT 0,
    form                 REAL NOT NULL DEFAULT 0,
    selected_by_percent  REAL NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'a',
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_players_search_name ON players(search_name);

CREATE TABLE IF NOT EXISTS fixtures (
    id               INTEGER PRIMARY KEY,
    gameweek         INTEGER,                           -- NULL if unscheduled
    home_team_id     INTEGER NOT NULL REFERENCES teams(id),
    away_team_id     INTEGER NOT NULL REFERENCES teams(id),
    kickoff_time     TEXT,
    home_difficulty  INTEGER,
    away_difficulty  INTEGER,
    finished         INTEGER NOT NULL DEFAULT 0,
    home_score       INTEGER,
    away_score       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fixtures_home ON fixtures(home_team_id, gameweek);
CREATE INDEX IF NOT EXISTS idx_fixtures_away ON fixtures(away_team_id, gameweek);

CREATE TABLE IF NOT EXISTS player_gameweek_stats (
    player_id         INTEGER NOT NULL REFERENCES players(id),
    gameweek          INTEGER NOT NULL,
    minutes           INTEGER NOT NULL DEFAULT 0,
    goals_scored      INTEGER NOT NULL DEFAULT 0,
    assists           INTEGER NOT NULL DEFAULT 0,
    clean_sheets      INTEGER NOT NULL DEFAULT 0,
    bonus             INTEGER NOT NULL DEFAULT 0,
    total_points      INTEGER NOT NULL DEFAULT 0,
    expected_goals    REAL NOT NULL DEFAULT 0,
    expected_assists  REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, gameweek)
);

-- Tracks when each dataset was last pulled from the FPL API (cache freshness).
CREATE TABLE IF NOT EXISTS sync_log (
    dataset     TEXT PRIMARY KEY,                       -- 'bootstrap', 'fixtures', ...
    synced_at   TEXT NOT NULL
);
