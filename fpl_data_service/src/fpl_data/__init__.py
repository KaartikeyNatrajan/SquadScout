"""Squad Scout data service.

Downloads Fantasy Premier League data, keeps it in SQLite, and answers
questions about it over gRPC. It also knows FPL's squad rules, so it can check
whether a set of transfers is allowed.

Layers (each only imports the ones below it):

    server.py, handlers/        gRPC: validate requests, convert to protobuf
    refresh.py, manager/        when to download, a manager's team
    squad.py, optimizer.py      the verifier and the transfer search (no I/O)
    db/, fpl/                   SQLite storage, the FPL API client
"""
