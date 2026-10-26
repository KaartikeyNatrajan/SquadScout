"""The gRPC server: the thin layer between the network and handlers/service.py.

There is no HTTP server: langgraph_service is the only caller and talks gRPC.
For manual testing, server reflection is enabled so grpcurl works:

    grpcurl -plaintext localhost:50051 list squadscout.v1.FplDataService
    grpcurl -plaintext -d '{"name":"saka"}' localhost:50051 squadscout.v1.FplDataService/SearchPlayers
    grpcurl -plaintext -d '{"player_id":12}' localhost:50051 squadscout.v1.FplDataService/GetPlayerRecentForm
    grpcurl -plaintext -d '{"team_name":"Arsenal","next_n":3}' localhost:50051 squadscout.v1.FplDataService/GetFixtures
    grpcurl -plaintext -d '{"team_id":6}' localhost:50051 squadscout.v1.FplDataService/GetManagerTeam
    grpcurl -plaintext -d '{"team_id":6,"player_out_ids":[124],"player_in_ids":[12],"max_hit":4}' \
        localhost:50051 squadscout.v1.FplDataService/EvaluateTransfers

(grpcurl prints field names in snake_case, e.g. "web_name"; it leaves out
fields that are 0 or false.)

Environment variables, read in config.py: GRPC_ADDR (default [::]:50051),
DB_PATH (default data/fpl.db), FPL_BASE_URL (default the real FPL API).
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import grpc
from google.protobuf.message import Message
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from fpl_data.handlers.errors import ServiceError
from fpl_data.handlers.service import FplService
from squadscout.v1 import fpl_pb2 as pb
from squadscout.v1 import fpl_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

Request = TypeVar("Request", bound=Message)
Response = TypeVar("Response", bound=Message)


class FplDataServicer(pb_grpc.FplDataServiceServicer):
    """Connects each RPC in fpl.proto to the matching FplService method.

    The method names must match the proto exactly (that's how gRPC finds them),
    which is why they are CamelCase here and snake_case in FplService.
    """

    def __init__(self, service: FplService):
        self._service = service

    async def SearchPlayers(self, request, context):
        return await self._call(context, self._service.search_players, request)

    async def FindPlayers(self, request, context):
        return await self._call(context, self._service.find_players, request)

    async def GetFixtures(self, request, context):
        return await self._call(context, self._service.get_fixtures, request)

    async def GetPlayerFixtures(self, request, context):
        return await self._call(context, self._service.get_player_fixtures, request)

    async def GetPlayerGameweekStats(self, request, context):
        return await self._call(context, self._service.get_player_gameweek_stats, request)

    async def GetPlayerRecentForm(self, request, context):
        return await self._call(context, self._service.get_player_recent_form, request)

    async def GetTransferTrends(self, request, context):
        return await self._call(context, self._service.get_transfer_trends, request)

    async def GetManagerTeam(self, request, context):
        return await self._call(context, self._service.get_manager_team, request)

    async def EvaluateTransfers(self, request, context):
        return await self._call(context, self._service.evaluate_transfers, request)

    async def SuggestTransfers(self, request, context):
        return await self._call(context, self._service.suggest_transfers, request)

    @staticmethod
    async def _call(
        context: grpc.aio.ServicerContext,
        handler: Callable[[Request], Awaitable[Response]],
        request: Request,
    ) -> Response:
        """Run a handler, turning its ServiceError into the matching gRPC status.

        Every call is logged in one line: which RPC, how it ended, how long it took.
        """
        name = handler.__name__
        code = grpc.StatusCode.OK
        started = time.perf_counter()
        try:
            return await handler(request)
        except ServiceError as err:
            code = err.code
            await context.abort(err.code, err.message)
        except Exception:
            # A bug or a database failure on our side. Log everything, but tell
            # the caller only that it wasn't their fault.
            code = grpc.StatusCode.INTERNAL
            logger.exception("unexpected error in %s", name)
            await context.abort(code, "internal error in the data service; see its log")
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("rpc=%s code=%s ms=%.0f", name, code.name, elapsed_ms)


async def start_server(service: FplService, address: str) -> tuple[grpc.aio.Server, int]:
    """Start a gRPC server for `service` on `address` (e.g. "[::]:50051").

    Returns the running server and the port it bound to (useful when the
    address asks for port 0, "any free port", as the tests do).
    """
    server = grpc.aio.server()
    pb_grpc.add_FplDataServiceServicer_to_server(FplDataServicer(service), server)

    # Standard gRPC health check (used later by docker-compose healthchecks).
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    await health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)

    # Lets grpcurl / Postman discover the API without the .proto file.
    reflection.enable_server_reflection(
        (
            pb.DESCRIPTOR.services_by_name["FplDataService"].full_name,
            reflection.SERVICE_NAME,
            "grpc.health.v1.Health",
        ),
        server,
    )

    port = server.add_insecure_port(address)
    await server.start()
    return server, port
