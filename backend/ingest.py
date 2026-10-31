"""Device telemetry: payload validation (blueprint §3.2) and the MQTT subscriber."""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

log = logging.getLogger("safarsaathi.ingest")

TOPIC_PREFIX = "safarsaathi/bus"
MAX_FUTURE_S = 300            # device clock may run a little ahead
MAX_BACKLOG_AGE_S = 24 * 3600  # older buffered data is discarded
MIN_SATS_FOR_FIX = 4


class Gps(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed: float = Field(ge=0, le=200)
    sats: int = Field(ge=0, le=64)


class Pax(BaseModel):
    in_count: int = Field(ge=0)
    out_count: int = Field(ge=0)
    net_current: int


class Telemetry(BaseModel):
    bus_id: str = Field(min_length=1, max_length=32)
    timestamp: int
    gps: Gps
    pax: Pax
    sos_active: bool = False

    @field_validator("timestamp")
    @classmethod
    def _sane_time(cls, ts: int) -> int:
        now = time.time()
        if ts > now + MAX_FUTURE_S:
            raise ValueError("timestamp is in the future")
        if ts < now - MAX_BACKLOG_AGE_S:
            raise ValueError("timestamp is older than 24 h")
        return ts

    @property
    def ts(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def has_fix(self) -> bool:
        # NEO-6M reports 0,0 with few satellites before it has a fix.
        return self.gps.sats >= MIN_SATS_FOR_FIX and not (self.gps.lat == 0 and self.gps.lon == 0)


_backlog = TypeAdapter(list[Telemetry])


def parse(kind: str, raw: bytes) -> list[Telemetry]:
    data = json.loads(raw)
    if kind == "telemetry":
        return [Telemetry.model_validate(data)]
    if kind == "backlog":
        items = _backlog.validate_python(data)
        if len(items) > 2000:
            raise ValueError("backlog too large")
        return items
    raise ValueError(f"unknown message kind {kind!r}")


def split_topic(topic: str) -> tuple[str, str]:
    """safarsaathi/bus/{device_id}/{telemetry|backlog} -> (device_id, kind)."""
    parts = topic.split("/")
    if len(parts) != 4 or "/".join(parts[:2]) != TOPIC_PREFIX:
        raise ValueError(f"unexpected topic {topic!r}")
    return parts[2], parts[3]


class MqttSubscriber:
    """paho runs its network loop in a thread; messages are handed to the asyncio loop.

    (paho in a thread instead of an asyncio MQTT client because those need a selector event loop,
    which uvicorn on Windows doesn't use.)
    """

    def __init__(self, url: str, handler, loop: asyncio.AbstractEventLoop):
        u = urlparse(url)
        self.host, self.port = u.hostname or "localhost", u.port or 1883
        self.handler, self.loop = handler, loop
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="safarsaathi-backend")
        if u.username:
            self.client.username_pw_set(u.username, u.password)
        if u.scheme == "mqtts":
            self.client.tls_set()
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 30)

    def start(self):
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, _userdata, _flags, reason, _props):
        if reason.is_failure:
            log.warning("MQTT connect failed: %s", reason)
            return
        client.subscribe(f"{TOPIC_PREFIX}/+/telemetry", qos=1)
        client.subscribe(f"{TOPIC_PREFIX}/+/backlog", qos=1)
        log.info("MQTT connected to %s:%s", self.host, self.port)

    def _on_message(self, _client, _userdata, msg):
        try:
            device_id, kind = split_topic(msg.topic)
            items = parse(kind, msg.payload)
        except (ValueError, ValidationError) as e:
            log.warning("rejected MQTT message on %s: %s", msg.topic, e)
            return
        fut = asyncio.run_coroutine_threadsafe(self.handler(device_id, items, kind == "backlog"), self.loop)
        fut.add_done_callback(_log_failure)


def _log_failure(fut):
    if fut.exception():
        log.error("telemetry handler failed", exc_info=fut.exception())
