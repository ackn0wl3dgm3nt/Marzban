"""
Load tests for XrayManager and OperationQueue.

Run standalone:
    python tests/test_xray_manager_load.py
"""
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, '.')


@dataclass
class MockUser:
    """Mock user for testing."""
    id: int
    username: str
    status: str = "active"
    proxies: list = field(default_factory=list)
    inbounds: dict = field(default_factory=dict)
    excluded_inbounds: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.proxies:
            mock_proxy = MagicMock()
            mock_proxy.type = "shadowsocks"
            self.proxies = [mock_proxy]
        if not self.inbounds:
            self.inbounds = {"shadowsocks": ["Shadowsocks TCP"]}


async def test_queue_deduplication():
    """Test OperationQueue deduplication."""
    from app.xray.operations_queue import OperationQueue, OpType, QueueConfig

    print("\n" + "=" * 60)
    print("TEST: OperationQueue Deduplication")
    print("=" * 60)

    config = QueueConfig(flush_interval=10.0)  # Don't auto-flush
    queue = OperationQueue(config)

    # Mock executor
    executed = []
    async def mock_executor(ops):
        executed.extend(ops)

    queue.set_executor(mock_executor)

    # Test 1: Same user multiple times
    print("\n1. Enqueue same user 1000 times...")
    user = MockUser(id=1, username="test_user")

    for _ in range(1000):
        await queue.enqueue(user_id=user.id, op_type=OpType.ADD, data=user)

    print(f"   Enqueued: {queue._stats['enqueued']}")
    print(f"   Deduplicated: {queue._stats['deduplicated']}")
    print(f"   Pending: {len(queue._pending)}")

    assert queue._stats['enqueued'] == 1000
    assert queue._stats['deduplicated'] == 999
    assert len(queue._pending) == 1
    print("   PASSED")

    # Reset
    queue._pending.clear()
    queue._stats = {'enqueued': 0, 'deduplicated': 0, 'flushed': 0, 'batches': 0}

    # Test 2: Many different users
    print("\n2. Enqueue 10000 different users...")
    start = time.perf_counter()

    for i in range(10000):
        await queue.enqueue(user_id=i, op_type=OpType.ADD, data=MockUser(id=i, username=f"user_{i}"))

    elapsed = time.perf_counter() - start
    print(f"   Time: {elapsed:.3f}s ({10000/elapsed:,.0f} ops/sec)")
    print(f"   Enqueued: {queue._stats['enqueued']}")
    print(f"   Deduplicated: {queue._stats['deduplicated']}")
    print(f"   Pending: {len(queue._pending)}")

    assert queue._stats['enqueued'] == 10000
    assert queue._stats['deduplicated'] == 0
    assert len(queue._pending) == 10000
    print("   PASSED")

    # Test 3: Operation override
    print("\n3. Test operation override (ADD -> UPDATE -> REMOVE)...")
    queue._pending.clear()
    queue._stats = {'enqueued': 0, 'deduplicated': 0, 'flushed': 0, 'batches': 0}

    user = MockUser(id=999, username="override_test")
    await queue.enqueue(user_id=user.id, op_type=OpType.ADD, data=user)
    await queue.enqueue(user_id=user.id, op_type=OpType.UPDATE, data=user)
    await queue.enqueue(user_id=user.id, op_type=OpType.REMOVE, data=user)

    pending_op = queue._pending[user.id]
    print(f"   Final operation: {pending_op.op_type}")
    assert pending_op.op_type == OpType.REMOVE
    assert len(queue._pending) == 1
    print("   PASSED")


async def test_queue_performance():
    """Test OperationQueue performance at scale."""
    from app.xray.operations_queue import OperationQueue, OpType, QueueConfig

    print("\n" + "=" * 60)
    print("TEST: OperationQueue Performance")
    print("=" * 60)

    config = QueueConfig(flush_interval=10.0)  # Don't auto-flush
    queue = OperationQueue(config)

    # Mock executor
    async def mock_executor(ops):
        pass
    queue.set_executor(mock_executor)

    for num_users in [100, 1000, 10000, 100000]:
        queue._pending.clear()
        queue._stats = {'enqueued': 0, 'deduplicated': 0, 'flushed': 0, 'batches': 0}

        start = time.perf_counter()

        for i in range(num_users):
            await queue.enqueue(
                user_id=i,
                op_type=OpType.ADD,
                data=MockUser(id=i, username=f"user_{i}")
            )

        elapsed = time.perf_counter() - start

        print(f"\n   {num_users:>7} users: {elapsed:.3f}s ({num_users/elapsed:>12,.0f} ops/sec)")

    # Test with deduplication
    print("\n" + "-" * 40)
    print("   With 90% deduplication (same 1000 users x 100):")

    queue._pending.clear()
    queue._stats = {'enqueued': 0, 'deduplicated': 0, 'flushed': 0, 'batches': 0}

    users = [MockUser(id=i, username=f"user_{i}") for i in range(1000)]

    start = time.perf_counter()

    for _ in range(100):
        for user in users:
            await queue.enqueue(user_id=user.id, op_type=OpType.UPDATE, data=user)

    elapsed = time.perf_counter() - start
    total_ops = 100000

    print(f"   {total_ops} ops in {elapsed:.3f}s ({total_ops/elapsed:,.0f} ops/sec)")
    print(f"   Deduplicated: {queue._stats['deduplicated']} ({queue._stats['deduplicated']/total_ops*100:.1f}%)")


async def test_manager_with_mock_channel():
    """Test XrayManager with mocked gRPC channel."""
    from app.xray.manager import XrayManager
    from app.xray.operations_queue import OpType

    print("\n" + "=" * 60)
    print("TEST: XrayManager with Mock Channel")
    print("=" * 60)

    # Create manager
    manager = XrayManager()

    # Mock the channel
    mock_channel = AsyncMock()
    mock_channel.is_connected = True
    mock_channel.add_user = AsyncMock()
    mock_channel.remove_user = AsyncMock()

    manager._main_channel = mock_channel
    manager._started = True

    # Start the queue with our executor
    executed_batches = []

    async def capture_executor(ops):
        executed_batches.append(ops)
        # Simulate actual gRPC calls
        for op in ops:
            if op.op_type == OpType.ADD:
                await mock_channel.add_user(op.data)
            elif op.op_type == OpType.REMOVE:
                await mock_channel.remove_user(op.data)

    manager._queue.set_executor(capture_executor)
    await manager._queue.start()

    try:
        # Test adding users
        print("\n1. Add 100 users concurrently...")
        users = [MockUser(id=i, username=f"user_{i}") for i in range(100)]

        start = time.perf_counter()
        tasks = [manager.add_user(user) for user in users]
        await asyncio.gather(*tasks)

        # Wait for flush
        await asyncio.sleep(0.3)

        elapsed = time.perf_counter() - start
        print(f"   Time: {elapsed:.3f}s")
        print(f"   Batches executed: {len(executed_batches)}")
        print(f"   gRPC calls: {mock_channel.add_user.call_count}")
        assert mock_channel.add_user.call_count == 100
        print("   PASSED")

        # Test rapid updates (should deduplicate)
        print("\n2. Rapid updates to same user (1000 times)...")
        mock_channel.reset_mock()
        executed_batches.clear()
        manager._queue._stats = {'enqueued': 0, 'deduplicated': 0, 'flushed': 0, 'batches': 0}

        user = MockUser(id=999, username="rapid_user")

        start = time.perf_counter()
        for _ in range(1000):
            await manager.update_user(user)

        await asyncio.sleep(0.3)

        elapsed = time.perf_counter() - start
        stats = manager._queue._stats

        print(f"   Time: {elapsed:.3f}s")
        print(f"   Enqueued: {stats['enqueued']}")
        print(f"   Deduplicated: {stats['deduplicated']} ({stats['deduplicated']/1000*100:.1f}%)")
        print(f"   Actual flushes: {stats['flushed']}")

        # Most should be deduplicated
        assert stats['deduplicated'] > 900
        print("   PASSED")

    finally:
        await manager._queue.stop()


async def test_async_ops_integration():
    """Test async_ops wrapper functions."""
    print("\n" + "=" * 60)
    print("TEST: async_ops Integration")
    print("=" * 60)

    user = MockUser(id=1, username="test")

    # Test with XrayManager NOT started (fallback)
    print("\n1. Test fallback to legacy operations...")

    with patch('app.xray.xray_manager') as mock_mgr:
        mock_mgr.is_started = False

        with patch('app.xray.operations') as mock_ops:
            mock_ops.add_user = MagicMock()
            mock_ops.update_user = MagicMock()
            mock_ops.remove_user = MagicMock()

            from app.xray import async_ops

            await async_ops.add_user(user)
            await async_ops.update_user(user)
            await async_ops.remove_user(user)

            assert mock_ops.add_user.call_count == 1
            assert mock_ops.update_user.call_count == 1
            assert mock_ops.remove_user.call_count == 1
            print("   Fallback works correctly")
            print("   PASSED")


def test_sync_ops_threads():
    """Test sync_ops from multiple threads."""
    print("\n" + "=" * 60)
    print("TEST: sync_ops Thread Safety")
    print("=" * 60)

    num_threads = 10
    ops_per_thread = 100
    total_ops = num_threads * ops_per_thread

    with patch('app.xray.xray_manager') as mock_mgr:
        mock_mgr.is_started = False

        with patch('app.xray.operations') as mock_ops:
            mock_ops.add_user = MagicMock()

            from app.xray import sync_ops

            def worker(thread_id):
                for i in range(ops_per_thread):
                    user = MockUser(id=thread_id * 1000 + i, username=f"t{thread_id}_u{i}")
                    sync_ops.add_user(user)

            print(f"\n   Running {total_ops} ops from {num_threads} threads...")

            start = time.perf_counter()

            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                list(executor.map(worker, range(num_threads)))

            elapsed = time.perf_counter() - start

            print(f"   Time: {elapsed:.3f}s ({total_ops/elapsed:,.0f} ops/sec)")
            print(f"   Total calls: {mock_ops.add_user.call_count}")

            assert mock_ops.add_user.call_count == total_ops
            print("   PASSED")


async def test_extreme_load():
    """Test with 1 million operations."""
    from app.xray.operations_queue import OperationQueue, OpType, QueueConfig

    print("\n" + "=" * 60)
    print("TEST: Extreme Load (1,000,000 operations)")
    print("=" * 60)

    config = QueueConfig(flush_interval=10.0)
    queue = OperationQueue(config)

    async def mock_executor(ops):
        pass
    queue.set_executor(mock_executor)

    # Test: 1 million operations with 10,000 unique users (100x each)
    print("\n   1,000,000 ops with 10,000 unique users (99% dedup expected)...")

    num_users = 10000
    repeats = 100
    total = num_users * repeats

    users = [MockUser(id=i, username=f"user_{i}") for i in range(num_users)]

    start = time.perf_counter()

    for _ in range(repeats):
        for user in users:
            await queue.enqueue(user_id=user.id, op_type=OpType.UPDATE, data=user)

    elapsed = time.perf_counter() - start

    print(f"   Total operations: {total:,}")
    print(f"   Time: {elapsed:.2f}s ({total/elapsed:,.0f} ops/sec)")
    print(f"   Deduplicated: {queue._stats['deduplicated']:,} ({queue._stats['deduplicated']/total*100:.1f}%)")
    print(f"   Pending (unique): {len(queue._pending):,}")

    assert queue._stats['deduplicated'] >= total * 0.99
    assert len(queue._pending) == num_users
    print("   PASSED")


async def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("XrayManager Load Tests")
    print("=" * 60)

    try:
        await test_queue_deduplication()
        await test_queue_performance()
        await test_manager_with_mock_channel()
        await test_async_ops_integration()
        test_sync_ops_threads()
        await test_extreme_load()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n   FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n   ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
