import asyncio
import logging
from typing import Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


class TCPClientManager:
    _connections: Dict[Tuple[str, int], "TCPClientManager"] = {}
    DEFAULT_TIMEOUT = 5  # Default connection timeout in seconds

    def __init__(self, host: str, port: int, timeout: Optional[float] = None):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.lock = asyncio.Lock()
        self.timeout = timeout or self.DEFAULT_TIMEOUT  # Use default if not specified

    @classmethod
    def get_instance(
        cls, host: str, port: int, timeout: Optional[float] = None
    ) -> "TCPClientManager":
        key = (host, port)
        if key not in cls._connections:
            cls._connections[key] = TCPClientManager(host, port, timeout)
        return cls._connections[key]

    async def get_reader_writer(self):
        async with self.lock:
            if not self.writer or self.writer.is_closing():
                await self.connect()
            return self.reader, self.writer

    async def connect(self):
        try:
            _LOGGER.info(
                f"Connecting to {self.host}:{self.port} with timeout {self.timeout}s"
            )
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
            _LOGGER.info(f"Connected to device at {self.host}:{self.port}")
        except asyncio.TimeoutError:
            _LOGGER.error(
                f"Connection to {self.host}:{self.port} timed out after {self.timeout} seconds"
            )
            raise
        except Exception as e:
            _LOGGER.error(
                f"Failed to connect to device at {self.host}:{self.port}: {e}"
            )
            raise

    async def close(self):
        if self.writer and not self.writer.is_closing():
            self.writer.close()
            await self.writer.wait_closed()
            _LOGGER.info(f"Closed connection to device at {self.host}:{self.port}")

    async def reconnect(self):
        _LOGGER.info(f"Reconnecting to {self.host}:{self.port}")
        await self.close()
        await self.connect()
