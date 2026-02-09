import requests
import sys
import random
import string

MARZBAN_URL = ""
ADMIN_USERNAME = ""
ADMIN_PASSWORD = ""


def random_username(prefix="user", length=16):
    chars = string.ascii_lowercase + string.digits
    suffix = "".join(random.choice(chars) for _ in range(length))
    return f"{prefix}_{suffix}"


def get_token():
    url = f"{MARZBAN_URL}/api/admin/token"
    data = {
        "username": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD
    }
    try:
        r = requests.post(url, data=data, timeout=10)
        r.raise_for_status()
        token = r.json().get("access_token")
        if not token:
            print("❌ Failed to obtain token: missing access_token in response")
            sys.exit(1)
        return token
    except requests.RequestException as e:
        print(f"❌ Authentication error: {e}")
        sys.exit(1)


def get_inbounds(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.get(
        f"{MARZBAN_URL}/api/inbounds",
        headers=headers,
        timeout=10
    )
    r.raise_for_status()

    inbounds = r.json()

    user_inbounds = {}
    for inbound in inbounds:
        tags = []
        for tag_group in inbounds[inbound]:
            tags.append(tag_group["tag"])
        user_inbounds[inbound] = tags

    return user_inbounds


def get_proxies(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.get(
        f"{MARZBAN_URL}/api/inbounds",
        headers=headers,
        timeout=10
    )
    r.raise_for_status()

    inbounds = r.json()

    user_proxies = {}
    for inbound in inbounds:
        user_proxies[inbound] = {}

        if inbound == "vless":
            user_proxies[inbound]["flow"] = "xtls-rprx-vision"

    return user_proxies


def create_user(
    token,
    username,
    status="active",
    expire=0,
    data_limit=0,
    note=""
):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    proxies = get_proxies(token)
    inbounds = get_inbounds(token)

    payload = {
        "username": username,
        "status": status,
        "expire": expire,
        "data_limit": data_limit,
        "note": note,
        "proxies": proxies,
        "inbounds": inbounds,
        "data_limit_reset_strategy": "no_reset",
        "auto_delete_in_days": -1
    }

    try:
        r = requests.post(
            f"{MARZBAN_URL}/api/user",
            headers=headers,
            json=payload,
            timeout=10
        )
        r.raise_for_status()
        print(f"✅ User created: {username}")
        return r.json()

    except requests.HTTPError:
        print(f"❌ Failed to create user {username}: {r.status_code} {r.text}")
    except requests.RequestException as e:
        print(f"❌ Error creating user {username}: {e}")


if __name__ == "__main__":
    token = get_token()
    N = input("How many users to create? ")

    for i in range(int(N)):
        username = random_username()
        create_user(
            token=token,
            username=username,
            status="active",
            expire=0,
            data_limit=0,
            note="Created via script"
        )
