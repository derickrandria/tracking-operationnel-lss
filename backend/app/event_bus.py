"""Bus d'événements temps réel (§9).

Implémentation mono-processus : diffusion directe vers les clients WebSocket.
En production multi-workers, remplacer `publish()` par Redis Pub/Sub
(l'interface reste identique — voir README § Architecture).
"""
import asyncio
import json
import logging

log = logging.getLogger("lss.bus")


class ConnectionManager:
    def __init__(self):
        self.active: set = set()

    async def connect(self, websocket):
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket):
        self.active.discard(websocket)

    async def broadcast(self, message: dict):
        if not self.active:
            return
        data = json.dumps(message, default=str, ensure_ascii=False)
        morts = []
        for ws in list(self.active):
            try:
                await ws.send_text(data)
            except Exception:
                morts.append(ws)
        for ws in morts:
            self.disconnect(ws)


manager = ConnectionManager()
_loop = {"loop": None}


def attacher_boucle(loop: asyncio.AbstractEventLoop):
    _loop["loop"] = loop


def publish(type_evenement: str, payload: dict):
    """Publication thread-safe d'un événement vers tous les clients WS."""
    loop = _loop["loop"]
    if loop is None or not manager.active:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({"type": type_evenement, "payload": payload}), loop
        )
    except RuntimeError:
        pass
