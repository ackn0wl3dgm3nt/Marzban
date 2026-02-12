# Benchmark: feature/async-mysql-driver

> **Status:** Pending — run `./experiments/run-benchmark.sh` on this branch to generate results.

**Branch:** `feature/async-mysql-driver`
**Baseline:** [01-xray-manager-baseline.md](01-xray-manager-baseline.md)

## What Changed

Replaced synchronous `pymysql` with async `asyncmy` driver for user CRUD routes (hot path):

- Async SQLAlchemy engine + `AsyncSession` for route handlers
- 15 async CRUD functions mirroring sync versions
- All user routes converted to `async def`
- Background jobs remain on sync `pymysql` (unaffected)

## Expected Improvement

Based on profiler data from baseline (DB = 52-53% of route time):

| Metric | Baseline | Expected |
|--------|----------|----------|
| SWITCH RPS | 33-34 | **80-120** (2-3x) |
| Route avg | 333-354 ms | **150-200 ms** |
| DB/CRUD avg | 178-183 ms | **80-100 ms** |

## How to Run

```bash
# On this branch (feature/async-mysql-driver):
./experiments/run-benchmark.sh --name 02-async-mysql-driver

# Or manually:
docker compose -f experiments/docker-compose.bench.yml build
docker compose -f experiments/docker-compose.bench.yml up -d
# wait for startup...
python tests/staging/benchmark.py \
  --url http://localhost:8000 \
  --login admin --password admin \
  --users 500 --concurrent 50
```

The script will automatically:
1. Build Docker image with `asyncmy` driver
2. Start MySQL + Marzban containers
3. Run the benchmark (500 users, concurrency=50)
4. Generate this report with actual numbers
5. Save raw JSON to `experiments/results/`

## Results

*To be populated after running benchmark.*
