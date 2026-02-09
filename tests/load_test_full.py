#!/usr/bin/env python3
"""
Полноценный нагрузочный тест для Marzban.

Тестирует при разных уровнях нагрузки:
- 10,000 операций
- 100,000 операций
- 1,000,000 операций (опционально)

Использование:
    # Быстрый тест (10k)
    python tests/load_test_full.py --level quick

    # Средний тест (100k)
    python tests/load_test_full.py --level medium

    # Полный тест (1M)
    python tests/load_test_full.py --level full

    # Кастомный тест
    python tests/load_test_full.py --users 50000 --concurrent 100

Требования:
    pip install httpx aiofiles
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import httpx
except ImportError:
    print("ERROR: httpx не установлен. Выполните: pip install httpx")
    sys.exit(1)


# Уровни нагрузки
LOAD_LEVELS = {
    "quick": {"users": 10_000, "concurrent": 50, "description": "Быстрый тест (10k)"},
    "medium": {"users": 100_000, "concurrent": 100, "description": "Средний тест (100k)"},
    "full": {"users": 1_000_000, "concurrent": 200, "description": "Полный тест (1M)"},
}


@dataclass
class TestMetrics:
    """Метрики для одного типа операций."""
    name: str
    times: List[float] = field(default_factory=list)
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)

    def add_success(self, elapsed: float):
        self.times.append(elapsed)

    def add_error(self, message: str = ""):
        self.errors += 1
        if message and len(self.error_messages) < 10:  # Храним только первые 10
            self.error_messages.append(message)

    @property
    def count(self) -> int:
        return len(self.times)

    @property
    def total_ops(self) -> int:
        return self.count + self.errors

    @property
    def success_rate(self) -> float:
        total = self.total_ops
        return (self.count / total * 100) if total > 0 else 0

    def percentile(self, p: float) -> float:
        if not self.times:
            return 0
        sorted_times = sorted(self.times)
        idx = int(len(sorted_times) * p / 100)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    def to_dict(self) -> dict:
        if not self.times:
            return {
                "count": 0,
                "errors": self.errors,
                "success_rate": 0,
            }
        return {
            "count": self.count,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 2),
            "mean_ms": round(statistics.mean(self.times) * 1000, 2),
            "median_ms": round(statistics.median(self.times) * 1000, 2),
            "p95_ms": round(self.percentile(95) * 1000, 2),
            "p99_ms": round(self.percentile(99) * 1000, 2),
            "min_ms": round(min(self.times) * 1000, 2),
            "max_ms": round(max(self.times) * 1000, 2),
            "std_dev_ms": round(statistics.stdev(self.times) * 1000, 2) if len(self.times) > 1 else 0,
        }


@dataclass
class TestResults:
    """Результаты всего теста."""
    level: str
    target_users: int
    concurrency: int
    database_type: str
    start_time: datetime
    end_time: Optional[datetime] = None
    metrics: Dict[str, TestMetrics] = field(default_factory=dict)
    profiler_before: Optional[dict] = None
    profiler_after: Optional[dict] = None

    def add_metric(self, name: str) -> TestMetrics:
        if name not in self.metrics:
            self.metrics[name] = TestMetrics(name=name)
        return self.metrics[name]

    @property
    def duration_seconds(self) -> float:
        if not self.end_time:
            return 0
        return (self.end_time - self.start_time).total_seconds()

    def to_dict(self) -> dict:
        return {
            "test_info": {
                "level": self.level,
                "target_users": self.target_users,
                "concurrency": self.concurrency,
                "database_type": self.database_type,
                "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat() if self.end_time else None,
                "duration_seconds": round(self.duration_seconds, 2),
            },
            "operations": {name: m.to_dict() for name, m in self.metrics.items()},
            "profiler_diff": self._calc_profiler_diff(),
        }

    def _calc_profiler_diff(self) -> dict:
        """Вычисляет разницу в профайлере до и после теста."""
        if not self.profiler_before or not self.profiler_after:
            return {}

        diff = {}
        for key, after in self.profiler_after.items():
            before = self.profiler_before.get(key, {"calls": 0, "total_ms": 0})
            calls_diff = after.get("calls", 0) - before.get("calls", 0)
            if calls_diff > 0:
                diff[key] = {
                    "calls": calls_diff,
                    "avg_ms": after.get("avg_ms", 0),
                    "p95_ms": after.get("p95_ms", 0),
                    "p99_ms": after.get("p99_ms", 0),
                }
        return diff


class LoadTester:
    """Нагрузочный тестер для Marzban API."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.token: Optional[str] = None
        self.inbounds: dict = {}
        self.db_type: str = "unknown"

    async def setup(self, username: str = "admin", password: str = "admin") -> bool:
        """Аутентификация и получение информации о системе."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Получаем токен
                resp = await client.post(
                    f"{self.base_url}/api/admin/token",
                    data={"username": username, "password": password}
                )
                if resp.status_code != 200:
                    print(f"ОШИБКА: Не удалось авторизоваться: {resp.text}")
                    return False

                self.token = resp.json()["access_token"]

                # Получаем inbounds
                resp = await client.get(
                    f"{self.base_url}/api/inbounds",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                self.inbounds = resp.json()

                # Пытаемся определить тип БД
                resp = await client.get(
                    f"{self.base_url}/api/system",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                if resp.status_code == 200:
                    system_info = resp.json()
                    # Пытаемся определить по косвенным признакам
                    self.db_type = "MySQL/MariaDB"  # По умолчанию предполагаем прод

            print(f"[OK] Авторизация успешна")
            print(f"[OK] Доступные протоколы: {list(self.inbounds.keys())}")
            return True

        except httpx.ConnectError:
            print(f"ОШИБКА: Не удалось подключиться к {self.base_url}")
            return False
        except Exception as e:
            print(f"ОШИБКА: {e}")
            return False

    async def get_profiler_stats(self) -> dict:
        """Получает текущие метрики профайлера."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.base_url}/api/core/profiler",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                if resp.status_code == 200:
                    return resp.json()
        except:
            pass
        return {}

    async def reset_profiler(self):
        """Сбрасывает профайлер."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{self.base_url}/api/core/profiler/reset",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
        except:
            pass

    def _get_user_payload(self, username: str) -> dict:
        """Генерирует payload для создания пользователя."""
        protocol = list(self.inbounds.keys())[0]
        inbound_tags = [inb["tag"] for inb in self.inbounds[protocol]]

        payload = {
            "username": username,
            "proxies": {},
            "inbounds": {protocol: inbound_tags},
            "status": "active"
        }

        if protocol == "shadowsocks":
            payload["proxies"]["shadowsocks"] = {"password": f"pass_{username}"}
        elif protocol == "vmess":
            payload["proxies"]["vmess"] = {}
        elif protocol == "vless":
            payload["proxies"]["vless"] = {}
        elif protocol == "trojan":
            payload["proxies"]["trojan"] = {"password": f"pass_{username}"}

        return payload

    async def _create_user(self, client: httpx.AsyncClient, username: str, metrics: TestMetrics):
        """Создаёт одного пользователя."""
        payload = self._get_user_payload(username)
        start = time.perf_counter()

        try:
            resp = await client.post(
                f"{self.base_url}/api/user",
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60.0
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                metrics.add_success(elapsed)
            else:
                metrics.add_error(f"{resp.status_code}: {resp.text[:100]}")

        except Exception as e:
            metrics.add_error(str(e)[:100])

    async def _delete_user(self, client: httpx.AsyncClient, username: str, metrics: TestMetrics):
        """Удаляет одного пользователя."""
        start = time.perf_counter()

        try:
            resp = await client.delete(
                f"{self.base_url}/api/user/{username}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60.0
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                metrics.add_success(elapsed)
            else:
                metrics.add_error(f"{resp.status_code}")

        except Exception as e:
            metrics.add_error(str(e)[:100])

    async def _update_user(self, client: httpx.AsyncClient, username: str, status: str, metrics: TestMetrics):
        """Обновляет статус пользователя."""
        start = time.perf_counter()

        try:
            resp = await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": status},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=60.0
            )
            elapsed = time.perf_counter() - start

            if resp.status_code == 200:
                metrics.add_success(elapsed)
            else:
                metrics.add_error(f"{resp.status_code}")

        except Exception as e:
            metrics.add_error(str(e)[:100])

    async def cleanup_test_users(self, prefix: str = "lt_"):
        """Удаляет тестовых пользователей."""
        print("Очистка тестовых пользователей...", end=" ", flush=True)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Получаем всех пользователей
                resp = await client.get(
                    f"{self.base_url}/api/users",
                    headers={"Authorization": f"Bearer {self.token}"},
                    params={"limit": 100000}
                )

                if resp.status_code != 200:
                    print("не удалось получить список")
                    return

                users = resp.json().get("users", [])
                test_users = [u["username"] for u in users if u["username"].startswith(prefix)]

                if not test_users:
                    print("нет тестовых пользователей")
                    return

                # Удаляем пакетами
                semaphore = asyncio.Semaphore(50)

                async def delete_one(username):
                    async with semaphore:
                        try:
                            await client.delete(
                                f"{self.base_url}/api/user/{username}",
                                headers={"Authorization": f"Bearer {self.token}"},
                                timeout=30.0
                            )
                        except:
                            pass

                await asyncio.gather(*[delete_one(u) for u in test_users])
                print(f"удалено {len(test_users)}")

        except Exception as e:
            print(f"ошибка: {e}")

    async def run_test(
        self,
        num_users: int,
        concurrency: int,
        level: str = "custom",
        skip_cleanup: bool = False
    ) -> TestResults:
        """Запускает полный цикл тестирования."""

        results = TestResults(
            level=level,
            target_users=num_users,
            concurrency=concurrency,
            database_type=self.db_type,
            start_time=datetime.now()
        )

        # Очистка перед тестом
        if not skip_cleanup:
            await self.cleanup_test_users()

        # Сброс и получение начальных метрик профайлера
        await self.reset_profiler()
        results.profiler_before = await self.get_profiler_stats()

        prefix = "lt_"
        usernames = [f"{prefix}{i}" for i in range(num_users)]

        # Лимиты для httpx
        limits = httpx.Limits(
            max_keepalive_connections=concurrency,
            max_connections=concurrency * 2
        )

        async with httpx.AsyncClient(limits=limits, timeout=120.0) as client:

            # === ТЕСТ СОЗДАНИЯ ===
            print(f"\n{'='*60}")
            print(f"СОЗДАНИЕ: {num_users:,} пользователей, concurrency={concurrency}")
            print(f"{'='*60}")

            create_metrics = results.add_metric("create")
            semaphore = asyncio.Semaphore(concurrency)

            async def create_with_sem(username):
                async with semaphore:
                    await self._create_user(client, username, create_metrics)

            start = time.perf_counter()

            # Прогресс
            total = len(usernames)
            completed = 0

            async def create_with_progress(username):
                nonlocal completed
                await create_with_sem(username)
                completed += 1
                if completed % 1000 == 0 or completed == total:
                    pct = completed / total * 100
                    rate = completed / (time.perf_counter() - start)
                    print(f"\r  Прогресс: {completed:,}/{total:,} ({pct:.1f}%) - {rate:.1f} оп/сек", end="", flush=True)

            await asyncio.gather(*[create_with_progress(u) for u in usernames])

            create_time = time.perf_counter() - start
            print(f"\n  Завершено за {create_time:.1f}с, {num_users/create_time:.1f} оп/сек")
            print(f"  Успешно: {create_metrics.count:,}, Ошибок: {create_metrics.errors:,}")

            # Список успешно созданных для последующих тестов
            created_users = usernames[:create_metrics.count]

            if not created_users:
                print("  ОШИБКА: Не удалось создать пользователей!")
                if create_metrics.error_messages:
                    print(f"  Примеры ошибок: {create_metrics.error_messages[:3]}")
                results.end_time = datetime.now()
                return results

            # === ТЕСТ ОБНОВЛЕНИЯ ===
            print(f"\n{'='*60}")
            print(f"ОБНОВЛЕНИЕ: {len(created_users):,} пользователей (disable → enable)")
            print(f"{'='*60}")

            update_metrics = results.add_metric("update")
            completed = 0
            total = len(created_users) * 2

            async def update_with_sem(username, status):
                async with semaphore:
                    await self._update_user(client, username, status, update_metrics)

            async def update_with_progress(username, status):
                nonlocal completed
                await update_with_sem(username, status)
                completed += 1
                if completed % 1000 == 0 or completed == total:
                    pct = completed / total * 100
                    print(f"\r  Прогресс: {completed:,}/{total:,} ({pct:.1f}%)", end="", flush=True)

            start = time.perf_counter()

            # Disable all
            await asyncio.gather(*[update_with_progress(u, "disabled") for u in created_users])
            # Enable all
            await asyncio.gather(*[update_with_progress(u, "active") for u in created_users])

            update_time = time.perf_counter() - start
            print(f"\n  Завершено за {update_time:.1f}с, {total/update_time:.1f} оп/сек")

            # === ТЕСТ УДАЛЕНИЯ ===
            print(f"\n{'='*60}")
            print(f"УДАЛЕНИЕ: {len(created_users):,} пользователей")
            print(f"{'='*60}")

            delete_metrics = results.add_metric("delete")
            completed = 0
            total = len(created_users)

            async def delete_with_sem(username):
                async with semaphore:
                    await self._delete_user(client, username, delete_metrics)

            async def delete_with_progress(username):
                nonlocal completed
                await delete_with_sem(username)
                completed += 1
                if completed % 1000 == 0 or completed == total:
                    pct = completed / total * 100
                    rate = completed / (time.perf_counter() - start)
                    print(f"\r  Прогресс: {completed:,}/{total:,} ({pct:.1f}%) - {rate:.1f} оп/сек", end="", flush=True)

            start = time.perf_counter()
            await asyncio.gather(*[delete_with_progress(u) for u in created_users])

            delete_time = time.perf_counter() - start
            print(f"\n  Завершено за {delete_time:.1f}с, {len(created_users)/delete_time:.1f} оп/сек")

        # Финальные метрики
        results.profiler_after = await self.get_profiler_stats()
        results.end_time = datetime.now()

        return results


def print_results(results: TestResults):
    """Выводит результаты в консоль."""
    print("\n")
    print("=" * 70)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 70)

    print(f"\nТест: {results.level}")
    print(f"База данных: {results.database_type}")
    print(f"Целевое количество: {results.target_users:,} пользователей")
    print(f"Параллельность: {results.concurrency}")
    print(f"Общее время: {results.duration_seconds:.1f} секунд")

    print("\n" + "-" * 70)
    print(f"{'Операция':<15} {'Успешно':>10} {'Ошибки':>10} {'Ср.время':>12} {'P95':>12} {'P99':>12}")
    print("-" * 70)

    for name, metrics in results.metrics.items():
        data = metrics.to_dict()
        print(f"{name:<15} {data.get('count', 0):>10,} {data.get('errors', 0):>10,} "
              f"{data.get('mean_ms', 0):>10.1f}мс {data.get('p95_ms', 0):>10.1f}мс {data.get('p99_ms', 0):>10.1f}мс")

    # Профайлер
    diff = results._calc_profiler_diff()
    if diff:
        print("\n" + "-" * 70)
        print("МЕТРИКИ ПРОФАЙЛЕРА (XrayManager)")
        print("-" * 70)
        print(f"{'Операция':<25} {'Вызовов':>10} {'Ср.время':>12} {'P95':>12}")
        print("-" * 70)

        for name, data in sorted(diff.items(), key=lambda x: x[1].get('calls', 0), reverse=True):
            if 'xray' in name.lower():
                print(f"{name:<25} {data.get('calls', 0):>10,} "
                      f"{data.get('avg_ms', 0):>10.2f}мс {data.get('p95_ms', 0):>10.2f}мс")

    print("\n" + "=" * 70)


def save_results(results: TestResults, filename: str):
    """Сохраняет результаты в JSON файл."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"\nРезультаты сохранены в: {filename}")


async def main():
    parser = argparse.ArgumentParser(
        description="Нагрузочный тест Marzban API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python tests/load_test_full.py --level quick     # 10,000 операций
  python tests/load_test_full.py --level medium    # 100,000 операций
  python tests/load_test_full.py --level full      # 1,000,000 операций
  python tests/load_test_full.py --users 50000 --concurrent 100
        """
    )

    parser.add_argument("--url", default="http://localhost:8000", help="URL API")
    parser.add_argument("--level", choices=["quick", "medium", "full"], help="Уровень нагрузки")
    parser.add_argument("--users", type=int, help="Количество пользователей")
    parser.add_argument("--concurrent", type=int, default=50, help="Параллельность")
    parser.add_argument("--output", help="Файл для сохранения результатов (JSON)")
    parser.add_argument("--skip-cleanup", action="store_true", help="Пропустить очистку")

    args = parser.parse_args()

    # Определяем параметры теста
    if args.level:
        config = LOAD_LEVELS[args.level]
        num_users = config["users"]
        concurrency = config["concurrent"]
        level = args.level
        print(f"\n{config['description']}")
    elif args.users:
        num_users = args.users
        concurrency = args.concurrent
        level = "custom"
    else:
        # По умолчанию quick
        config = LOAD_LEVELS["quick"]
        num_users = config["users"]
        concurrency = config["concurrent"]
        level = "quick"
        print(f"\n{config['description']} (по умолчанию)")

    print(f"Параметры: {num_users:,} пользователей, concurrency={concurrency}")
    print(f"URL: {args.url}")

    # Запуск
    tester = LoadTester(args.url)

    if not await tester.setup():
        sys.exit(1)

    results = await tester.run_test(
        num_users=num_users,
        concurrency=concurrency,
        level=level,
        skip_cleanup=args.skip_cleanup
    )

    print_results(results)

    # Сохранение
    if args.output:
        save_results(results, args.output)
    else:
        # Авто-имя файла
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"load_test_{level}_{timestamp}.json"
        save_results(results, filename)


if __name__ == "__main__":
    asyncio.run(main())
