"""
Base Adapter — Abstract base class for all protocol adapters.

Every adapter (OPC-UA, Modbus, etc.) extends this class and implements
connect(), disconnect(), and run().
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from core.event_bus import EventBus

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract base class for protocol adapters."""

    def __init__(self, adapter_id: str, name: str, config: dict, bus: EventBus):
        self.adapter_id = adapter_id
        self.name = name
        self.config = config
        self.bus = bus
        self.running = False
        self.status = "stopped"  # stopped | connecting | connected | error
        self.error_message = ""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the data source."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully disconnect from the data source."""
        ...

    @abstractmethod
    async def run(self) -> None:
        """Main loop — read data and publish events to the bus."""
        ...

    async def start(self) -> None:
        """Start the adapter lifecycle with automatic reconnection."""
        self.running = True
        logger.info(f"Starting adapter '{self.name}' ({self.adapter_id})")
        
        while self.running:
            self.status = "connecting"
            try:
                await self.connect()
                self.status = "connected"
                self.error_message = ""
                logger.info(f"Adapter '{self.name}' connected")
                await self.run()
                # If run() successfully finishes without exception, exit cleanly
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                logger.error(f"Adapter '{self.name}' connection lost/error: {e} - retrying in 5 seconds...")
                
                # Cleanup before retry
                try:
                    await self.disconnect()
                except Exception:
                    pass
                
                if self.running:
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the adapter."""
        self.running = False
        self.status = "stopped"
        try:
            await self.disconnect()
        except Exception as e:
            logger.warning(f"Error disconnecting adapter '{self.name}': {e}")
        logger.info(f"Adapter '{self.name}' stopped")
