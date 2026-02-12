# Benchmark: feature/xray-manager (Baseline)

**Branch:** `feature/xray-manager`
**Date:** 2026-02-12
**Commit:** `1e365aa` — XrayManager with node connection rework

## Environment

| Parameter | Value |
|-----------|-------|
| OS | Windows 11 Pro (Docker Desktop) |
| CPU | 16 cores |
| RAM | 16 GB |
| Database | MySQL 8.0 (docker, pymysql sync driver) |
| DB Pool | pool_size=10, max_overflow=30 |
| Python | 3.12 |
| Marzban | 0.8.4 (custom XrayManager branch) |
| Existing users | 72,601 |
| Nodes | master only |
| Inbounds | shadowsocks (TCP/2080) |

## Test Parameters

| Parameter | Value |
|-----------|-------|
| Test users | 500 |
| Concurrency | 50 |
| Benchmark tool | `tests/staging/benchmark.py` (httpx async client) |

## Results

### Throughput & Latency

| Operation | Requests | Success | RPS | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) |
|-----------|----------|---------|-----|----------|----------|----------|----------|
| CREATE users | 500 | 500 (100%) | **100.5** | 464.7 | 426.4 | 945.7 | 966.9 |
| SWITCH active -> disabled | 500 | 500 (100%) | **34.1** | 1398.2 | 1464.2 | 1696.0 | 1757.1 |
| SWITCH disabled -> active | 500 | 500 (100%) | **33.1** | 1431.5 | 1488.8 | 1777.1 | 1989.7 |

### Profiler Breakdown: DISABLE (active -> disabled)

| Component | Calls | Avg (ms) | P95 (ms) | P99 (ms) | % of Route |
|-----------|-------|----------|----------|----------|------------|
| **route.modify_user** | 500 | 333.3 | 535.3 | 576.2 | 100% |
| crud.update_user | 500 | 178.0 | 278.7 | 339.1 | **53.4%** |
| xray.do_remove_user | 500 | 95.4 | 176.2 | 251.3 | 28.6% |
| xray.grpc_remove | 500 | 50.5 | 95.8 | 151.5 | 15.1% |
| xray.execute_batch | 45 | 121.9 | 339.9 | 354.3 | — |

### Profiler Breakdown: ENABLE (disabled -> active)

| Component | Calls | Avg (ms) | P95 (ms) | P99 (ms) | % of Route |
|-----------|-------|----------|----------|----------|------------|
| **route.modify_user** | 500 | 353.9 | 585.0 | 630.7 | 100% |
| crud.update_user | 500 | 182.7 | 298.3 | 339.7 | **51.6%** |
| xray.do_update_user | 500 | 127.5 | 315.3 | 383.7 | 36.0% |
| xray.grpc_remove | 500 | 64.9 | 113.9 | 229.3 | 18.3% |
| xray.grpc_add | 500 | 32.9 | 81.5 | 140.0 | 9.3% |
| xray.execute_batch | 42 | 167.1 | 450.2 | 534.4 | — |

## Analysis

### Bottleneck: Database (pymysql sync driver)

```
Route total:   333 - 354 ms avg
  DB/CRUD:     178 - 183 ms (52-53%)  <-- BOTTLENECK
  XrayManager:  95 - 128 ms (29-36%)
  gRPC:         50 -  65 ms (15-18%)
```

The synchronous `pymysql` driver blocks the event loop on every DB query. With concurrency=50, requests serialize on DB access because:

1. **pymysql is blocking** — each `db.query()` / `db.commit()` blocks the entire event loop thread
2. **pool_size=10** limits parallel DB connections, but even with pool_size=30 no improvement was observed
3. Under 50 concurrent requests, effective throughput caps at **~34 RPS**

### Key Metrics (for comparison with async-mysql-driver)

| Metric | Value |
|--------|-------|
| **SWITCH RPS** | 33-34 |
| **Route avg latency** | 333-354 ms |
| **DB/CRUD avg latency** | 178-183 ms |
| **DB share of route** | 52-53% |

## Reproduce

```bash
docker compose -f docker-compose.mysql.yml up -d
python tests/staging/benchmark.py \
  --url http://localhost:8000 \
  --login admin --password admin \
  --users 500 --concurrent 50
```
