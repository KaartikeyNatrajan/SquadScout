"""Service settings, read from environment variables."""

import os
from dataclasses import dataclass

from fpl_data.fpl.client import DEFAULT_BASE_URL
from fpl_data.history import archive


@dataclass(frozen=True)
class Settings:
    grpc_addr: str  # where to listen, e.g. "[::]:50051"
    db_path: str  # the SQLite file
    fpl_base_url: str  # the FPL API; tests and experiments can point elsewhere
    archive_base_url: str  # the archive of past seasons (see history/archive.py)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            grpc_addr=os.environ.get("GRPC_ADDR") or "[::]:50051",
            db_path=os.environ.get("DB_PATH") or "data/fpl.db",
            fpl_base_url=os.environ.get("FPL_BASE_URL") or DEFAULT_BASE_URL,
            archive_base_url=os.environ.get("ARCHIVE_BASE_URL") or archive.DEFAULT_BASE_URL,
        )
