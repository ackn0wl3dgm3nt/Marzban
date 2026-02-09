"""
XrayManager - async manager for xray operations with persistent connections.
"""
import asyncio
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional

import grpc
import grpc.aio

from app import logger
from app.utils.profiler import profile
from app.models.user import UserResponse
from app.xray.channels import AsyncGrpcChannel, ChannelConfig
from app.xray.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from app.xray.operations_queue import OperationQueue, OpType, PendingOperation, QueueConfig
from xray_api.proto.app.proxyman.command import command_pb2, command_pb2_grpc
from xray_api.proto.common.protocol import user_pb2
from xray_api.types.account import Account, XTLSFlows
from xray_api.types.message import Message

if TYPE_CHECKING:
    from app.db.models import Node as DBNode
    from app.db.models import User as DBUser
    from app.xray.config import XRayConfig


@dataclass
class XrayManagerConfig:
    call_timeout: float = 5.0
    connect_timeout: float = 5.0
    queue_flush_interval: float = 0.1
    queue_max_batch_size: int = 100
    circuit_failure_threshold: int = 3
    circuit_recovery_timeout: float = 30.0


class XrayManager:
    """
    Central manager for all xray operations.

    Features:
    - Persistent async gRPC connections to main core and nodes
    - Operation queue with deduplication
    - Circuit breaker for fault tolerance

    Usage:
        manager = XrayManager()
        await manager.start(xray_config)

        # User operations (go through queue)
        await manager.add_user(dbuser)
        await manager.update_user(dbuser)
        await manager.remove_user(dbuser)

        # Node management
        await manager.connect_node(dbnode)
        await manager.disconnect_node(node_id)

        # Shutdown
        await manager.stop()
    """

    def __init__(self, config: Optional[XrayManagerConfig] = None):
        self.config = config or XrayManagerConfig()

        self._main_channel: Optional[AsyncGrpcChannel] = None
        self._node_channels: Dict[int, AsyncGrpcChannel] = {}

        self._circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=self.config.circuit_failure_threshold,
            recovery_timeout=self.config.circuit_recovery_timeout,
        ))

        self._queue = OperationQueue(QueueConfig(
            flush_interval=self.config.queue_flush_interval,
            max_batch_size=self.config.queue_max_batch_size,
        ))
        self._queue.set_executor(self._execute_batch)

        self._started = False
        self._xray_config: Optional["XRayConfig"] = None

    # ==================== Lifecycle ====================

    async def start(self, xray_config: "XRayConfig"):
        """Start the manager after xray core has started."""
        if self._started:
            logger.warning("XrayManager already started")
            return

        self._xray_config = xray_config

        # Connect to main core
        self._main_channel = AsyncGrpcChannel(ChannelConfig(
            address=xray_config.api_host,
            port=xray_config.api_port,
            connect_timeout=self.config.connect_timeout,
            call_timeout=self.config.call_timeout,
        ))

        try:
            await self._main_channel.connect()
            logger.info(f"XrayManager connected to main core at {self._main_channel.target}")
        except ConnectionError as e:
            logger.error(f"Failed to connect to main core: {e}")
            raise

        # Start the operation queue
        await self._queue.start()

        self._started = True
        logger.info("XrayManager started")

    async def stop(self):
        """Stop the manager, flushing pending operations."""
        if not self._started:
            return

        self._started = False

        # Stop the queue (flushes remaining operations)
        await self._queue.stop()

        # Disconnect all channels
        if self._main_channel:
            await self._main_channel.disconnect()
            self._main_channel = None

        for node_id in list(self._node_channels.keys()):
            await self.disconnect_node(node_id)

        logger.info("XrayManager stopped")

    @property
    def is_started(self) -> bool:
        return self._started

    # ==================== Node Management ====================

    async def connect_node(self, node: "DBNode") -> bool:
        """Connect to a node. Returns True if successful."""
        if not self._started:
            raise RuntimeError("XrayManager not started")

        if node.id in self._node_channels:
            await self.disconnect_node(node.id)

        # Get node's server certificate (from REST port, not gRPC port)
        try:
            node_cert = ssl.get_server_certificate((node.address, node.port))
        except Exception as e:
            logger.error(f"Failed to get certificate for node {node.id}: {e}")
            return False

        channel = AsyncGrpcChannel(
            ChannelConfig(
                address=node.address,
                port=node.api_port,
                ssl_cert=node_cert.encode(),
                ssl_target_name="Gozargah",
                connect_timeout=self.config.connect_timeout,
                call_timeout=self.config.call_timeout,
            ),
            node_id=node.id
        )

        try:
            await channel.connect()
            self._node_channels[node.id] = channel
            await self._circuit_breaker.reset(node.id)
            logger.info(f"Connected to node {node.id} ({node.name}) at {channel.target}")
            return True
        except ConnectionError as e:
            logger.error(f"Failed to connect to node {node.id}: {e}")
            return False

    async def disconnect_node(self, node_id: int):
        """Disconnect from a node."""
        if node_id in self._node_channels:
            channel = self._node_channels.pop(node_id)
            await channel.disconnect()
            logger.info(f"Disconnected from node {node_id}")

    async def reconnect_node(self, node: "DBNode") -> bool:
        """Reconnect to a node."""
        await self.disconnect_node(node.id)
        return await self.connect_node(node)

    def get_connected_nodes(self) -> List[int]:
        """Get list of connected node IDs."""
        return list(self._node_channels.keys())

    # ==================== User Operations (Public API) ====================

    async def add_user(self, dbuser: "DBUser"):
        """Add a user. Operation goes through the queue."""
        if not self._started:
            raise RuntimeError("XrayManager not started")
        await self._queue.enqueue(dbuser.id, OpType.ADD, dbuser)

    async def update_user(self, dbuser: "DBUser"):
        """Update a user. Operation goes through the queue."""
        if not self._started:
            raise RuntimeError("XrayManager not started")
        await self._queue.enqueue(dbuser.id, OpType.UPDATE, dbuser)

    async def remove_user(self, dbuser: "DBUser"):
        """Remove a user. Operation goes through the queue."""
        if not self._started:
            raise RuntimeError("XrayManager not started")
        await self._queue.enqueue(dbuser.id, OpType.REMOVE, dbuser)

    # ==================== Direct Operations (bypass queue) ====================

    async def add_user_direct(self, dbuser: "DBUser"):
        """Add a user immediately, bypassing the queue."""
        await self._do_add_user(dbuser)

    async def update_user_direct(self, dbuser: "DBUser"):
        """Update a user immediately, bypassing the queue."""
        await self._do_update_user(dbuser)

    async def remove_user_direct(self, dbuser: "DBUser"):
        """Remove a user immediately, bypassing the queue."""
        await self._do_remove_user(dbuser)

    # ==================== Batch Execution (Internal) ====================

    @profile("xray.execute_batch")
    async def _execute_batch(self, operations: List[PendingOperation]):
        """Execute a batch of operations."""
        if not operations:
            return

        tasks = []
        for op in operations:
            match op.op_type:
                case OpType.ADD:
                    tasks.append(self._do_add_user(op.data))
                case OpType.UPDATE:
                    tasks.append(self._do_update_user(op.data))
                case OpType.REMOVE:
                    tasks.append(self._do_remove_user(op.data))

        # Execute all operations in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log errors
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                op = operations[i]
                logger.error(f"Operation {op.op_type.value} for user {op.user_id} failed: {result}")

    @profile("xray.do_add_user")
    async def _do_add_user(self, dbuser: "DBUser"):
        """Add user to main core and all nodes."""
        user = UserResponse.model_validate(dbuser)
        email = f"{dbuser.id}.{dbuser.username}"

        tasks = []

        for proxy_type, inbound_tags in user.inbounds.items():
            for inbound_tag in inbound_tags:
                account = self._build_account(user, proxy_type, inbound_tag, email)

                # Main core
                tasks.append(self._add_to_channel(self._main_channel, inbound_tag, account))

                # All nodes
                for node_id, channel in self._node_channels.items():
                    if await self._circuit_breaker.is_allowed(node_id):
                        tasks.append(self._add_to_channel(channel, inbound_tag, account, node_id))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @profile("xray.do_update_user")
    async def _do_update_user(self, dbuser: "DBUser"):
        """Update user on main core and all nodes."""
        user = UserResponse.model_validate(dbuser)
        email = f"{dbuser.id}.{dbuser.username}"

        tasks = []
        active_inbounds = set()

        # Update active inbounds
        for proxy_type, inbound_tags in user.inbounds.items():
            for inbound_tag in inbound_tags:
                active_inbounds.add(inbound_tag)
                account = self._build_account(user, proxy_type, inbound_tag, email)

                # Main core
                tasks.append(self._alter_on_channel(self._main_channel, inbound_tag, account))

                # All nodes
                for node_id, channel in self._node_channels.items():
                    if await self._circuit_breaker.is_allowed(node_id):
                        tasks.append(self._alter_on_channel(channel, inbound_tag, account, node_id))

        # Remove from inactive inbounds
        for inbound_tag in self._xray_config.inbounds_by_tag:
            if inbound_tag not in active_inbounds:
                # Main core
                tasks.append(self._remove_from_channel(self._main_channel, inbound_tag, email))

                # All nodes
                for node_id, channel in self._node_channels.items():
                    if await self._circuit_breaker.is_allowed(node_id):
                        tasks.append(self._remove_from_channel(channel, inbound_tag, email, node_id))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @profile("xray.do_remove_user")
    async def _do_remove_user(self, dbuser: "DBUser"):
        """Remove user from all inbounds on main core and all nodes."""
        email = f"{dbuser.id}.{dbuser.username}"

        tasks = []

        for inbound_tag in self._xray_config.inbounds_by_tag:
            # Main core
            tasks.append(self._remove_from_channel(self._main_channel, inbound_tag, email))

            # All nodes
            for node_id, channel in self._node_channels.items():
                if await self._circuit_breaker.is_allowed(node_id):
                    tasks.append(self._remove_from_channel(channel, inbound_tag, email, node_id))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ==================== Low-level gRPC Operations ====================

    @profile("xray.grpc_add")
    async def _add_to_channel(
        self,
        channel: AsyncGrpcChannel,
        inbound_tag: str,
        account: Account,
        node_id: Optional[int] = None
    ):
        """Add user to one inbound on one channel."""
        try:
            await channel.ensure_connected()
            stub = command_pb2_grpc.HandlerServiceStub(channel.channel)

            request = command_pb2.AlterInboundRequest(
                tag=inbound_tag,
                operation=Message(
                    command_pb2.AddUserOperation(
                        user=user_pb2.User(
                            level=account.level,
                            email=account.email,
                            account=account.message
                        )
                    )
                )
            )

            await stub.AlterInbound(request, timeout=self.config.call_timeout)

            if node_id is not None:
                await self._circuit_breaker.record_success(node_id)

        except grpc.aio.AioRpcError as e:
            if node_id is not None:
                await self._circuit_breaker.record_failure(node_id)

            # Ignore "already exists" errors
            if e.code() != grpc.StatusCode.ALREADY_EXISTS and "already exists" not in str(e.details()).lower():
                raise

        except Exception as e:
            if node_id is not None:
                await self._circuit_breaker.record_failure(node_id)
            raise

    @profile("xray.grpc_remove")
    async def _remove_from_channel(
        self,
        channel: AsyncGrpcChannel,
        inbound_tag: str,
        email: str,
        node_id: Optional[int] = None
    ):
        """Remove user from one inbound on one channel."""
        try:
            await channel.ensure_connected()
            stub = command_pb2_grpc.HandlerServiceStub(channel.channel)

            request = command_pb2.AlterInboundRequest(
                tag=inbound_tag,
                operation=Message(
                    command_pb2.RemoveUserOperation(email=email)
                )
            )

            await stub.AlterInbound(request, timeout=self.config.call_timeout)

            if node_id is not None:
                await self._circuit_breaker.record_success(node_id)

        except grpc.aio.AioRpcError as e:
            if node_id is not None:
                await self._circuit_breaker.record_failure(node_id)

            # Ignore "not found" errors
            if e.code() != grpc.StatusCode.NOT_FOUND and "not found" not in str(e.details()).lower():
                raise

        except Exception as e:
            if node_id is not None:
                await self._circuit_breaker.record_failure(node_id)
            raise

    async def _alter_on_channel(
        self,
        channel: AsyncGrpcChannel,
        inbound_tag: str,
        account: Account,
        node_id: Optional[int] = None
    ):
        """Update user (remove + add) on one inbound on one channel."""
        # Remove first (ignore errors if not found)
        await self._remove_from_channel(channel, inbound_tag, account.email, node_id)
        # Then add
        await self._add_to_channel(channel, inbound_tag, account, node_id)

    # ==================== Helper Methods ====================

    def _build_account(
        self,
        user: UserResponse,
        proxy_type,
        inbound_tag: str,
        email: str
    ) -> Account:
        """Build Account object for gRPC request."""
        inbound = self._xray_config.inbounds_by_tag.get(inbound_tag, {})

        try:
            proxy_settings = user.proxies[proxy_type].dict(no_obj=True)
        except KeyError:
            proxy_settings = {}

        account = proxy_type.account_model(email=email, **proxy_settings)

        # XTLS flow restrictions
        if getattr(account, 'flow', None) and (
            inbound.get('network', 'tcp') not in ('tcp', 'kcp')
            or (
                inbound.get('network', 'tcp') in ('tcp', 'kcp')
                and inbound.get('tls') not in ('tls', 'reality')
            )
            or inbound.get('header_type') == 'http'
        ):
            account.flow = XTLSFlows.NONE

        return account

    # ==================== Stats ====================

    def get_stats(self) -> dict:
        """Get manager statistics."""
        return {
            "started": self._started,
            "main_channel_state": self._main_channel.state.value if self._main_channel else "none",
            "connected_nodes": len(self._node_channels),
            "open_circuits": len(self._circuit_breaker.get_open_circuits()),
            "queue_stats": self._queue.stats,
        }

    def __repr__(self) -> str:
        return f"XrayManager(started={self._started}, nodes={len(self._node_channels)})"
