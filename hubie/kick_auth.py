"""One-time Kick login for Hubie's chat. Run on the server, then open the printed link on your Mac.

  ssh -L 8765:localhost:8765 hubie@<server>
  /opt/hubie/venv/bin/python -m hubie.kick_auth

Log in to Kick as the HUBIE account, approve, and the token is saved to /opt/hubie/kick_tokens.json.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

from .config import ROOT
from .kick import TOKEN_URL, TOKENS

REDIRECT = "http://localhost:8765/callback"
SCOPES = "user:read channel:read channel:write chat:write events:subscribe moderation:ban"


def main():
    cid, sec = os.environ.get("KICK_CLIENT_ID"), os.environ.get("KICK_CLIENT_SECRET")
    if not cid or not sec:
        raise SystemExit("KICK_CLIENT_ID / KICK_CLIENT_SECRET missing from /opt/hubie/.env")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://id.kick.com/oauth/authorize?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    print("\n1) Make sure you're logged in to kick.com as the HUBIE account in your Mac browser.")
    print("2) Open this link on your Mac and click Approve:\n")
    print(url + "\n")
    result = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] != state or "code" not in q:
                self.send_response(400); self.end_headers(); self.wfile.write(b"Bad callback"); return
            result["code"] = q["code"][0]
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Hubie is connected to Kick chat. You can close this tab.")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 8765), H)
    while "code" not in result:
        srv.handle_request()
    r = httpx.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "client_id": cid, "client_secret": sec,
        "redirect_uri": REDIRECT, "code_verifier": verifier, "code": result["code"]}, timeout=20)
    if r.status_code >= 300:
        raise SystemExit(f"Token exchange failed: {r.status_code} {r.text[:300]}")
    tok = r.json()
    tok["obtained_at"] = time.time()
    TOKENS.write_text(json.dumps(tok))
    os.chmod(TOKENS, 0o600)
    print("Saved. Hubie can now talk in chat. Scopes:", tok.get("scope"))


if __name__ == "__main__":
    main()
