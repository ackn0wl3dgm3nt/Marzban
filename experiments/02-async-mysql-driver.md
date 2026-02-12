# Benchmark: feature/async-mysql-driver

**Branch:** `feature/async-mysql-driver`
**Date:** 2026-02-12 21:19:49
**Baseline:** [01-xray-manager-baseline.md](01-xray-manager-baseline.md)

## What Changed

Replaced synchronous `pymysql` with async `asyncmy` driver for user CRUD routes (hot path):

- Async SQLAlchemy engine + `AsyncSession` for route handlers
- 15 async CRUD functions mirroring sync versions
- All user routes converted to `async def`
- Background jobs remain on sync `pymysql` (unaffected)

## Environment

| Parameter | Value |
|-----------|-------|
| CPU | 16 cores |
| RAM | 15.5 GB |
| Database | MySQL 8.0 (Docker) |
| Pool size | 30 |
| Max overflow | 70 |
| Existing users | 0 |
| Nodes | 0 (master only) |
| Inbounds | shadowsocks (1) |

## Test Parameters

| Parameter | Value |
|-----------|-------|
| Test users | 500 |
| Concurrency | 50 |

## Results

### Throughput & Latency

| Operation | Requests | Success | RPS | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) |
|-----------|----------|---------|-----|----------|----------|----------|----------|
| CREATE users | 500 | 500 (100%) | **82.9** | 570.5 | 572.3 | 753.7 | 779.5 |
| SWITCH active -> disabled | 500 | 500 (100%) | **61.8** | 763.4 | 760.9 | 919.9 | 1072.3 |
| SWITCH disabled -> active | 500 | 500 (100%) | **60.7** | 781.4 | 775.0 | 918.9 | 1071.2 |

## Comparison with Baseline

| Metric | Baseline (sync pymysql) | Async (asyncmy) | Improvement |
|--------|------------------------|-----------------|-------------|
| CREATE RPS | 53.3 | **82.9** | **+56%** |
| SWITCH RPS (disable) | 50.6 | **61.8** | **+22%** |
| SWITCH RPS (enable) | 48.8 | **60.7** | **+24%** |
| CREATE avg latency | 901.3 ms | **570.5 ms** | **-37%** |
| SWITCH avg latency (disable) | 956.8 ms | **763.4 ms** | **-20%** |
| SWITCH avg latency (enable) | 998.2 ms | **781.4 ms** | **-22%** |
| P95 latency (disable) | 1196.1 ms | **919.9 ms** | **-23%** |
| P95 latency (enable) | 1242.7 ms | **918.9 ms** | **-26%** |
| Success rate | 100% | 100% | - |

## Analysis

The async MySQL driver delivers consistent improvements across all operations:

- **CREATE throughput: +56%** — biggest gain, as user creation involves multiple DB writes (user + proxies + inbounds)
- **SWITCH throughput: +22-24%** — solid improvement for status toggle operations
- **Latency reduction: 20-37%** across all operations

The improvement comes from the event loop no longer blocking on synchronous DB calls. With `asyncmy`, all 50 concurrent requests can truly execute I/O in parallel rather than waiting for `pymysql`'s blocking socket operations.

Note: The gains are more modest than the theoretical 2-3x because gRPC calls to xray-core (which run in a separate thread pool) are still a significant portion of the route processing time.
