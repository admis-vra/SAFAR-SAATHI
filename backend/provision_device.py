"""Issue a new HTTP-ingest token for a bus device. Prints the token once; only its hash is stored.

Usage (from backend/): .venv/Scripts/python provision_device.py --bus 14
Running it again for the same bus revokes the old token.
"""
import argparse
import asyncio
import hashlib
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

import db


async def provision(bus_id: int) -> str:
    load_dotenv(Path(__file__).parent / ".env")
    await db.connect(os.environ["DATABASE_URL"])
    try:
        device_id = await db.pool.fetchval("select mqtt_device_id from buses where id = $1", bus_id)
        if device_id is None:
            raise SystemExit(f"bus {bus_id} not found")
        token = secrets.token_urlsafe(32)
        await db.set_device_token_hash(bus_id, hashlib.sha256(token.encode()).hexdigest())
        return f"{device_id} {token}"
    finally:
        await db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, required=True)
    device_id, token = asyncio.run(provision(ap.parse_args().bus)).split()
    print(f"device {device_id}\nX-Device-Token: {token}\n(store it on the device now; it is not shown again)")
