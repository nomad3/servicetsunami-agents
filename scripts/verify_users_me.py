import requests
import sys

BASE_URL = "http://localhost:8000"

def verify():
    # 1. Login
    print("1. Logging in...")
    login_data = {
        "username": "test@example.com",
        "password": "password"
    }

    try:
        response = requests.post(f"{BASE_URL}/api/v1/auth/login", data=login_data)
    except requests.exceptions.ConnectionError:
        print(f"❌ Could not connect to {BASE_URL}. Is the API running?")
        sys.exit(1)

    if response.status_code != 200:
        print(f"❌ Login failed: {response.status_code}")
        print(response.text)
        sys.exit(1)

    token = response.json()["access_token"]
    print("✅ Login successful")

    # 2. Get User Me
    print("\n2. Fetching /users/me...")
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{BASE_URL}/api/v1/users/me", headers=headers)

    if response.status_code == 200:
        user_data = response.json()
        print("✅ Success!")
        print(f"User: {user_data.get('email')}")
        print(f"Tenant: {user_data.get('tenant', {}).get('name', 'N/A')}")

        # 3. Get Dashboard Stats
        print("\n3. Fetching /analytics/dashboard...")
        stats_response = requests.get(f"{BASE_URL}/api/v1/analytics/dashboard", headers=headers)

        if stats_response.status_code == 200:
            stats_data = stats_response.json()
            print("✅ Analytics Success!")
            print(f"Overview: {stats_data.get('overview')}")
        else:
            print(f"❌ Analytics Failed: {stats_response.status_code}")
            print(stats_response.text)

    else:
        print(f"❌ Failed: {response.status_code}")
        print(response.text)
        sys.exit(1)

if __name__ == "__main__":
    verify()
