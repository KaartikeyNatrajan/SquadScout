"""Past results: season labels, reading FPL stats, the archive, the download job."""

from datetime import datetime

import pytest

from fpl_data.db import results
from fpl_data.fpl.models import Fixture
from fpl_data.history import seasons
from fpl_data.history.archive import ArchiveClient, ArchiveError
from fpl_data.history.refresh import HistoryRefresher, HistoryRefreshError
from fpl_data.history.results import Club, Person, to_results
from tests.fake_archive import FakeArchive
from tests.helpers import count_rows

ARSENAL, LIVERPOOL, SUNDERLAND = 3, 14, 56  # clubs' lasting codes


@pytest.fixture
async def archive():
    fake = FakeArchive()
    client = ArchiveClient("http://archive.test", transport=fake.transport)
    client.fake = fake  # for counting requests
    yield client
    await client.aclose()


@pytest.mark.parametrize(
    ("date", "season"),
    [
        ("2026-02-01", "2025-26"),
        ("2026-06-30", "2025-26"),
        ("2026-07-01", "2026-27"),
        ("2099-12-31", "2099-00"),
    ],
)
def test_season_of(date, season):
    assert seasons.season_of(datetime.fromisoformat(date)) == season


def test_previous_seasons_newest_first():
    assert seasons.previous("2026-27", 3) == ["2025-26", "2024-25", "2023-24"]


def test_current_season_comes_from_the_fixtures(seeded_store):
    assert seasons.searched(seeded_store, past=2) == ["2026-27", "2025-26", "2024-25"]


def _fixture(**fields) -> Fixture:
    base = dict(
        id=1,
        event=1,
        kickoff_time="2025-08-16T14:00:00Z",
        team_h=1,
        team_a=2,
        team_h_difficulty=3,
        team_a_difficulty=3,
        finished=True,
        team_h_score=2,
        team_a_score=1,
    )
    return Fixture.model_validate(base | fields)


def test_to_results_reads_events_and_skips_unfinished_matches():
    clubs = {1: Club(3, "Arsenal"), 2: Club(14, "Liverpool")}
    people = {10: Person(100, "Saka"), 20: Person(200, "Salah")}
    stats = [
        {
            "identifier": "goals_scored",
            "h": [{"element": 10, "value": 2}],
            "a": [{"element": 20, "value": 1}],
        },
        {"identifier": "bonus", "h": [{"element": 10, "value": 3}], "a": []},  # not kept
        {"identifier": "assists", "h": [{"element": 99, "value": 1}], "a": []},  # unknown player
    ]
    found = to_results(
        [_fixture(stats=stats), _fixture(id=2, finished=False, team_h_score=None)], clubs, people
    )

    [r] = found
    assert (r.season, r.home.name, r.away.name, r.home_score, r.away_score) == (
        "2025-26",
        "Arsenal",
        "Liverpool",
        2,
        1,
    )
    assert [(e.kind, e.player_name, e.team_code, e.count) for e in r.events] == [
        ("goal", "Saka", ARSENAL, 2),
        ("goal", "Salah", LIVERPOOL, 1),
    ]


async def test_archive_reads_a_season(archive):
    found = await archive.fetch_season("2025-26")

    assert len(found) == 4
    first = min(found, key=lambda r: r.kickoff_time)  # Liverpool 1-0 Arsenal, 31 Aug 2025
    assert (first.home.code, first.away.code, first.home_score, first.away_score) == (
        LIVERPOOL,
        ARSENAL,
        1,
        0,
    )
    assert [(e.kind, e.player_name) for e in first.events] == [
        ("goal", "Szoboszlai"),
        ("assist", "C.Jones"),
    ]


async def test_archive_missing_season_is_an_archive_error(archive):
    with pytest.raises(ArchiveError, match="404"):
        await archive.fetch_season("2019-20")


async def test_refresher_saves_what_it_can_and_retries_only_the_rest(seeded_store, archive):
    # The fake archive has 2025-26 and 2024-25; the other three past seasons fail.
    with pytest.raises(HistoryRefreshError, match="2023-24, 2022-23, 2021-22"):
        await HistoryRefresher(seeded_store, archive).refresh()

    assert results.has_season(seeded_store, "2025-26") and results.has_season(
        seeded_store, "2024-25"
    )
    assert count_rows(seeded_store, "match_results") == 8
    first_run = sum(archive.fake.hits.values())

    with pytest.raises(HistoryRefreshError):
        await HistoryRefresher(seeded_store, archive).refresh()
    # Second run: saved seasons are not downloaded again (only the 3 failing ones are tried).
    assert sum(archive.fake.hits.values()) - first_run == 3


async def test_the_fixtures_refresh_saves_this_seasons_results(service):
    # `service` has run the FPL refresh against the fake API (50 finished fixtures).
    assert results.has_season(service.store, "2026-27")
    [match] = results.meetings(service.store, ARSENAL, SUNDERLAND, ["2026-27"])
    assert (match.home.name, match.home_score, match.away_score, match.away.name) == (
        "Sunderland",
        0,
        2,
        "Arsenal",
    )


def test_saving_a_season_again_replaces_it(seeded_store):
    r = to_results([_fixture()], {1: Club(3, "Arsenal"), 2: Club(14, "Liverpool")}, {})
    results.save_season(seeded_store, "2025-26", r)
    results.save_season(seeded_store, "2025-26", r)
    assert count_rows(seeded_store, "match_results") == 1
