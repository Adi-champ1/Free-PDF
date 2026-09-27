"""
One-time helper: run on YOUR computer to create the Google Drive refresh token.

    pip install google-auth-oauthlib
    python get_refresh_token.py            # needs client_secret.json in this folder

A browser opens -> log in with the Google account that owns the Drive folder -> allow.
Paste the printed block into Streamlit Cloud -> App -> Settings -> Secrets.
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
cfg = json.loads(Path("client_secret.json").read_text())
cfg = cfg.get("installed") or cfg.get("web")

print("\n# ---- copy everything below into your secrets ----")
print("[google_oauth]")
print(f'client_id = "{cfg["client_id"]}"')
print(f'client_secret = "{cfg["client_secret"]}"')
print(f'refresh_token = "{creds.refresh_token}"')
