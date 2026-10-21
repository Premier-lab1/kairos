import asyncio
import logging

from .app import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(main()))
