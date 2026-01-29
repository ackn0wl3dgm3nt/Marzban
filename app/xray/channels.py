"""
Async gRPC channel wrapper with automatic reconnection.
"""
import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import grpc.aio

from app import logger


class ChannelState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"


@dataclass
class ChannelConfig:
    address: str
    port: int
    ssl_cert: Optional[bytes] = None
    ssl_target_name: Optional[str] = None
    connect_timeout: float = 5.0
    call_timeout: float = 5.0


class AsyncGrpcChannel:
    """
    Async gRPC channel with automatic reconnection support.

    Usage:
        channel = AsyncGrpcChannel(config)
        await channel.connect()

        # Use channel for gRPC calls
        stub = SomeServiceStub(channel.channel)
        await stub.SomeMethod(request)

        # Reconnect if needed
        await channel.ensure_connected()

        # Cleanup
        await channel.disconnect()
    """

    def __init__(self, config: ChannelConfig, node_id: Optional[int] = None):
        self.config = config
        self.node_id = node_id  # None for main core
        self._channel: Optional[grpc.aio.Channel] = None
        self._state = ChannelState.DISCONNECTED
        self._lock = asyncio.Lock()
        self._last_error: Optional[str] = None

    @property
    def state(self) -> ChannelState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._state == ChannelState.CONNECTED

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def target(self) -> str:
        return f"{self.config.address}:{self.config.port}"

    async def connect(self) -> bool:
        """
        Establishes connection to gRPC server.
        Returns True if successful, raises ConnectionError otherwise.
        """
        async with self._lock:
            if self._state == ChannelState.CONNECTED:
                return True

            self._state = ChannelState.CONNECTING
            self._last_error = None

            try:
                if self.config.ssl_cert:
                    creds = grpc.ssl_channel_credentials(
                        root_certificates=self.config.ssl_cert
                    )
                    options = []
                    if self.config.ssl_target_name:
                        options.append((
                            'grpc.ssl_target_name_override',
                            self.config.ssl_target_name
                        ))
                    self._channel = grpc.aio.secure_channel(
                        self.target,
                        credentials=creds,
                        options=options or None
                    )
                else:
                    self._channel = grpc.aio.insecure_channel(self.target)

                # Wait for channel to be ready
                await asyncio.wait_for(
                    self._channel.channel_ready(),
                    timeout=self.config.connect_timeout
                )

                self._state = ChannelState.CONNECTED
                logger.debug(f"gRPC channel connected to {self.target}")
                return True

            except asyncio.TimeoutError:
                self._state = ChannelState.FAILED
                self._last_error = f"Connection timeout after {self.config.connect_timeout}s"
                await self._cleanup_channel()
                raise ConnectionError(self._last_error)

            except Exception as e:
                self._state = ChannelState.FAILED
                self._last_error = str(e)
                await self._cleanup_channel()
                raise ConnectionError(f"Failed to connect to {self.target}: {e}")

    async def disconnect(self):
        """Closes the connection."""
        async with self._lock:
            await self._cleanup_channel()
            self._state = ChannelState.DISCONNECTED
            logger.debug(f"gRPC channel disconnected from {self.target}")

    async def _cleanup_channel(self):
        """Internal cleanup of channel resources."""
        if self._channel:
            try:
                await self._channel.close()
            except Exception:
                pass
            self._channel = None

    async def ensure_connected(self) -> bool:
        """
        Checks connection and reconnects if necessary.
        Returns True if connected, raises ConnectionError otherwise.
        """
        if self._state == ChannelState.CONNECTED and self._channel:
            # Check if channel is actually ready
            try:
                state = self._channel.get_state(try_to_connect=False)
                if state == grpc.ChannelConnectivity.READY:
                    return True
                if state == grpc.ChannelConnectivity.IDLE:
                    # Try to reconnect
                    await asyncio.wait_for(
                        self._channel.channel_ready(),
                        timeout=self.config.connect_timeout
                    )
                    return True
            except Exception:
                pass

        # Need to reconnect
        async with self._lock:
            await self._cleanup_channel()
            self._state = ChannelState.DISCONNECTED

        return await self.connect()

    @property
    def channel(self) -> grpc.aio.Channel:
        """
        Returns the raw gRPC channel for making calls.
        Raises ConnectionError if not connected.
        """
        if not self._channel or self._state != ChannelState.CONNECTED:
            raise ConnectionError(
                f"Channel to {self.target} is not connected "
                f"(state: {self._state.value})"
            )
        return self._channel

    def __repr__(self) -> str:
        node_info = f", node_id={self.node_id}" if self.node_id else ""
        return f"AsyncGrpcChannel({self.target}, state={self._state.value}{node_info})"
