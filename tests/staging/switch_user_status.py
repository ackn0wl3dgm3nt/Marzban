import requests
import sys
import time

MARZBAN_URL = ""
ADMIN_USERNAME = ""
ADMIN_PASSWORD = ""
USER_STATUS = input("User status - active / disabled >> ")

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

def change_all_users_status(token):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        users = requests.get(f"{MARZBAN_URL}/api/users", headers=headers, timeout=10).json()["users"]
        print(users)
    except requests.RequestException as e:
        print(f"❌ Failed to fetch users: {e}")
        sys.exit(1)

    print(f"Found {len(users)} users. {USER_STATUS} them all...\n")

    for user in users:
        username = user["username"]
        user["status"] = USER_STATUS
        try:
            r = requests.put(
                f"{MARZBAN_URL}/api/user/{username}",
                headers=headers,
                json=user,
                timeout=10
            )
            if r.status_code == 200:
                print(f"✅ {USER_STATUS} user: {username}")
            else:
                print(f"⚠️ Failed to {USER_STATUS} {username}: {r.status_code} {r.text}")
        except requests.RequestException as e:
            print(f"⚠️ Error {USER_STATUS} {username}: {e}")


if __name__ == "__main__":
    token = get_token()

    start = time.time()
    change_all_users_status(token)
    end = time.time()
    print(f"Execution time: {end - start:.2f} seconds")
