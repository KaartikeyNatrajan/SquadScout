from datetime import timedelta

import pytest

from fpl_data.main import CHECK_EVERY, RETRY_AFTER_FAILURE, refresh_all
from fpl_data.refresh import Refresher


@pytest.fixture
def refresher(store, client):
    return Refresher(store, client, timedelta(hours=1))


async def test_a_good_refresh_waits_the_normal_time(refresher):
    assert await refresh_all(refresher) == CHECK_EVERY


async def test_a_failed_refresh_is_retried_soon(refresher, fake_fpl):
    fake_fpl.down = True
    assert await refresh_all(refresher) == RETRY_AFTER_FAILURE


async def test_a_rate_limited_refresh_waits_as_long_as_fpl_asked(refresher, fake_fpl):
    fake_fpl.rate_limit_next = 1
    fake_fpl.retry_after = "300"  # longer than the client will wait itself

    assert await refresh_all(refresher) == timedelta(seconds=300)


async def test_a_short_rate_limit_still_waits_at_least_the_usual_retry_time(refresher, fake_fpl):
    fake_fpl.rate_limit_next = 99
    fake_fpl.retry_after = "0"  # retries are instant, then it gives up

    assert await refresh_all(refresher) == RETRY_AFTER_FAILURE
