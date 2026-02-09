"""
Profiling utilities for finding bottlenecks.

Usage:
    from app.utils.profiler import profile, get_profiler_stats, reset_profiler

    @profile("create_user")
    def create_user(...):
        ...

    # Or as context manager:
    with profile("db_query"):
        result = db.query(...)

    # Get stats:
    stats = get_profiler_stats()
    print_profiler_stats()
"""
import asyncio
import functools
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from app import logger


@dataclass
class ProfileStats:
    """Statistics for a profiled operation."""
    calls: int = 0
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0
    times: List[float] = field(default_factory=list)

    def record(self, elapsed: float):
        self.calls += 1
        self.total_time += elapsed
        self.min_time = min(self.min_time, elapsed)
        self.max_time = max(self.max_time, elapsed)
        # Keep last 1000 times for percentile calculations
        if len(self.times) < 1000:
            self.times.append(elapsed)
        else:
            self.times[self.calls % 1000] = elapsed

    @property
    def avg_time(self) -> float:
        return self.total_time / self.calls if self.calls > 0 else 0

    @property
    def p50(self) -> float:
        if not self.times:
            return 0
        sorted_times = sorted(self.times)
        return sorted_times[len(sorted_times) // 2]

    @property
    def p95(self) -> float:
        if not self.times:
            return 0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99(self) -> float:
        if not self.times:
            return 0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]


class Profiler:
    """Thread-safe profiler for collecting timing statistics."""

    _instance: Optional['Profiler'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._stats: Dict[str, ProfileStats] = defaultdict(ProfileStats)
                    cls._instance._enabled = True
                    cls._instance._stats_lock = threading.Lock()
        return cls._instance

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def reset(self):
        with self._stats_lock:
            self._stats.clear()

    def record(self, name: str, elapsed: float):
        if not self._enabled:
            return
        with self._stats_lock:
            self._stats[name].record(elapsed)

    def get_stats(self) -> Dict[str, ProfileStats]:
        with self._stats_lock:
            return dict(self._stats)

    @contextmanager
    def profile(self, name: str):
        """Context manager for profiling a block of code."""
        if not self._enabled:
            yield
            return

        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.record(name, elapsed)


# Global profiler instance
_profiler = Profiler()


def profile(name: str):
    """
    Decorator/context manager for profiling.

    As decorator:
        @profile("my_function")
        def my_function():
            ...

    As context manager:
        with profile("my_block"):
            ...
    """
    profiler = _profiler

    # Check if used as context manager (no function passed)
    class ProfileContext:
        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, *args):
            elapsed = time.perf_counter() - self.start
            profiler.record(name, elapsed)

        def __call__(self, func):
            """Allow use as decorator."""
            if asyncio.iscoroutinefunction(func):
                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    start = time.perf_counter()
                    try:
                        return await func(*args, **kwargs)
                    finally:
                        elapsed = time.perf_counter() - start
                        profiler.record(name, elapsed)
                return async_wrapper
            else:
                @functools.wraps(func)
                def sync_wrapper(*args, **kwargs):
                    start = time.perf_counter()
                    try:
                        return func(*args, **kwargs)
                    finally:
                        elapsed = time.perf_counter() - start
                        profiler.record(name, elapsed)
                return sync_wrapper

    return ProfileContext()


def get_profiler_stats() -> Dict[str, ProfileStats]:
    """Get all profiling statistics."""
    return _profiler.get_stats()


def reset_profiler():
    """Reset all profiling statistics."""
    _profiler.reset()


def enable_profiler():
    """Enable profiling."""
    _profiler.enable()


def disable_profiler():
    """Disable profiling."""
    _profiler.disable()


def print_profiler_stats():
    """Print profiling statistics to log."""
    stats = get_profiler_stats()

    if not stats:
        logger.info("No profiling data collected")
        return

    logger.info("=" * 70)
    logger.info("PROFILER STATISTICS")
    logger.info("=" * 70)
    logger.info(f"{'Operation':<30} {'Calls':>8} {'Avg':>10} {'P50':>10} {'P95':>10} {'P99':>10}")
    logger.info("-" * 70)

    # Sort by total time descending
    sorted_stats = sorted(stats.items(), key=lambda x: x[1].total_time, reverse=True)

    for name, stat in sorted_stats:
        logger.info(
            f"{name:<30} {stat.calls:>8} {stat.avg_time*1000:>9.2f}ms "
            f"{stat.p50*1000:>9.2f}ms {stat.p95*1000:>9.2f}ms {stat.p99*1000:>9.2f}ms"
        )

    logger.info("=" * 70)


def get_profiler_report() -> str:
    """Get profiling report as string."""
    stats = get_profiler_stats()

    if not stats:
        return "No profiling data collected"

    lines = []
    lines.append("=" * 70)
    lines.append("PROFILER REPORT")
    lines.append("=" * 70)
    lines.append(f"{'Operation':<30} {'Calls':>8} {'Avg':>10} {'P50':>10} {'P95':>10} {'Max':>10}")
    lines.append("-" * 70)

    sorted_stats = sorted(stats.items(), key=lambda x: x[1].total_time, reverse=True)

    for name, stat in sorted_stats:
        lines.append(
            f"{name:<30} {stat.calls:>8} {stat.avg_time*1000:>9.2f}ms "
            f"{stat.p50*1000:>9.2f}ms {stat.p95*1000:>9.2f}ms {stat.max*1000:>9.2f}ms"
        )

    lines.append("=" * 70)

    return "\n".join(lines)
