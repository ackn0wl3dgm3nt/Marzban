import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import sys
import time

logging.basicConfig(level=logging.INFO)

MARZBAN_URL = ""
ADMIN_USERNAME = ""
ADMIN_PASSWORD = ""

MAX_WORKERS = 100
REQUEST_TIMEOUT = 120


def get_token() -> str:
    """Authenticate admin and return access token."""
    url = f"{MARZBAN_URL}/api/admin/token"
    payload = {
        "username": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD,
    }

    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            raise ValueError("access_token missing in response")
        return token
    except (requests.RequestException, ValueError) as e:
        print(f"❌ Authentication failed: {e}")
        sys.exit(1)


def fetch_users(headers: dict) -> list[dict]:
    """Fetch all users from Marzban."""
    try:
        r = requests.get(
            f"{MARZBAN_URL}/api/users",
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["users"]
    except (requests.RequestException, KeyError) as e:
        print(f"❌ Failed to fetch users: {e}")
        sys.exit(1)


def update_user_status(user: dict, headers: dict, status: str) -> str:
    """Update status for a single user."""
    username = user["username"]
    user["status"] = status

    try:
        r = requests.put(
            f"{MARZBAN_URL}/api/user/{username}",
            headers=headers,
            json=user,
            timeout=REQUEST_TIMEOUT,
        )

        if r.status_code == 200:
            return f"✅ {status} user: {username}"
        return f"⚠️ Failed to {status} {username}: {r.status_code} {r.text}"

    except requests.RequestException as e:
        return f"⚠️ Error {status} {username}: {e}"


def change_all_users_status(token: str, status: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}

    users = fetch_users(headers)
    print(f"Found {len(users)} users. Setting status → {status}\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [
            executor.submit(update_user_status, user, headers, status)
            for user in users
        ]

        for future in as_completed(futures):
            logging.info(future.result())


def main():
    status = input("User status (active / disabled) >> ").strip().lower()
    if status not in {"active", "disabled"}:
        print("❌ Invalid status. Use: active or disabled")
        sys.exit(1)

    token = get_token()

    start = time.time()
    change_all_users_status(token, status)
    end = time.time()

    print(f"\n⏱ Execution time: {end - start:.2f} seconds")


if __name__ == "__main__":
    main()
