import json
import logging
from typing import Dict, Set, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket subscriptions per job."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, job_id: str, websocket: WebSocket) -> None:
        """Accept new WebSocket connection and register under job_id."""
        await websocket.accept()
        if job_id not in self._connections:
            self._connections[job_id] = set()
        self._connections[job_id].add(websocket)
        logger.debug(f"WS client connected for job {job_id}. Total: {len(self._connections[job_id])}")

    def disconnect(self, job_id: str, websocket: WebSocket) -> None:
        """Remove WebSocket connection on disconnect."""
        if job_id in self._connections:
            self._connections[job_id].discard(websocket)
            if not self._connections[job_id]:
                del self._connections[job_id]
        logger.debug(f"WS client disconnected for job {job_id}")

    async def broadcast(self, job_id: str, payload: Dict[str, Any]) -> None:
        """Broadcast JSON payload to all active WebSockets connected to this job."""
        if job_id not in self._connections:
            return

        dead_connections = set()
        for ws in self._connections[job_id]:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.warning(f"Failed to send WS message for job {job_id}: {e}")
                dead_connections.add(ws)

        for ws in dead_connections:
            self.disconnect(job_id, ws)


ws_manager = ConnectionManager()
