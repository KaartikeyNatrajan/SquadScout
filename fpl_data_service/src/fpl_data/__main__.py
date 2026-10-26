"""`python -m fpl_data` starts the data service."""

import asyncio

from fpl_data.main import run

if __name__ == "__main__":
    asyncio.run(run())
