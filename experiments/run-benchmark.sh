#!/usr/bin/env bash
#
# Marzban Benchmark — автономный скрипт.
# Поднимает окружение, прогоняет бенчмарк, генерирует отчёт.
#
# Использование:
#   ./experiments/run-benchmark.sh                    # все параметры по умолчанию
#   ./experiments/run-benchmark.sh --users 1000       # 1000 тестовых пользователей
#   ./experiments/run-benchmark.sh --name my-test     # своё имя отчёта
#   ./experiments/run-benchmark.sh --skip-docker      # не трогать Docker (сервер уже запущен)
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

URL="http://localhost:8000"
LOGIN="admin"
PASSWORD="admin"
USERS=500
CONCURRENT=50
REPORT_NAME=""
SKIP_DOCKER=false
SKIP_BUILD=false
CLEANUP=false

# ─────────────────────────────────────────────────────────────────────────────
# Parse arguments
# ─────────────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --url)        URL="$2";        shift 2 ;;
        --login)      LOGIN="$2";      shift 2 ;;
        --password)   PASSWORD="$2";   shift 2 ;;
        --users)      USERS="$2";      shift 2 ;;
        --concurrent) CONCURRENT="$2"; shift 2 ;;
        --name)       REPORT_NAME="$2"; shift 2 ;;
        --skip-docker) SKIP_DOCKER=true; shift ;;
        --skip-build)  SKIP_BUILD=true;  shift ;;
        --cleanup)     CLEANUP=true;     shift ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --url URL            Marzban URL (default: http://localhost:8000)"
            echo "  --login USER         Admin login (default: admin)"
            echo "  --password PASS      Admin password (default: admin)"
            echo "  --users N            Number of test users (default: 500)"
            echo "  --concurrent N       Concurrent requests (default: 50)"
            echo "  --name NAME          Report filename (default: auto from branch)"
            echo "  --skip-docker        Don't start/stop Docker (server already running)"
            echo "  --skip-build         Don't rebuild Docker image"
            echo "  --cleanup            Remove Docker volumes after benchmark"
            echo ""
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ─────────────────────────────────────────────────────────────────────────────
# Detect branch and commit
# ─────────────────────────────────────────────────────────────────────────────
cd "$PROJECT_DIR"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

if [ -z "$REPORT_NAME" ]; then
    # Sanitize branch name for filename
    REPORT_NAME=$(echo "$BRANCH" | sed 's/[\/]/-/g')
fi

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
JSON_FILE="$SCRIPT_DIR/results/${REPORT_NAME}_${TIMESTAMP}.json"
REPORT_FILE="$SCRIPT_DIR/${REPORT_NAME}.md"

mkdir -p "$SCRIPT_DIR/results"

echo "============================================================"
echo "  MARZBAN BENCHMARK"
echo "============================================================"
echo ""
echo "  Branch:      $BRANCH"
echo "  Commit:      $COMMIT"
echo "  URL:         $URL"
echo "  Users:       $USERS"
echo "  Concurrency: $CONCURRENT"
echo "  Report:      $REPORT_FILE"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Ensure httpx is installed
# ─────────────────────────────────────────────────────────────────────────────
echo "[1/5] Checking dependencies..."
if ! python3 -c "import httpx" 2>/dev/null; then
    echo "  Installing httpx..."
    pip3 install httpx --quiet
fi
echo "  OK"

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Start Docker environment
# ─────────────────────────────────────────────────────────────────────────────
if [ "$SKIP_DOCKER" = false ]; then
    echo ""
    echo "[2/5] Starting Docker environment..."

    COMPOSE_FILE="$SCRIPT_DIR/docker-compose.bench.yml"

    if [ "$SKIP_BUILD" = false ]; then
        echo "  Building image..."
        docker compose -f "$COMPOSE_FILE" build --quiet
    fi

    echo "  Starting containers..."
    docker compose -f "$COMPOSE_FILE" up -d

    echo "  Waiting for Marzban to be ready..."
    RETRIES=0
    MAX_RETRIES=60
    until curl -sf "$URL/api/system" > /dev/null 2>&1; do
        RETRIES=$((RETRIES + 1))
        if [ $RETRIES -ge $MAX_RETRIES ]; then
            echo "  ERROR: Marzban did not start within ${MAX_RETRIES}s"
            echo "  Check logs: docker compose -f $COMPOSE_FILE logs marzban"
            exit 1
        fi
        printf "\r  Waiting... %ds" "$RETRIES"
        sleep 1
    done
    echo ""
    echo "  Marzban is ready!"
else
    echo ""
    echo "[2/5] Skipping Docker (--skip-docker)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Run benchmark
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "[3/5] Running benchmark..."

cd "$SCRIPT_DIR"
python3 "$PROJECT_DIR/tests/staging/benchmark.py" \
    --url "$URL" \
    --login "$LOGIN" \
    --password "$PASSWORD" \
    --users "$USERS" \
    --concurrent "$CONCURRENT"

# Find the latest JSON file produced by the benchmark script
LATEST_JSON=$(ls -t "$PROJECT_DIR"/benchmark_*.json 2>/dev/null | head -1)
if [ -z "$LATEST_JSON" ]; then
    echo "ERROR: Benchmark did not produce a JSON file"
    exit 1
fi

# Move it to experiments/results/
mv "$LATEST_JSON" "$JSON_FILE"
echo "  Raw results: $JSON_FILE"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Generate markdown report
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "[4/5] Generating report..."

python3 "$SCRIPT_DIR/generate_report.py" \
    "$JSON_FILE" \
    --title "Benchmark: $BRANCH" \
    --branch "$BRANCH" \
    --commit "$COMMIT" \
    --output "$REPORT_FILE"

echo "  Report: $REPORT_FILE"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Cleanup (optional)
# ─────────────────────────────────────────────────────────────────────────────
if [ "$SKIP_DOCKER" = false ]; then
    if [ "$CLEANUP" = true ]; then
        echo ""
        echo "[5/5] Cleaning up Docker..."
        docker compose -f "$SCRIPT_DIR/docker-compose.bench.yml" down -v
    else
        echo ""
        echo "[5/5] Docker containers are still running."
        echo "  Stop:    docker compose -f $SCRIPT_DIR/docker-compose.bench.yml down"
        echo "  Cleanup: docker compose -f $SCRIPT_DIR/docker-compose.bench.yml down -v"
    fi
else
    echo ""
    echo "[5/5] Done (Docker not managed)"
fi

echo ""
echo "============================================================"
echo "  DONE"
echo "============================================================"
echo ""
echo "  JSON:   $JSON_FILE"
echo "  Report: $REPORT_FILE"
echo ""
echo "  To view: cat $REPORT_FILE"
echo ""
