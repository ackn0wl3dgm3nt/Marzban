# Benchmark: feature/xray-manager (Baseline)

**Branch:** `feature/xray-manager`
**Date:** 2026-02-12 20:57:47
**Commit:** `0239886`

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
| CREATE users | 500 | 500 (100%) | **53.3** | 901.3 | 922.2 | 1252.0 | 1299.2 |
| SWITCH active -> disabled | 500 | 500 (100%) | **50.6** | 956.8 | 979.7 | 1196.1 | 1314.3 |
| SWITCH disabled -> active | 500 | 500 (100%) | **48.8** | 998.2 | 1012.4 | 1242.7 | 1322.1 |

## Key Metrics

| Metric | Value |
|--------|-------|
| CREATE RPS | 53.3 |
| SWITCH RPS (disable) | 50.6 |
| SWITCH RPS (enable) | 48.8 |
| SWITCH avg latency (disable) | 956.8 ms |
| SWITCH avg latency (enable) | 998.2 ms |
| Success rate | 100% |
