from typing import Callable, Any



class Emitter:
    def __init__(self):
        self.events = {}
        self.acks = {}

    def on(self, key: str, function: Callable[[str, Any], None]) -> None:
        """Register an event handler."""
        self.events[key] = function

    def on_ack(self, key: str, function: Callable[[str, Any, Any], None]) -> None:
        """Register an acknowledgment handler."""
        self.acks[key] = function

    async def execute(self, key: str, obj: Any) -> None:
        """Execute a registered event handler asynchronously."""
        if key in self.events and callable(self.events[key]):
            await self.events[key](key, obj)

    def has_event_ack(self, key: str) -> bool:
        """Check if an event acknowledgment handler is registered."""
        return key in self.acks

    async def execute_ack(self, key: str, obj: Any, ack: Callable) -> None:
        """Execute an acknowledgment handler."""
        if key in self.acks and callable(self.acks[key]):
            await self.acks[key](key, obj, ack)