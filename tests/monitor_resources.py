#!/usr/bin/env python3
"""
Мониторинг ресурсов во время нагрузочного теста.
"""
import asyncio
import sys
import time
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import httpx
except ImportError:
    print("pip install httpx")
    sys.exit(1)


async def get_system_stats(api_url: str, token: str) -> dict:
    """Получает системную статистику."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{api_url}/api/system",
                headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code == 200:
                return resp.json()
    except:
        pass
    return {}


async def get_profiler_stats(api_url: str, token: str) -> dict:
    """Получает статистику профайлера."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{api_url}/api/core/profiler",
                headers={"Authorization": f"Bearer {token}"}
            )
            if resp.status_code == 200:
                return resp.json()
    except:
        pass
    return {}


async def monitor(api_url: str = "http://localhost:8000", interval: int = 10, duration: int = 600):
    """Мониторит ресурсы в течение времени."""

    # Auth
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{api_url}/api/admin/token",
            data={"username": "admin", "password": "admin"}
        )
        token = resp.json()["access_token"]

    print(f"{'Time':<10} {'Users':>8} {'Online':>8} {'CPU%':>6} {'Mem MB':>8} {'BW In':>10} {'BW Out':>10}")
    print("-" * 70)

    start = time.time()
    samples = []

    while time.time() - start < duration:
        stats = await get_system_stats(api_url, token)

        if stats:
            elapsed = int(time.time() - start)
            mem_mb = stats.get('mem_used', 0) / 1024 / 1024

            sample = {
                'time': elapsed,
                'users': stats.get('total_user', 0),
                'online': stats.get('online_users', 0),
                'cpu': stats.get('cpu_usage', 0),
                'mem_mb': mem_mb,
                'bw_in': stats.get('incoming_bandwidth_speed', 0),
                'bw_out': stats.get('outgoing_bandwidth_speed', 0),
            }
            samples.append(sample)

            print(f"{elapsed:>6}s   {sample['users']:>8} {sample['online']:>8} {sample['cpu']:>5.1f}% {mem_mb:>7.0f} {sample['bw_in']:>10} {sample['bw_out']:>10}")

        await asyncio.sleep(interval)

    # Summary
    if samples:
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Max users: {max(s['users'] for s in samples)}")
        print(f"Max online: {max(s['online'] for s in samples)}")
        print(f"Max CPU: {max(s['cpu'] for s in samples):.1f}%")
        print(f"Max Memory: {max(s['mem_mb'] for s in samples):.0f} MB")
        print(f"Max BW In: {max(s['bw_in'] for s in samples)}")
        print(f"Max BW Out: {max(s['bw_out'] for s in samples)}")

    # Profiler stats
    print("\n" + "=" * 70)
    print("PROFILER STATS")
    print("=" * 70)

    prof = await get_profiler_stats(api_url, token)
    if prof:
        sorted_prof = sorted(prof.items(), key=lambda x: x[1].get('total_ms', 0), reverse=True)
        print(f"{'Operation':<30} {'Calls':>10} {'Avg ms':>10} {'P95 ms':>10}")
        print("-" * 70)
        for name, data in sorted_prof[:10]:
            print(f"{name:<30} {data.get('calls', 0):>10} {data.get('avg_ms', 0):>10.1f} {data.get('p95_ms', 0):>10.1f}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--duration", type=int, default=600)
    args = parser.parse_args()

    asyncio.run(monitor(args.url, args.interval, args.duration))
