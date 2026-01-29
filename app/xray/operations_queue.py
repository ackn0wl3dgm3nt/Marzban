"""
Operation queue with deduplication for batching xray operations.
"""
import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, List, Optional

from app import logger


class OpType(Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"


@dataclass
class PendingOperation:
    op_type: OpType
    user_id: int
    data: Any  # dbuser or whatever is needed for the operation
    enqueued_at: float

    def __repr__(self) -> str:
        age_ms = (time.time() - self.enqueued_at) * 1000
        return f"PendingOp({self.op_type.value}, user={self.user_id}, age={age_ms:.0f}ms)"


@dataclass
class QueueConfig:
    flush_interval: float = 0.1       # Seconds between flushes
    max_batch_size: int = 100         # Max operations per flush
    max_wait_time: float = 1.0        # Max time an operation can wait in queue


# Type alias for executor function
ExecutorFunc = Callable[[List[PendingOperation]], Awaitable[None]]


class OperationQueue:
    """
    Queue for xray operations with deduplication.

    Key features:
    - Deduplication: Multiple operations for same user_id keep only the latest
    - Batching: Operations are flushed every flush_interval seconds
    - Priority: Operations are processed in FIFO order (by user_id first seen)

    Usage:
        queue = OperationQueue()
        queue.set_executor(my_batch_executor)
        await queue.start()

        # Enqueue operations
        await queue.enqueue(user_id=1, op_type=OpType.UPDATE, data=dbuser)

        # Graceful shutdown
        await queue.stop()
    """

    def __init__(self, config: Optional[QueueConfig] = None):
        self.config = config or QueueConfig()
        self._pending: OrderedDict[int, PendingOperation] = OrderedDict()
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._executor: Optional[ExecutorFunc] = None
        self._running = False
        self._stats = {
            'enqueued': 0,
            'deduplicated': 0,
            'flushed': 0,
            'batches': 0,
        }

    def set_executor(self, executor: ExecutorFunc):
        """
        Set the executor function that processes batches.

        Executor signature: async def executor(operations: list[PendingOperation]) -> None
        """
        self._executor = executor

    async def start(self):
        """Start the background flush loop."""
        if self._running:
            return

        if not self._executor:
            raise RuntimeError("Executor not set. Call set_executor() first.")

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("OperationQueue started")

    async def stop(self):
        """Stop the queue, flushing remaining operations."""
        if not self._running:
            return

        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Final flush to process remaining operations
        await self._flush()
        logger.info(f"OperationQueue stopped. Stats: {self._stats}")

    async def enqueue(self, user_id: int, op_type: OpType, data: Any):
        """
        Add an operation to the queue.

        If an operation for this user_id already exists, it's replaced
        with the new one (deduplication).
        """
        async with self._lock:
            was_present = user_id in self._pending

            # Move to end if already present (update order)
            if was_present:
                del self._pending[user_id]
                self._stats['deduplicated'] += 1

            self._pending[user_id] = PendingOperation(
                op_type=op_type,
                user_id=user_id,
                data=data,
                enqueued_at=time.time()
            )
            self._stats['enqueued'] += 1

    async def _flush_loop(self):
        """Background loop that periodically flushes the queue."""
        while self._running:
            try:
                await asyncio.sleep(self.config.flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in flush loop: {e}")

    async def _flush(self):
        """Execute pending operations."""
        if not self._executor:
            return

        # Get batch to process
        async with self._lock:
            if not self._pending:
                return

            now = time.time()
            batch: List[PendingOperation] = []
            keys_to_remove: List[int] = []

            for user_id, op in self._pending.items():
                # Check max wait time
                wait_time = now - op.enqueued_at
                should_flush = (
                    len(batch) < self.config.max_batch_size or
                    wait_time >= self.config.max_wait_time
                )

                if should_flush and len(batch) < self.config.max_batch_size:
                    batch.append(op)
                    keys_to_remove.append(user_id)

                if len(batch) >= self.config.max_batch_size:
                    break

            for key in keys_to_remove:
                del self._pending[key]

        # Execute outside the lock
        if batch:
            self._stats['batches'] += 1
            self._stats['flushed'] += len(batch)

            try:
                await self._executor(batch)
                logger.debug(f"Flushed {len(batch)} operations")
            except Exception as e:
                logger.error(f"Executor failed for batch of {len(batch)}: {e}")
                # TODO: Consider retry logic or dead letter queue

    @property
    def pending_count(self) -> int:
        """Number of operations waiting in the queue."""
        return len(self._pending)

    @property
    def stats(self) -> dict:
        """Queue statistics."""
        return {
            **self._stats,
            'pending': self.pending_count,
        }

    def __repr__(self) -> str:
        return f"OperationQueue(pending={self.pending_count}, running={self._running})"
