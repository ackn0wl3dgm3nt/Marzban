# Benchmark: feature/xray-manager (Baseline)

**Branch:** `feature/xray-manager`
**Date:** 2026-02-12 19:48:12
**Commit:** `1e365aa`

## Environment

| Parameter | Value |
|-----------|-------|
| CPU | 16 cores |
| RAM | 15.5 GB |
| Existing users | 72,601 |
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
| CREATE users | 500 | 500 (100%) | **100.5** | 464.7 | 426.4 | 945.7 | 966.9 |
| SWITCH active -> disabled | 500 | 500 (100%) | **34.1** | 1398.2 | 1464.2 | 1696.0 | 1757.1 |
| SWITCH disabled -> active | 500 | 500 (100%) | **33.1** | 1431.5 | 1488.8 | 1777.1 | 1989.7 |

### Profiler: DISABLE (active -> disabled)

| Component | Calls | Avg (ms) | P95 (ms) | P99 (ms) | % of Route |
|-----------|-------|----------|----------|----------|------------|
| **route.modify_user** | 500 | 333.3 | 535.3 | 576.2 | 100.0% |
| **crud.update_user** | 500 | 178.0 | 278.7 | 339.1 | 53.4% |
| xray.do_remove_user | 500 | 95.4 | 176.2 | 251.2 | 28.6% |
| xray.grpc_remove | 500 | 50.5 | 95.8 | 151.5 | 15.1% |
| xray.execute_batch | 45 | 121.9 | 339.9 | 354.2 | 36.6% |

### Profiler: ENABLE (disabled -> active)

| Component | Calls | Avg (ms) | P95 (ms) | P99 (ms) | % of Route |
|-----------|-------|----------|----------|----------|------------|
| **route.modify_user** | 500 | 353.9 | 585.0 | 630.7 | 100.0% |
| **crud.update_user** | 500 | 182.7 | 298.3 | 339.7 | 51.6% |
| xray.do_update_user | 500 | 127.5 | 315.2 | 383.7 | 36.0% |
| xray.grpc_remove | 500 | 64.9 | 113.9 | 229.3 | 18.3% |
| xray.grpc_add | 500 | 32.9 | 81.5 | 140.0 | 9.3% |
| xray.execute_batch | 42 | 167.1 | 450.2 | 534.4 | 47.2% |

## Analysis

**DISABLE breakdown:**
```
Route total:   333 ms
  DB/CRUD:     178 ms (53%)
  XrayManager: 122 ms (37%)
  gRPC:        50 ms (15%)
```

**ENABLE breakdown:**
```
Route total:   354 ms
  DB/CRUD:     183 ms (52%)
  XrayManager: 167 ms (47%)
  gRPC:        65 ms (18%)
```

### Key Metrics

| Metric | Value |
|--------|-------|
| SWITCH RPS | 33 - 34 |
| Route avg latency | 1398 - 1432 ms |
| DB/CRUD avg (disable) | 178 ms |
| DB/CRUD avg (enable) | 183 ms |
