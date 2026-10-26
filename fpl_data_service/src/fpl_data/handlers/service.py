"""The gRPC service logic: one method per RPC, spread over one module per topic.

Every RPC follows the same shape: validate the request, read from the store,
convert to protobuf. Only GetManagerTeam (and the RPCs built on it) can trigger
an FPL download, the first time a team is asked about each gameweek; everything
else is answered from SQLite.

    players.py        SearchPlayers, FindPlayers
    fixtures.py       GetFixtures, GetPlayerFixtures
    stats.py          GetPlayerGameweekStats, GetPlayerRecentForm
    manager_team.py   GetManagerTeam
    transfers.py      GetTransferTrends, EvaluateTransfers, SuggestTransfers
    news.py           GetPlayerNews, GetTeamNews (stories saved by the hourly news job)
    base.py           shared helpers (lookups, limits, fixture memo)
    convert.py        turning database rows into protobuf messages

The methods raise ServiceError (errors.py) for failures; server.py turns them
into gRPC status codes. They're plain async methods taking the request
message, which makes them easy to test without a network.
"""

from fpl_data.handlers.fixtures import FixtureHandlers
from fpl_data.handlers.manager_team import ManagerTeamHandlers
from fpl_data.handlers.news import NewsHandlers
from fpl_data.handlers.players import PlayerHandlers
from fpl_data.handlers.stats import StatsHandlers
from fpl_data.handlers.transfers import TransferHandlers


class FplService(
    PlayerHandlers,
    FixtureHandlers,
    StatsHandlers,
    ManagerTeamHandlers,
    TransferHandlers,
    NewsHandlers,
):
    """Everything the FplDataService RPCs do. Each handler group above is a
    class with the methods for its topic; this class combines them."""
