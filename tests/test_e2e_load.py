"""
End-to-end load tests for user operations.

Тестирует реальные HTTP запросы на FastAPI endpoints.
Профилирует каждый этап и выводит метрики.

Run:
    python tests/test_e2e_load.py --users 100
    python tests/test_e2e_load.py --users 1000 --concurrent 50
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Async HTTP client
try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


@dataclass
class TimingStats:
    """Statistics for a single operation type."""
    times: List[float] = field(default_factory=list)
    errors: int = 0

    def add(self, elapsed: float):
        self.times.append(elapsed)

    def add_error(self):
        self.errors += 1

    @property
    def count(self) -> int:
        return len(self.times)

    @property
    def total(self) -> float:
        return sum(self.times)

    @property
    def mean(self) -> float:
        return statistics.mean(self.times) if self.times else 0

    @property
    def median(self) -> float:
        return statistics.median(self.times) if self.times else 0

    @property
    def p95(self) -> float:
        if not self.times:
            return 0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[idx]

    @property
    def p99(self) -> float:
        if not self.times:
            return 0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[idx]

    @property
    def min(self) -> float:
        return min(self.times) if self.times else 0

    @property
    def max(self) -> float:
        return max(self.times) if self.times else 0


class LoadTester:
    """E2E load tester for Marzban API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.token: Optional[str] = None
        self.stats: Dict[str, TimingStats] = defaultdict(TimingStats)
        self.inbounds: dict = {}

    async def setup(self, username: str = "admin", password: str = "admin"):
        """Authenticate and get available inbounds."""
        async with httpx.AsyncClient() as client:
            # Get token
            resp = await client.post(
                f"{self.base_url}/api/admin/token",
                data={"username": username, "password": password}
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Auth failed: {resp.text}")

            self.token = resp.json()["access_token"]

            # Get inbounds
            resp = await client.get(
                f"{self.base_url}/api/inbounds",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            self.inbounds = resp.json()

        print(f"Authenticated. Available inbounds: {list(self.inbounds.keys())}")

    def _get_user_payload(self, username: str) -> dict:
        """Generate user creation payload."""
        # Use first available protocol
        protocol = list(self.inbounds.keys())[0]
        inbound_tags = [inb["tag"] for inb in self.inbounds[protocol]]

        payload = {
            "username": username,
            "proxies": {},
            "inbounds": {protocol: inbound_tags},
            "status": "active"
        }

        # Add protocol-specific settings
        if protocol == "shadowsocks":
            payload["proxies"]["shadowsocks"] = {"password": f"pass_{username}"}
        elif protocol == "vmess":
            payload["proxies"]["vmess"] = {}
        elif protocol == "vless":
            payload["proxies"]["vless"] = {}
        elif protocol == "trojan":
            payload["proxies"]["trojan"] = {"password": f"pass_{username}"}

        return payload

    async def create_user(self, client: httpx.AsyncClient, username: str) -> bool:
        """Create a single user and record timing."""
        payload = self._get_user_payload(username)

        start = time.perf_counter()
        try:
            resp = await client.post(
                f"{self.base_url}/api/user",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                self.stats["create_user"].add(elapsed)
                return True
            else:
                self.stats["create_user"].add_error()
                print(f"  Create {username} failed: {resp.status_code} - {resp.text[:100]}")
                return False

        except Exception as e:
            self.stats["create_user"].add_error()
            print(f"  Create {username} error: {e}")
            return False

    async def delete_user(self, client: httpx.AsyncClient, username: str) -> bool:
        """Delete a single user and record timing."""
        start = time.perf_counter()
        try:
            resp = await client.delete(
                f"{self.base_url}/api/user/{username}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                self.stats["delete_user"].add(elapsed)
                return True
            else:
                self.stats["delete_user"].add_error()
                return False

        except Exception as e:
            self.stats["delete_user"].add_error()
            return False

    async def update_user(self, client: httpx.AsyncClient, username: str, status: str) -> bool:
        """Update user status and record timing."""
        start = time.perf_counter()
        try:
            resp = await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": status},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=30.0
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                self.stats["update_user"].add(elapsed)
                return True
            else:
                self.stats["update_user"].add_error()
                return False

        except Exception as e:
            self.stats["update_user"].add_error()
            return False

    async def run_create_test(self, num_users: int, concurrency: int = 10):
        """Run user creation load test."""
        print(f"\n{'='*60}")
        print(f"CREATE TEST: {num_users} users, concurrency={concurrency}")
        print(f"{'='*60}")

        usernames = [f"loadtest_user_{i}" for i in range(num_users)]
        semaphore = asyncio.Semaphore(concurrency)

        async def create_with_semaphore(client, username):
            async with semaphore:
                return await self.create_user(client, username)

        start = time.perf_counter()

        async with httpx.AsyncClient() as client:
            tasks = [create_with_semaphore(client, u) for u in usernames]
            results = await asyncio.gather(*tasks)

        total_time = time.perf_counter() - start
        success = sum(results)

        print(f"\nResults:")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Success: {success}/{num_users} ({success/num_users*100:.1f}%)")
        print(f"  Throughput: {num_users/total_time:.1f} users/sec")

        return usernames

    async def run_update_test(self, usernames: List[str], concurrency: int = 10):
        """Run user update load test (disable -> enable cycle)."""
        print(f"\n{'='*60}")
        print(f"UPDATE TEST: {len(usernames)} users, concurrency={concurrency}")
        print(f"{'='*60}")

        semaphore = asyncio.Semaphore(concurrency)

        async def update_with_semaphore(client, username, status):
            async with semaphore:
                return await self.update_user(client, username, status)

        start = time.perf_counter()

        async with httpx.AsyncClient() as client:
            # Disable all
            tasks = [update_with_semaphore(client, u, "disabled") for u in usernames]
            await asyncio.gather(*tasks)

            # Enable all
            tasks = [update_with_semaphore(client, u, "active") for u in usernames]
            await asyncio.gather(*tasks)

        total_time = time.perf_counter() - start
        total_ops = len(usernames) * 2

        print(f"\nResults:")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Operations: {total_ops}")
        print(f"  Throughput: {total_ops/total_time:.1f} ops/sec")

    async def run_delete_test(self, usernames: List[str], concurrency: int = 10):
        """Run user deletion load test."""
        print(f"\n{'='*60}")
        print(f"DELETE TEST: {len(usernames)} users, concurrency={concurrency}")
        print(f"{'='*60}")

        semaphore = asyncio.Semaphore(concurrency)

        async def delete_with_semaphore(client, username):
            async with semaphore:
                return await self.delete_user(client, username)

        start = time.perf_counter()

        async with httpx.AsyncClient() as client:
            tasks = [delete_with_semaphore(client, u) for u in usernames]
            results = await asyncio.gather(*tasks)

        total_time = time.perf_counter() - start
        success = sum(results)

        print(f"\nResults:")
        print(f"  Total time: {total_time:.2f}s")
        print(f"  Success: {success}/{len(usernames)}")
        print(f"  Throughput: {len(usernames)/total_time:.1f} users/sec")

    def print_stats(self):
        """Print detailed statistics."""
        print(f"\n{'='*60}")
        print("DETAILED STATISTICS")
        print(f"{'='*60}")

        for op_name, stats in self.stats.items():
            if stats.count == 0:
                continue

            print(f"\n{op_name}:")
            print(f"  Count:   {stats.count}")
            print(f"  Errors:  {stats.errors}")
            print(f"  Mean:    {stats.mean*1000:.1f}ms")
            print(f"  Median:  {stats.median*1000:.1f}ms")
            print(f"  P95:     {stats.p95*1000:.1f}ms")
            print(f"  P99:     {stats.p99*1000:.1f}ms")
            print(f"  Min:     {stats.min*1000:.1f}ms")
            print(f"  Max:     {stats.max*1000:.1f}ms")

    async def cleanup_test_users(self):
        """Delete any leftover test users."""
        print("\nCleaning up test users...")
        async with httpx.AsyncClient() as client:
            # Get all users
            resp = await client.get(
                f"{self.base_url}/api/users",
                headers={"Authorization": f"Bearer {self.token}"},
                params={"limit": 10000}
            )
            if resp.status_code != 200:
                return

            users = resp.json().get("users", [])
            test_users = [u["username"] for u in users if u["username"].startswith("loadtest_")]

            for username in test_users:
                await self.delete_user(client, username)

            print(f"  Deleted {len(test_users)} test users")


async def main():
    parser = argparse.ArgumentParser(description="E2E Load Test for Marzban")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL")
    parser.add_argument("--users", type=int, default=100, help="Number of users")
    parser.add_argument("--concurrent", type=int, default=10, help="Concurrency level")
    parser.add_argument("--cleanup-only", action="store_true", help="Only cleanup test users")
    parser.add_argument("--skip-delete", action="store_true", help="Skip delete test (keep users)")
    args = parser.parse_args()

    tester = LoadTester(args.url)

    try:
        await tester.setup()

        if args.cleanup_only:
            await tester.cleanup_test_users()
            return

        # Cleanup first
        await tester.cleanup_test_users()

        # Run tests
        usernames = await tester.run_create_test(args.users, args.concurrent)

        if usernames:
            await tester.run_update_test(usernames, args.concurrent)

            if not args.skip_delete:
                await tester.run_delete_test(usernames, args.concurrent)

        # Print stats
        tester.print_stats()

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
