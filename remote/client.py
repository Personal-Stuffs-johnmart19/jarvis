# remote/client.py
# Jarvis — transport abstraction.
#
# jarvis.remote_clients was removed with the WebSocket bridge. Only the
# Bluetooth transport uses RemoteClient now; the core engine talks to it via
# remote.bluetooth_transport.BluetoothBridge.

class RemoteClient:
    """A single connected remote (phone) endpoint.

    Subclasses implement the asynchronous send primitives.
    """

    transport = "abstract"

    def __init__(self, peer: str = "?"):
        self.peer = peer

    # ── async primitives (subclass) ─────────────────────────────────────────
    async def send_json(self, payload: dict) -> bool:
        raise NotImplementedError

    async def send_audio(self, data: bytes) -> bool:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError
