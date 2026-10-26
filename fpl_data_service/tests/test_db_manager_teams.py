from datetime import UTC, datetime

from fpl_data.db import manager_teams
from tests.helpers import count_rows


def test_save_and_load_a_manager_team(store):
    assert manager_teams.load_manager_team(store, 6) is None  # never saved

    fetched = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    deadline = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
    manager_teams.save_manager_team(store, 6, '{"id": 6}', fetched, deadline)
    # Saving again replaces the first copy.
    manager_teams.save_manager_team(store, 6, '{"id": 6, "bank": 1}', fetched, deadline)

    saved = manager_teams.load_manager_team(store, 6)
    assert saved.data == '{"id": 6, "bank": 1}'
    assert saved.fetched_at == fetched
    assert saved.valid_until == deadline
    assert count_rows(store, "manager_teams") == 1
