import json
import logging
import asyncio
import websockets
import emitter
from typing import Callable, Any

# Initialize logger
sclogger = logging.getLogger(__name__)
sclogger.addHandler(logging.StreamHandler())
sclogger.setLevel(logging.WARNING)

class Socket(emitter.Emitter):
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.auth_token: str | None = None
        self.is_connected = False
        self.reconnect_enabled = False
        self.reconnect_delay = 3
        self.ack_counter = 0
        self.acks = {}
        self.channels = []

        # Event listeners
        self.on_connected: Callable[[Any], None] | None = None
        self.on_disconnected: Callable[[Any], None] | None = None
        self.on_connect_error: Callable[[Any, Exception], None] | None = None
        self.on_set_auth: Callable[[Any, str], None] | None = None
        self.on_authentication: Callable[[Any, bool], None] | None = None

    def enable_logger(self, enabled: bool) -> None:
        """Enable or disable the logger."""
        sclogger.setLevel(logging.DEBUG if enabled else logging.WARNING)

    async def emit(self, event: str, data: Any) -> None:
        """Emit an event to the server."""
        emit_obj = {"event": event, "data": data}
        await self.ws.send(json.dumps(emit_obj, sort_keys=True))

    async def emit_ack(self, event: str, data: Any, ack: Callable) -> None:
        """Emit an event with acknowledgment to the server."""
        emit_obj = {"event": event, "data": data, "cid": self.increment_and_get_ack_id()}
        await self.ws.send(json.dumps(emit_obj, sort_keys=True))
        sclogger.debug(f"Emit data: {emit_obj}")
        self.acks[self.ack_counter] = (event, ack)

    async def subscribe(self, channel: str) -> None:
        """Subscribe to a channel."""
        sub_obj = {"event": "#subscribe", "data": {"channel": channel}, "cid": self.increment_and_get_ack_id()}
        await self.ws.send(json.dumps(sub_obj, sort_keys=True))
        self.channels.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        """Unsubscribe from a channel."""
        unsub_obj = {"event": "#unsubscribe", "data": {"channel": channel}, "cid": self.increment_and_get_ack_id()}
        await self.ws.send(json.dumps(unsub_obj, sort_keys=True))
        self.channels.remove(channel)

    async def publish(self, channel: str, data: Any) -> None:
        """Publish data to a channel."""
        pub_obj = {"event": "#publish", "data": {"channel": channel, "data": data}, "cid": self.increment_and_get_ack_id()}
        await self.ws.send(json.dumps(pub_obj, sort_keys=True))

    async def on_message(self, message: str) -> None:
        """Handle incoming messages."""
        if not message:
            sclogger.debug("Received ping, sending pong back")
            await self.ws.send("")
            return

        sclogger.debug(f"Message received: {message}")
        main_obj = json.loads(message)
        data = main_obj.get("data", {})
        rid = main_obj.get("rid")
        cid = main_obj.get("cid")
        event = main_obj.get("event")

        if rid in self.acks:
            event_name, ack = self.acks.pop(rid)
            sclogger.debug(f"Ack received for event: {event_name}")
            if ack:
                await ack(event_name, main_obj.get("error"), data)
        else:
            await self.handle_event(event, data, cid)

    async def handle_event(self, event: str, data: dict, cid: int | None = None) -> None:
        """Handle a specific event."""
        if event == "#publish":
            channel = data.get("channel", "")
            await self.execute(channel, data.get("data"))
            sclogger.debug(f"Publish event for channel: {channel}")
        elif event == "#setAuthToken":
            self.auth_token = data.get("token", "")
            sclogger.debug(f"Set auth token: {self.auth_token}")
            if self.on_set_auth:
                await self.on_set_auth(self, self.auth_token)
        elif event == "#removeAuthToken":
            self.auth_token = None
            sclogger.debug("Removed auth token")
        else:
            if cid and self.has_event_ack(event):
                await self.execute_ack(event, data, await self.create_ack(cid))
            else:
                await self.execute(event, data)

    async def create_ack(self, cid: int) -> Callable[[str| None, Any], None]:
        """Create an acknowledgment function for a given CID."""
        async def ack_fn(error: str | None, result: Any) -> None:
            ack_obj = {"error": error, "data": result, "rid": cid}
            await self.ws.send(json.dumps(ack_obj, sort_keys=True))
        return ack_fn

    def increment_and_get_ack_id(self) -> int:
        """Increment and return the current ack counter."""
        self.ack_counter += 1
        return self.ack_counter

    async def connect(self, ssl_options: dict| None = None) -> None:
        """Connect to the WebSocket server."""
        try:
            self.ws = await websockets.connect(self.url, ssl=ssl_options)
            await self.on_open()
        except Exception as e:
            sclogger.error(f"Failed to connect: {e}")
            if self.on_connect_error:
                await self.on_connect_error(self, e)

    async def on_open(self) -> None:
        """Handle the WebSocket connection being opened."""
        await self.handshake()
        sclogger.info("Handshake done")
        self.is_connected = True
        if self.on_connected:
            await self.on_connected(self)
        await self.listen()

    async def listen(self) -> None:
        """Listen for incoming WebSocket messages."""
        try:
            async for message in self.ws:
                await self.on_message(message)
        except websockets.ConnectionClosed:
            sclogger.warning("Connection closed")
            self.is_connected = False
            if self.on_disconnected:
                await self.on_disconnected(self)
            if self.reconnect_enabled:
                await self.reconnect()
        except Exception as e:
            sclogger.error(f"Error while listening: {e}")
            self.is_connected = False

    async def handshake(self) -> None:
        """Perform the handshake process."""
        handshake_obj = {"event": "#handshake", "data": {"authToken": self.auth_token}, "cid": self.increment_and_get_ack_id()}
        await self.ws.send(json.dumps(handshake_obj, sort_keys=True))

    async def reconnect(self) -> None:
        """Attempt to reconnect after a delay."""
        sclogger.info("Reconnecting...")
        await asyncio.sleep(self.reconnect_delay)
        await self.connect()

    async def disconnect(self) -> None:
        """Disconnect from the WebSocket server."""
        if self.ws:
            await self.ws.close()
            self.is_connected = False

    def set_reconnection(self, enable: bool) -> None:
        """Enable or disable automatic reconnection."""
        self.reconnect_enabled = enable

    def set_auth_token(self, token: str) -> None:
        """Set the authentication token."""
        self.auth_token = token

    def get_auth_token(self) -> str | None:
        """Get the current authentication token."""
        return self.auth_token

    def set_basic_listeners(self, on_connected: Callable, on_disconnected: Callable, on_connect_error: Callable) -> None:
        """Set basic connection listeners."""
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_connect_error = on_connect_error

    def set_auth_listeners(self, on_set_auth: Callable, on_authentication: Callable) -> None:
        """Set authentication listeners."""
        self.on_set_auth = on_set_auth
        self.on_authentication = on_authentication
