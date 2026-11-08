"""WebSocket connection manager: fan-out of `{"type", "data"}` messages to every connected dashboard."""
import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("app.hub")

SEND_TIMEOUT = 5.0


def _default(obj: Any) -> Any:
    """JSON fallback for numpy scalars, sets and other odd values."""
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, (set, tuple)):
        return list(obj)
    return str(obj)


def encode(msg_type: str, data: Any) -> str:
    return json.dumps({"type": msg_type, "data": data}, default=_default)


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    @property
    def count(self) -> int:
        return len(self.clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def send(self, ws: WebSocket, msg_type: str, data: Any) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(encode(msg_type, data)), SEND_TIMEOUT)
            return True
        except Exception:  # noqa: BLE001 - any send failure means the client is gone
            self.disconnect(ws)
            return False

    async def broadcast(self, msg_type: str, data: Any) -> None:
        if not self.clients:
            return
        text = encode(msg_type, data)
        clients = list(self.clients)

        async def _send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_text(text), SEND_TIMEOUT)
            except Exception:  # noqa: BLE001
                self.disconnect(ws)

        await asyncio.gather(*(_send(ws) for ws in clients))
