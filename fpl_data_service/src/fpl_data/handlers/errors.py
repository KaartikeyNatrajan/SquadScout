"""How a handler reports failure.

Handlers raise ServiceError with a gRPC status code; server.py turns it into
the gRPC reply. Codes used (the Python tools turn any error into an
"ERROR: ..." string the model can read, so messages say how to recover):

    INVALID_ARGUMENT     the request itself is wrong (empty name, id <= 0)
    NOT_FOUND            a well-formed request for something that doesn't exist
    FAILED_PRECONDITION  it exists but can't be used yet (a team with no squad)
    UNAVAILABLE          the FPL API is down or rate-limiting and the team isn't saved yet
    INTERNAL             a bug or database failure on our side (raised by server.py)
"""

import grpc


class ServiceError(Exception):
    def __init__(self, code: grpc.StatusCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def invalid_argument(message: str) -> ServiceError:
    return ServiceError(grpc.StatusCode.INVALID_ARGUMENT, message)


def not_found(message: str) -> ServiceError:
    return ServiceError(grpc.StatusCode.NOT_FOUND, message)
