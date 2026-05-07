# run_login.py (run this once every morning)
from kite_client import get_login_url, set_access_token

print("Open this URL and login:")
print(get_login_url())

request_token = input("Paste request_token from redirect URL: ")
access_token = set_access_token(request_token)
print(f"Access token: {access_token}")

# Save it
with open("access_token.txt", "w") as f:
    f.write(access_token)