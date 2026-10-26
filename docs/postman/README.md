# Calling the data service from Postman

The data service speaks gRPC on `localhost:50051`, so use Postman's **New → gRPC**
request (not an HTTP request). Postman cannot import a gRPC collection from a JSON
file, so this page lists the method and a ready-to-paste message for each RPC.

## One-time setup

1. New → gRPC. Server URL: `localhost:50051` (no TLS).
2. Method list: either use **server reflection** (the service has it enabled, so
   Postman fetches the methods itself), or import `proto/squadscout/v1/fpl.proto`.
3. Pick a method below, paste the message in the **Message** tab, click **Invoke**.

If Postman (Windows) can't reach WSL on `localhost`, use the address from `hostname -I` in WSL.

Ids to try (from the saved test data and live data): Saka = 12, Arsenal = team 1,
manager team ids 1 and 6 work with the test snapshot. Enums are written as names.

## Messages (service `squadscout.v1.FplDataService`)

| Method | Message |
|---|---|
| SearchPlayers | `{"name": "saka", "limit": 5}` |
| FindPlayers | `{"position": "POSITION_MIDFIELDER", "max_price": 8.0, "sort_by": "PLAYER_SORT_EXPECTED_POINTS", "limit": 5}` |
| GetPlayerRecentForm | `{"player_id": 12, "last_n": 3}` |
| GetPlayerFixtures | `{"player_id": 12, "next_n": 5}` |
| GetPlayerGameweekStats | `{"player_id": 12, "gameweek": 3}` |
| GetFixtures | `{"team_name": "Arsenal", "next_n": 3}` |
| GetTransferTrends | `{"direction": "TRANSFER_DIRECTION_IN", "limit": 5}` |
| GetPlayerNews | `{"player_id": 12, "limit": 3, "hours": 72}` |
| GetTeamNews | `{"team_name": "Man City", "limit": 5}` |
| GetManagerTeam | `{"team_id": 6}` |
| EvaluateTransfers | `{"team_id": 6, "player_out_ids": [124], "player_in_ids": [12], "max_hit": 4}` |
| SuggestTransfers | `{"team_id": 6, "max_transfers": 2, "max_hit": 4}` |

`EvaluateTransfers` and `SuggestTransfers` also accept `current_squad_ids`,
`current_bank` and `free_transfers_available` to override the squad FPL shows
(it only shows the squad as of the last deadline).

The same messages work in the terminal, for example:

    grpcurl -plaintext -d '{"player_id":12,"limit":3}' localhost:50051 squadscout.v1.FplDataService/GetPlayerNews
