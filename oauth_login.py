#!/usr/bin/env python3
"""One-shot Yoto login via OAuth2 authorization-code + PKCE (confidential client).

Run in YOUR terminal:   python3 oauth_login.py
Opens a browser -> you log in & approve -> it exchanges the code (id+secret) for
tokens (.yoto_tokens.json, gitignored) and fetches your library to lib.json.
Network calls go through `curl` (uses the macOS system cert store) to avoid the
python.org SSL "local issuer" problem.
"""
import base64, hashlib, http.server, json, secrets, subprocess, sys
import urllib.parse, webbrowser

CLIENT_ID   = "<YOTO_CLIENT_ID>"
SECRET_FILE = ".yoto_token"            # holds the client SECRET
REDIRECT    = "http://127.0.0.1:8787/callback"
AUTH        = "https://login.yotoplay.com"
API         = "https://api.yotoplay.com"
# NOTE: family:device-status:view is intentionally NOT here. Yoto rejects login with
# "access_denied: scopes that have not been pre-approved" because this scope isn't enabled
# on the app registration at yoto.dev. Re-add it ONLY after enabling it in the Yoto
# developer dashboard (Manage App → scopes), or login breaks entirely for ALL scopes.
SCOPES      = "openid profile offline_access family:library:view user:content:view family:devices:view family:devices:control"

client_secret = open(SECRET_FILE).read().strip()

def curl_post(url, fields, outfile):
    args = ["curl", "-sS", "-X", "POST", url, "-o", outfile, "-w", "%{http_code}"]
    for k, v in fields.items():
        args += ["--data-urlencode", f"{k}={v}"]
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()

def curl_get(url, token, outfile):
    args = ["curl", "-sS", "-H", f"Authorization: Bearer {token}", "-o", outfile, "-w", "%{http_code}", url]
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()

# --- PKCE ---
verifier  = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state     = secrets.token_urlsafe(16)

authorize_url = AUTH + "/authorize?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
    "scope": SCOPES, "audience": API,
    "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
})

result = {}
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/callback":
            self.send_response(404); self.end_headers(); return
        q = urllib.parse.parse_qs(u.query)
        for k in ("code", "state", "error", "error_description"):
            result[k] = q.get(k, [None])[0]
        ok = bool(result.get("code"))
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        msg = ("Login complete - close this tab and return to the terminal."
               if ok else f"Login error: {result.get('error')} - {result.get('error_description')}")
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:2em'><h2>{msg}</h2></body></html>".encode())
    def log_message(self, *a): pass

srv = http.server.HTTPServer(("127.0.0.1", 8787), Handler)
print("\n1) A browser tab should open. If not, paste this URL into your browser:\n")
print("   " + authorize_url + "\n")
print("2) Log in to Yoto and approve. Waiting for the redirect on http://127.0.0.1:8787 ...\n")
try: webbrowser.open(authorize_url)
except Exception: pass
while "code" not in result and not result.get("error"):
    srv.handle_request()

if result.get("error"):
    print("Authorization failed:", result["error"], "-", result.get("error_description")); sys.exit(1)
if result.get("state") != state:
    print("State mismatch - aborting for safety."); sys.exit(1)
code = result["code"]

# --- exchange code for tokens ---
http_code = curl_post(AUTH + "/oauth/token", {
    "grant_type": "authorization_code", "client_id": CLIENT_ID, "client_secret": client_secret,
    "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier,
}, ".yoto_tokens.json")
print(f"token exchange: HTTP {http_code}")
try:
    tok = json.load(open(".yoto_tokens.json"))
except Exception:
    print("  response:", open(".yoto_tokens.json").read()[:400]); sys.exit(1)
if "access_token" not in tok:
    print("  no access_token. response:", json.dumps(tok)[:400]); sys.exit(1)
at = tok["access_token"]
print(f"  access_token: {'JWT' if at.startswith('eyJ') else 'opaque'} ({len(at)} chars) | "
      f"refresh_token: {'YES' if tok.get('refresh_token') else 'no'} | scopes: {tok.get('scope','?')}")

# --- fetch library ---
lib_code = curl_get(API + "/content/mine", at, "lib.json")
print(f"library /content/mine: HTTP {lib_code}")
try:
    d = json.load(open("lib.json"))
    cards = d.get("cards") if isinstance(d, dict) else d
    if isinstance(cards, list):
        print(f"  -> {len(cards)} cards written to lib.json")
    else:
        print("  -> response:", json.dumps(d)[:300])
except Exception:
    print("  -> response:", open("lib.json").read()[:300])
