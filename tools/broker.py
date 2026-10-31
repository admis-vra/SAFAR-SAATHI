"""Development MQTT broker (pure Python, no install beyond pip). NOT for a real pilot:
it allows anonymous clients. Use Mosquitto with per-device passwords + ACL for that (tools/mosquitto/).

Usage (from repo root): backend/.venv/Scripts/python tools/broker.py [--host 0.0.0.0] [--port 1883]
Default binds to 127.0.0.1 only, so nothing outside this laptop can publish.
"""
import argparse
import asyncio
import logging

from amqtt.broker import Broker


async def main(host: str, port: int):
    broker = Broker({
        "listeners": {"default": {"type": "tcp", "bind": f"{host}:{port}"}},
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    })
    await broker.start()
    print(f"MQTT broker listening on {host}:{port} (Ctrl+C to stop)")
    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(main(args.host, args.port))
    except KeyboardInterrupt:
        pass
