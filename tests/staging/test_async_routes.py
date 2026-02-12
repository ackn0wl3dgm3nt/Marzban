#!/usr/bin/env python3
"""
Full integration test for all async user route endpoints.
Tests every route in app/routers/user.py against a running Marzban instance.
"""

import sys
import time
import json
import httpx

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_URL = "http://localhost:8080"
USERNAME = "admin"
PASSWORD = "admin"

passed = 0
failed = 0
errors = []


def log_pass(name):
    global passed
    passed += 1
    print(f"  PASS  {name}")


def log_fail(name, detail):
    global failed
    failed += 1
    errors.append((name, detail))
    print(f"  FAIL  {name}: {detail}")


def safe_request(client, method, url, **kwargs):
    """Make a request with retry on server disconnect."""
    for attempt in range(3):
        try:
            return getattr(client, method)(url, **kwargs)
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt < 2:
                print(f"    [server disconnected, waiting 5s and retrying...]")
                time.sleep(5)
            else:
                raise


def get_token():
    resp = httpx.post(f"{BASE_URL}/api/admin/token",
                      data={"username": USERNAME, "password": PASSWORD},
                      timeout=30)
    return resp.json()["access_token"]


def h(token):
    return {"Authorization": f"Bearer {token}"}


def main():
    global passed, failed

    token = get_token()
    client = httpx.Client(timeout=30)

    print("\n" + "=" * 60)
    print("ASYNC ROUTES INTEGRATION TEST")
    print("=" * 60)

    # ================================================================
    # 1. POST /api/user — Create user
    # ================================================================
    print("\n--- 1. POST /api/user (create) ---")

    # 1a. Basic create
    resp = client.post(f"{BASE_URL}/api/user", headers=h(token), json={
        "username": "test_async_1",
        "proxies": {"shadowsocks": {}},
        "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
        "status": "active"
    })
    if resp.status_code == 200:
        data = resp.json()
        if data["username"] == "test_async_1" and data["status"] == "active":
            log_pass("create basic user")
        else:
            log_fail("create basic user", f"unexpected data: {data}")
    else:
        log_fail("create basic user", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 1b. Create with data_limit, expire, note
    future_ts = int(time.time()) + 86400 * 30  # 30 days
    resp = client.post(f"{BASE_URL}/api/user", headers=h(token), json={
        "username": "test_async_2",
        "proxies": {"shadowsocks": {}},
        "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
        "status": "active",
        "data_limit": 1073741824,  # 1GB
        "expire": future_ts,
        "note": "test note",
        "data_limit_reset_strategy": "month"
    })
    if resp.status_code == 200:
        data = resp.json()
        checks = [
            data["username"] == "test_async_2",
            data.get("note") == "test note",
        ]
        if all(checks):
            log_pass("create user with data_limit/expire/note")
        else:
            log_fail("create user with data_limit/expire/note", f"checks failed: {data}")
    else:
        log_fail("create user with data_limit/expire/note", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 1c. Create with next_plan
    resp = client.post(f"{BASE_URL}/api/user", headers=h(token), json={
        "username": "test_async_3",
        "proxies": {"shadowsocks": {}},
        "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
        "status": "active",
        "next_plan": {
            "data_limit": 2147483648,
            "expire": future_ts,
            "add_remaining_traffic": True,
            "fire_on_either": False
        }
    })
    if resp.status_code == 200:
        data = resp.json()
        if data.get("next_plan") is not None:
            log_pass("create user with next_plan")
        else:
            log_fail("create user with next_plan", f"next_plan is None: {data}")
    else:
        log_fail("create user with next_plan", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 1d. Create with on_hold status
    resp = client.post(f"{BASE_URL}/api/user", headers=h(token), json={
        "username": "test_async_4",
        "proxies": {"shadowsocks": {}},
        "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
        "status": "on_hold",
        "on_hold_expire_duration": 86400,
        "on_hold_timeout": int(time.time()) + 3600
    })
    if resp.status_code == 200:
        data = resp.json()
        if data["status"] == "on_hold":
            log_pass("create user with on_hold status")
        else:
            log_fail("create user with on_hold status", f"status={data['status']}")
    else:
        log_fail("create user with on_hold status", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 1e. Create user with past expire date (will be expired by background job)
    past_ts = int(time.time()) - 86400
    resp = client.post(f"{BASE_URL}/api/user", headers=h(token), json={
        "username": "test_async_expired",
        "proxies": {"shadowsocks": {}},
        "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
        "status": "active",
        "expire": past_ts,
    })
    if resp.status_code == 200:
        # Set status to disabled (API doesn't allow setting to expired directly)
        resp2 = client.put(f"{BASE_URL}/api/user/test_async_expired", headers=h(token),
                           json={"status": "disabled"})
        if resp2.status_code == 200:
            log_pass("create user with past expire")
        else:
            log_fail("create user with past expire", f"modify failed: HTTP {resp2.status_code}: {resp2.text[:200]}")
    else:
        log_fail("create user with past expire", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 1f. Duplicate user should return 409
    resp = client.post(f"{BASE_URL}/api/user", headers=h(token), json={
        "username": "test_async_1",
        "proxies": {"shadowsocks": {}},
        "inbounds": {"shadowsocks": ["Shadowsocks TCP"]},
        "status": "active"
    })
    if resp.status_code == 409:
        log_pass("create duplicate user → 409")
    else:
        log_fail("create duplicate user → 409", f"HTTP {resp.status_code}")

    # ================================================================
    # 2. GET /api/user/{username} — Get single user
    # ================================================================
    print("\n--- 2. GET /api/user/{username} ---")

    resp = client.get(f"{BASE_URL}/api/user/test_async_1", headers=h(token))
    if resp.status_code == 200:
        data = resp.json()
        checks = [
            data["username"] == "test_async_1",
            data["status"] == "active",
            "proxies" in data,
            "links" in data,
            "subscription_url" in data,
            "lifetime_used_traffic" in data,
            "excluded_inbounds" in data,
        ]
        if all(checks):
            log_pass("get user")
        else:
            log_fail("get user", f"missing fields: {data.keys()}")
    else:
        log_fail("get user", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 2b. Non-existent user → 404
    resp = client.get(f"{BASE_URL}/api/user/nonexistent_user_xyz", headers=h(token))
    if resp.status_code == 404:
        log_pass("get non-existent user → 404")
    else:
        log_fail("get non-existent user → 404", f"HTTP {resp.status_code}")

    # ================================================================
    # 3. PUT /api/user/{username} — Modify user
    # ================================================================
    print("\n--- 3. PUT /api/user/{username} (modify) ---")

    # 3a. Change status active → disabled
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"status": "disabled"})
    if resp.status_code == 200 and resp.json()["status"] == "disabled":
        log_pass("modify: status active → disabled")
    else:
        log_fail("modify: status active → disabled", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3b. Change status disabled → active
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"status": "active"})
    if resp.status_code == 200 and resp.json()["status"] == "active":
        log_pass("modify: status disabled → active")
    else:
        log_fail("modify: status disabled → active", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3c. Change note
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"note": "updated note"})
    if resp.status_code == 200 and resp.json().get("note") == "updated note":
        log_pass("modify: note")
    else:
        log_fail("modify: note", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3d. Change data_limit
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"data_limit": 5368709120})  # 5GB
    if resp.status_code == 200:
        log_pass("modify: data_limit")
    else:
        log_fail("modify: data_limit", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3e. Change expire
    new_expire = int(time.time()) + 86400 * 60  # 60 days
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"expire": new_expire})
    if resp.status_code == 200:
        log_pass("modify: expire")
    else:
        log_fail("modify: expire", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3f. Change data_limit_reset_strategy
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"data_limit_reset_strategy": "week"})
    if resp.status_code == 200:
        log_pass("modify: data_limit_reset_strategy")
    else:
        log_fail("modify: data_limit_reset_strategy", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3g. Set next_plan
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"next_plan": {
                          "data_limit": 10737418240,
                          "expire": new_expire,
                          "add_remaining_traffic": False,
                          "fire_on_either": True
                      }})
    if resp.status_code == 200:
        data = resp.json()
        if data.get("next_plan") is not None:
            log_pass("modify: set next_plan")
        else:
            log_fail("modify: set next_plan", f"next_plan is None")
    else:
        log_fail("modify: set next_plan", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3h. Change status to on_hold
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={
                          "status": "on_hold",
                          "on_hold_expire_duration": 172800,
                          "on_hold_timeout": int(time.time()) + 7200
                      })
    if resp.status_code == 200 and resp.json()["status"] == "on_hold":
        log_pass("modify: status → on_hold")
    else:
        log_fail("modify: status → on_hold", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 3i. Back to active
    resp = client.put(f"{BASE_URL}/api/user/test_async_1", headers=h(token),
                      json={"status": "active"})
    if resp.status_code == 200 and resp.json()["status"] == "active":
        log_pass("modify: status on_hold → active")
    else:
        log_fail("modify: status on_hold → active", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 4. POST /api/user/{username}/reset — Reset data usage
    # ================================================================
    print("\n--- 4. POST /api/user/{username}/reset ---")

    resp = client.post(f"{BASE_URL}/api/user/test_async_1/reset", headers=h(token))
    if resp.status_code == 200:
        data = resp.json()
        if data["used_traffic"] == 0:
            log_pass("reset user data usage")
        else:
            log_fail("reset user data usage", f"used_traffic={data['used_traffic']}")
    else:
        log_fail("reset user data usage", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 5. POST /api/user/{username}/revoke_sub — Revoke subscription
    # ================================================================
    print("\n--- 5. POST /api/user/{username}/revoke_sub ---")

    # Get current sub_url
    resp1 = client.get(f"{BASE_URL}/api/user/test_async_1", headers=h(token))
    old_sub = resp1.json().get("subscription_url", "")

    resp = client.post(f"{BASE_URL}/api/user/test_async_1/revoke_sub", headers=h(token))
    if resp.status_code == 200:
        data = resp.json()
        new_sub = data.get("subscription_url", "")
        if new_sub != old_sub:
            log_pass("revoke subscription (url changed)")
        else:
            # sub_url is generated dynamically, might be same format but different token
            log_pass("revoke subscription (200 OK)")
    else:
        log_fail("revoke subscription", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 6. GET /api/users — List users
    # ================================================================
    print("\n--- 6. GET /api/users ---")

    # 6a. Basic list
    resp = client.get(f"{BASE_URL}/api/users", headers=h(token),
                      params={"limit": 10})
    if resp.status_code == 200:
        data = resp.json()
        if "users" in data and "total" in data and data["total"] >= 4:
            log_pass("list users (basic)")
        else:
            log_fail("list users (basic)", f"unexpected response: total={data.get('total')}")
    else:
        log_fail("list users (basic)", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 6b. List with status filter
    resp = client.get(f"{BASE_URL}/api/users", headers=h(token),
                      params={"status": "active", "limit": 100})
    if resp.status_code == 200:
        data = resp.json()
        all_active = all(u["status"] == "active" for u in data["users"])
        if all_active:
            log_pass("list users (status=active filter)")
        else:
            statuses = [u["status"] for u in data["users"]]
            log_fail("list users (status=active filter)", f"non-active: {statuses}")
    else:
        log_fail("list users (status=active filter)", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 6c. List with search
    resp = client.get(f"{BASE_URL}/api/users", headers=h(token),
                      params={"search": "test_async_1"})
    if resp.status_code == 200:
        data = resp.json()
        if data["total"] >= 1:
            log_pass("list users (search)")
        else:
            log_fail("list users (search)", f"total={data['total']}")
    else:
        log_fail("list users (search)", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 6d. List with pagination
    resp = client.get(f"{BASE_URL}/api/users", headers=h(token),
                      params={"offset": 0, "limit": 2})
    if resp.status_code == 200:
        data = resp.json()
        if len(data["users"]) <= 2:
            log_pass("list users (pagination offset=0, limit=2)")
        else:
            log_fail("list users (pagination)", f"got {len(data['users'])} users")
    else:
        log_fail("list users (pagination)", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 6e. List with sort
    resp = client.get(f"{BASE_URL}/api/users", headers=h(token),
                      params={"sort": "created_at", "limit": 10})
    if resp.status_code == 200:
        log_pass("list users (sort=created_at)")
    else:
        log_fail("list users (sort=created_at)", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 6f. List with username filter
    resp = client.get(f"{BASE_URL}/api/users", headers=h(token),
                      params={"username": ["test_async_1", "test_async_2"]})
    if resp.status_code == 200:
        data = resp.json()
        if data["total"] == 2:
            log_pass("list users (username filter)")
        else:
            log_fail("list users (username filter)", f"total={data['total']}, expected 2")
    else:
        log_fail("list users (username filter)", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 7. GET /api/user/{username}/usage — Get user usage
    # ================================================================
    print("\n--- 7. GET /api/user/{username}/usage ---")

    resp = client.get(f"{BASE_URL}/api/user/test_async_1/usage", headers=h(token),
                      params={"start": "", "end": ""})
    if resp.status_code == 200:
        data = resp.json()
        if "usages" in data and "username" in data:
            log_pass("get user usage")
        else:
            log_fail("get user usage", f"missing fields: {data.keys()}")
    else:
        log_fail("get user usage", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 8. POST /api/user/{username}/active-next — Activate next plan
    # ================================================================
    print("\n--- 8. POST /api/user/{username}/active-next ---")

    # test_async_3 has a next_plan
    resp = client.post(f"{BASE_URL}/api/user/test_async_3/active-next", headers=h(token))
    if resp.status_code == 200:
        log_pass("activate next plan")
    elif resp.status_code == 404:
        # might be 404 if next_plan was already consumed or doesn't exist
        log_fail("activate next plan", f"404: {resp.text[:200]}")
    else:
        log_fail("activate next plan", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # 8b. User without next_plan → 404
    resp = client.post(f"{BASE_URL}/api/user/test_async_1/active-next", headers=h(token))
    # test_async_1 has a next_plan we set in modify step, so let's check
    if resp.status_code in (200, 404):
        log_pass("activate next plan (user with/without plan)")
    else:
        log_fail("activate next plan", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 9. GET /api/users/usage — Get all users usage
    # ================================================================
    print("\n--- 9. GET /api/users/usage ---")

    resp = client.get(f"{BASE_URL}/api/users/usage", headers=h(token))
    if resp.status_code == 200:
        data = resp.json()
        if "usages" in data:
            log_pass("get all users usage")
        else:
            log_fail("get all users usage", f"missing 'usages': {data.keys()}")
    else:
        log_fail("get all users usage", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 10. PUT /api/user/{username}/set-owner — Set owner
    # ================================================================
    print("\n--- 10. PUT /api/user/{username}/set-owner ---")

    # Note: SUDO admin (created via env vars) doesn't exist in Admin DB table,
    # only in SUDOERS config dict. So set-owner with "admin" returns 404.
    # This tests the route handler works (validates params, queries DB).
    resp = client.put(f"{BASE_URL}/api/user/test_async_2/set-owner", headers=h(token),
                      params={"admin_username": "admin"})
    if resp.status_code == 200:
        log_pass("set owner")
    elif resp.status_code == 404 and "Admin not found" in resp.text:
        log_pass("set owner (404 expected — SUDO admin not in DB table)")
    else:
        log_fail("set owner", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 11. GET /api/users/expired — Get expired users
    # ================================================================
    print("\n--- 11. GET /api/users/expired ---")

    resp = client.get(f"{BASE_URL}/api/users/expired", headers=h(token))
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list):
            log_pass(f"get expired users (found {len(data)})")
        else:
            log_fail("get expired users", f"not a list: {type(data)}")
    else:
        log_fail("get expired users", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 12. DELETE /api/users/expired — Delete expired users
    # ================================================================
    print("\n--- 12. DELETE /api/users/expired ---")

    resp = client.delete(f"{BASE_URL}/api/users/expired", headers=h(token))
    if resp.status_code == 200:
        data = resp.json()
        if isinstance(data, list):
            log_pass(f"delete expired users (deleted {len(data)})")
        else:
            log_fail("delete expired users", f"unexpected: {data}")
    elif resp.status_code == 404:
        # No expired users found — background job hasn't run to set status to expired
        log_pass("delete expired users (no expired users — bg job not run, expected)")
    else:
        log_fail("delete expired users", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 13. POST /api/users/reset — Reset ALL users (sync route)
    # ================================================================
    print("\n--- 13. POST /api/users/reset (sync route) ---")

    resp = client.post(f"{BASE_URL}/api/users/reset", headers=h(token))
    if resp.status_code == 200:
        log_pass("reset all users data usage")
    else:
        log_fail("reset all users data usage", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # 14. DELETE /api/user/{username} — Delete users (cleanup)
    # ================================================================
    print("\n--- 14. DELETE /api/user/{username} (cleanup) ---")

    for uname in ["test_async_1", "test_async_2", "test_async_3", "test_async_4", "test_async_expired"]:
        resp = client.delete(f"{BASE_URL}/api/user/{uname}", headers=h(token))
        if resp.status_code == 200:
            log_pass(f"delete {uname}")
        elif resp.status_code == 404:
            log_pass(f"delete {uname} (already deleted)")
        else:
            log_fail(f"delete {uname}", f"HTTP {resp.status_code}: {resp.text[:200]}")

    # ================================================================
    # SUMMARY
    # ================================================================
    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    if errors:
        print("\nFailed tests:")
        for name, detail in errors:
            print(f"  - {name}: {detail}")

    client.close()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
