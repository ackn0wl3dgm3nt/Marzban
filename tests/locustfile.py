"""
Locust load test for Marzban API.

Install:
    pip install locust

Run:
    locust -f tests/locustfile.py --host http://localhost:8000

Web UI: http://localhost:8089
"""
import random
import string
from locust import HttpUser, task, between, events


def random_username(prefix="locust_"):
    """Generate random username."""
    return prefix + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))


class MarzbanUser(HttpUser):
    """Simulates a Marzban admin user performing operations."""

    wait_time = between(0.1, 0.5)  # Wait between tasks
    token = None
    inbounds = None
    created_users = []

    def on_start(self):
        """Login and get token."""
        response = self.client.post(
            "/api/admin/token",
            data={"username": "admin", "password": "admin"}
        )
        if response.status_code == 200:
            self.token = response.json()["access_token"]

            # Get inbounds
            resp = self.client.get(
                "/api/inbounds",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if resp.status_code == 200:
                self.inbounds = resp.json()
        else:
            print(f"Login failed: {response.text}")

    def on_stop(self):
        """Cleanup created users."""
        for username in self.created_users:
            self.client.delete(
                f"/api/user/{username}",
                headers={"Authorization": f"Bearer {self.token}"},
                name="/api/user/[cleanup]"
            )
        self.created_users.clear()

    def _get_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _get_user_payload(self, username):
        """Generate user payload based on available inbounds."""
        if not self.inbounds:
            return None

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

    @task(10)
    def create_user(self):
        """Create a new user."""
        if not self.token or not self.inbounds:
            return

        username = random_username()
        payload = self._get_user_payload(username)

        response = self.client.post(
            "/api/user",
            json=payload,
            headers=self._get_headers(),
            name="/api/user [CREATE]"
        )

        if response.status_code == 200:
            self.created_users.append(username)
            # Keep max 50 users per locust user
            if len(self.created_users) > 50:
                old_user = self.created_users.pop(0)
                self.client.delete(
                    f"/api/user/{old_user}",
                    headers=self._get_headers(),
                    name="/api/user [DELETE-cleanup]"
                )

    @task(5)
    def update_user(self):
        """Update an existing user (disable/enable)."""
        if not self.token or not self.created_users:
            return

        username = random.choice(self.created_users)
        status = random.choice(["active", "disabled"])

        self.client.put(
            f"/api/user/{username}",
            json={"status": status},
            headers=self._get_headers(),
            name="/api/user/[username] [UPDATE]"
        )

    @task(3)
    def get_user(self):
        """Get user info."""
        if not self.token or not self.created_users:
            return

        username = random.choice(self.created_users)

        self.client.get(
            f"/api/user/{username}",
            headers=self._get_headers(),
            name="/api/user/[username] [GET]"
        )

    @task(2)
    def list_users(self):
        """List users."""
        if not self.token:
            return

        self.client.get(
            "/api/users",
            headers=self._get_headers(),
            params={"limit": 50}
        )

    @task(1)
    def delete_user(self):
        """Delete a user."""
        if not self.token or not self.created_users:
            return

        username = self.created_users.pop(random.randrange(len(self.created_users)))

        self.client.delete(
            f"/api/user/{username}",
            headers=self._get_headers(),
            name="/api/user/[username] [DELETE]"
        )

    @task(1)
    def get_profiler_stats(self):
        """Get profiler statistics."""
        if not self.token:
            return

        self.client.get(
            "/api/core/profiler",
            headers=self._get_headers(),
            name="/api/core/profiler"
        )


class HighLoadUser(HttpUser):
    """
    High-load user that focuses only on create/delete for stress testing.

    Run with:
        locust -f tests/locustfile.py --host http://localhost:8000 HighLoadUser
    """

    wait_time = between(0, 0.1)  # Minimal wait
    token = None
    inbounds = None
    user_counter = 0

    def on_start(self):
        """Login and get token."""
        response = self.client.post(
            "/api/admin/token",
            data={"username": "admin", "password": "admin"}
        )
        if response.status_code == 200:
            self.token = response.json()["access_token"]

            resp = self.client.get(
                "/api/inbounds",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if resp.status_code == 200:
                self.inbounds = resp.json()

    def _get_headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _get_user_payload(self, username):
        if not self.inbounds:
            return None

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

        return payload

    @task
    def create_and_delete(self):
        """Create a user and immediately delete it."""
        if not self.token or not self.inbounds:
            return

        self.user_counter += 1
        username = f"stress_{id(self)}_{self.user_counter}"
        payload = self._get_user_payload(username)

        # Create
        response = self.client.post(
            "/api/user",
            json=payload,
            headers=self._get_headers(),
            name="/api/user [CREATE-stress]"
        )

        if response.status_code == 200:
            # Immediately delete
            self.client.delete(
                f"/api/user/{username}",
                headers=self._get_headers(),
                name="/api/user [DELETE-stress]"
            )


# Event hooks for reporting
@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print profiler stats at the end of the test."""
    print("\n" + "=" * 60)
    print("Test completed. Check /api/core/profiler for server-side metrics.")
    print("=" * 60)
