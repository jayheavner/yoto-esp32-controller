#!/usr/bin/env python3
"""Local dev proxy for the Yoto API.

Run in its OWN terminal tab and leave it running:   python3 yoto_proxy.py
It holds your tokens (.yoto_tokens.json) and forwards requests to
https://api.yotoplay.com on http://127.0.0.1:8123 with NO auth, auto-refreshing
the access token on 401 (handling refresh-token rotation). Network goes through
curl (system certs). Localhost-only, so it never leaves your machine.

This also doubles as a first prototype of the "small backend holds the secret"
architecture for the appliance.

Examples (run by the agent):
  curl -sS http://127.0.0.1:8123/card/family/library
  curl -sS http://127.0.0.1:8123/content/iXwvb
  curl -sS http://127.0.0.1:8123/device-v2/devices/mine
"""
import json, os, signal, subprocess, sys, threading, time, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

try:
    import paho.mqtt.client as mqtt
    import certifi                       # python.org Python on macOS can't find the system CA
    HAVE_MQTT = True                      # store; hand paho an explicit bundle (same reason the
except ImportError:                       # REST path uses curl, not urllib)
    HAVE_MQTT = False

# ---- cover thumbnails for the board (RGB565, the panel's native pixel format) ----
# The board can't decode 49 PNGs; we resize the cached covers here and hand it raw
# little-endian RGB565 (matches LVGL: LV_COLOR_DEPTH 16, LV_COLOR_16_SWAP 0) that it
# blits straight into an lv_img. Tile size matches app.html (122x193).
THUMB_W, THUMB_H = 122, 193          # grid tile (matches app.html); cover aspect 638:1011
IMAGES_DIR, THUMB_CACHE = "images", "thumbs"
_thumb_mem = {}   # (id,w,h) -> bytes, in-process cache

def make_thumb(card_id, w=THUMB_W, h=THUMB_H):
    """Return raw RGB565 bytes (w*h*2) for a card cover, cached on disk + memory."""
    key = (card_id, w, h)
    if key in _thumb_mem:
        return _thumb_mem[key]
    src = os.path.join(IMAGES_DIR, card_id + ".png")
    if not os.path.isfile(src):
        return None
    cache = os.path.join(THUMB_CACHE, f"{card_id}_{w}x{h}.rgb565")
    if os.path.isfile(cache) and os.path.getmtime(cache) >= os.path.getmtime(src):
        data = open(cache, "rb").read()
        _thumb_mem[key] = data
        return data
    from PIL import Image
    import numpy as np
    im = Image.open(src).convert("RGB").resize((w, h), Image.LANCZOS)
    a = np.asarray(im, dtype=np.uint16)
    r = (a[:, :, 0] >> 3) & 0x1F
    g = (a[:, :, 1] >> 2) & 0x3F
    b = (a[:, :, 2] >> 3) & 0x1F
    val = (r << 11) | (g << 5) | b
    data = val.astype("<u2").tobytes()
    os.makedirs(THUMB_CACHE, exist_ok=True)
    open(cache, "wb").write(data)
    _thumb_mem[key] = data
    return data

CLIENT_ID = "<YOTO_CLIENT_ID>"
AUTH = "https://login.yotoplay.com"
API  = "https://api.yotoplay.com"
PORT = 8123

# Playback target + safety. The board sends slim /board/cmd/* requests; we hold the
# deviceId and CLAMP volume here so a tap can never blast a 7-year-old's room.
DEVICE_ID = "<DEVICE_ID>"   # "Kiddo's Yoto" (v3e)
VOL_MAX   = 60                            # hard cap (0-100) for little ears

try:
    client_secret = open(".yoto_token").read().strip()
    tokens = json.load(open(".yoto_tokens.json"))
    assert tokens.get("access_token")
except Exception as e:
    print("ERROR: need .yoto_token (client secret) and .yoto_tokens.json (run oauth_login.py first):", e)
    sys.exit(1)

def _curl(args):
    r = subprocess.run(args, capture_output=True, text=True)
    out = r.stdout
    body, _, code = out.rpartition("__HTTP__")
    return code.strip(), body.rstrip("\n")

# Serializes token refresh across the HTTP handler threads AND the MQTT thread.
# The refresh token is single-use/rotating: two concurrent refreshes would each try to
# spend the same token and one would fail, logging us out. The lock makes the second
# caller wait and then re-read the freshly-rotated token.
_token_lock = threading.Lock()

def refresh_token():
    with _token_lock:
        print("  [proxy] access token rejected -> refreshing ...")
        code, body = _curl(["curl", "-sS", "-X", "POST", AUTH + "/oauth/token",
            "--data-urlencode", "grant_type=refresh_token",
            "--data-urlencode", f"client_id={CLIENT_ID}",
            "--data-urlencode", f"client_secret={client_secret}",
            "--data-urlencode", f"refresh_token={tokens['refresh_token']}",
            "-w", "__HTTP__%{http_code}"])
        if code == "200":
            new = json.loads(body)
            tokens["access_token"] = new["access_token"]
            if new.get("refresh_token"):      # refresh tokens rotate — persist the new one
                tokens["refresh_token"] = new["refresh_token"]
            json.dump(tokens, open(".yoto_tokens.json", "w"))
            print("  [proxy] refresh OK")
            return True
        print("  [proxy] refresh FAILED:", code, body[:200])
        return False

def upstream(method, path, body):
    args = ["curl", "-sS", "-X", method, API + path,
            "-H", f"Authorization: Bearer {tokens['access_token']}",
            "-w", "__HTTP__%{http_code}"]
    if body:
        args += ["-H", "Content-Type: application/json", "--data-binary", body]
    return _curl(args)

def _needs_refresh(code, body):
    # 401 = classic expired token. But Yoto also returns 403 {"error":{"code":"unauthorized"}}
    # for an expired access token — distinct from a genuine missing-SCOPE 403, which says
    # "forbidden"/mentions "scope". Refresh on the former, not the latter.
    if code == "401":
        return True
    return code == "403" and '"unauthorized"' in body and "scope" not in body

def request(method, path, body=None):
    """upstream() + auto-refresh (handling 401 AND expired-token 403), retried once."""
    code, resp = upstream(method, path, body)
    if _needs_refresh(code, resp) and refresh_token():
        code, resp = upstream(method, path, body)
    return code, resp

# ---- live device state via Yoto's MQTT feed (AWS IoT, custom JWT authorizer) ----
# The REST GET /device-v2/{id}/status path is a dead end (it demands a scope no third-party
# app can hold). Live now-playing instead comes from MQTT, authorized by the SAME access
# token + the family:devices:control scope we already have. We keep one long-lived connection
# here on the Mac and expose a slim snapshot at /board/state so the ESP32 never touches MQTT.
# Connection details cribbed from cdnninja/yoto_api (the reference lib).
MQTT_URL  = "aqrphjqbp3u2z-ats.iot.eu-west-2.amazonaws.com"
MQTT_PORT = 443
MQTT_AUTH = "PublicJWTAuthorizer"
HW_VOL_MAX = 16                     # player volume is a raw 0..16 scale; events report it raw

_state_lock = threading.Lock()
_state = {                          # last-known live state for DEVICE_ID (None = unknown)
    "online": None, "status": None, "cardId": None,
    "chapterKey": None, "chapterTitle": None, "trackKey": None, "trackTitle": None,
    "position": None, "trackLength": None, "volume": None, "updated": 0.0,
}
# data/events wire-key -> our state key (only playback fields the board needs)
_EVT_MAP = {
    "cardId": "cardId", "chapterKey": "chapterKey", "chapterTitle": "chapterTitle",
    "trackKey": "trackKey", "trackTitle": "trackTitle",
    "position": "position", "trackLength": "trackLength",
}
_mqtt_disc = threading.Event()      # set by on_disconnect so the worker rebuilds with a fresh JWT
_mqtt_up   = threading.Event()      # set once a CONNACK-success lands; gates the token-refresh path

def _mqtt_on_connect(client, userdata, flags, reason_code, properties=None):
    failed = getattr(reason_code, "is_failure", None)
    if failed is None:                       # older/int-style reason codes
        failed = (reason_code != 0)
    if failed:
        print(f"  [mqtt] connect refused: {reason_code}")
        return
    _mqtt_up.set()
    for suffix in ("data/events", "data/status", "status/full", "presence"):
        client.subscribe(f"device/{DEVICE_ID}/{suffix}")
    # nudge the player to push its current state immediately (it won't otherwise)
    client.publish(f"device/{DEVICE_ID}/command/events/request")
    client.publish(f"device/{DEVICE_ID}/command/status/request")
    print("  [mqtt] connected + subscribed; requested current state")

def _mqtt_on_message(client, userdata, msg):
    parts = msg.topic.split("/")
    if len(parts) < 3:
        return
    suffix = "/".join(parts[2:])
    try:
        body = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return
    if suffix == "data/events":
        print("  [mqtt] event:", json.dumps(body))   # full raw event (capture Yoto Daily's id/fields)
    with _state_lock:
        if suffix == "data/events":
            for raw, dest in _EVT_MAP.items():
                if body.get(raw) is not None:
                    _state[dest] = body[raw]
            if body.get("playbackStatus") is not None:
                _state["status"] = str(body["playbackStatus"])   # playing/paused/stopped
            if body.get("volume") is not None:                   # raw 0..16 -> percentage
                _state["volume"] = round(int(body["volume"]) / HW_VOL_MAX * 100)
            _state["online"] = True                              # a live event proves reachability
            _state["updated"] = time.time()
        elif suffix == "presence":
            _state["online"] = (body.get("state") == "online")
            _state["updated"] = time.time()

def _mqtt_on_disconnect(client, userdata, *args):
    print("  [mqtt] disconnected")
    _mqtt_disc.set()

def _mqtt_build():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id="YOTOAPI" + uuid.uuid4().hex, transport="websockets")
    with _token_lock:
        tok = tokens["access_token"]
    # AWS IoT custom authorizer: name in the username, the Yoto JWT as the password.
    c.username_pw_set(f"_?x-amz-customauthorizer-name={MQTT_AUTH}", password=tok)
    c.ws_set_options(path="/mqtt")
    c.tls_set(ca_certs=certifi.where())   # explicit CA bundle (system store is unreachable here)
    c.on_connect = _mqtt_on_connect
    c.on_message = _mqtt_on_message
    c.on_disconnect = _mqtt_on_disconnect
    return c

def _mqtt_worker():
    """Own one MQTT connection, reconnecting with exponential backoff.

    Refresh the token ONLY after a connection that was actually UP then dropped — AWS
    drops the socket when the JWT expires (~hourly), so that's the real expiry signal.
    Do NOT refresh on transport/TLS/connect failures: those aren't auth problems, and
    blindly refreshing burns the single-use rotating refresh token on every retry."""
    backoff = 1.0
    while True:
        _mqtt_disc.clear()
        _mqtt_up.clear()
        try:
            c = _mqtt_build()
            c.connect(MQTT_URL, MQTT_PORT, keepalive=60)
            c.loop_start()
            _mqtt_disc.wait()             # block until on_disconnect fires
            c.loop_stop()
            try: c.disconnect()
            except Exception: pass
        except Exception as e:
            print("  [mqtt] error:", e)
        with _state_lock:
            _state["online"] = None       # connection gone -> truth unknown
        if _mqtt_up.is_set():             # was connected then dropped -> JWT likely expired
            refresh_token()
            backoff = 1.0                 # healthy cycle; reset backoff
        else:                             # never got up (TLS/network/auth) -> just back off
            backoff = min(backoff * 2, 60)
        time.sleep(backoff)

def board_state():
    """Slim live snapshot for the board, with an `age` (secs since last MQTT update)."""
    with _state_lock:
        s = dict(_state)
    s["age"] = round(time.time() - s["updated"], 1) if s["updated"] else None
    s.pop("updated", None)
    return s

class Handler(BaseHTTPRequestHandler):
    def _proxy(self, method):
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(n).decode() if n else ""
        code, resp = request(method, self.path, body)
        print(f"  [proxy] {method} {self.path} -> {code} ({len(resp)} bytes)")
        try: status = int(code)
        except ValueError: status = 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp.encode())
    def _board_cmd(self, name):
        # Slim playback commands from the board -> Yoto device commands. We own the
        # deviceId and clamp volume. Body is JSON (may be empty for pause/resume/stop).
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n).decode()) if n else {}
        except Exception:
            body = {}
        cmd, payload = None, None
        if name == "play":
            cmd = "card/start"
            payload = {"uri": "https://yoto.io/" + str(body.get("id", ""))}
            for k in ("chapterKey", "trackKey", "secondsIn"):
                if body.get(k) not in (None, ""):
                    payload[k] = body[k]
        elif name in ("pause", "resume", "stop"):
            cmd = "card/" + name
        elif name == "volume":
            v = max(0, min(VOL_MAX, int(body.get("volume", 30))))
            cmd, payload = "volume/set", {"volume": v}
        if not cmd:
            self.send_response(404); self.end_headers(); return
        path = f"/device-v2/{DEVICE_ID}/command/{cmd}"
        data = json.dumps(payload) if payload is not None else None
        code, resp = request("POST", path, data)
        print(f"  [proxy] /board/cmd/{name} -> {cmd} {payload or ''} -> {code}")
        try: status = int(code)
        except ValueError: status = 502
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(resp.encode())

    def do_GET(self):
        if self.path == "/board/library":
            return self._board_library()
        if self.path == "/board/state":
            out = json.dumps(board_state()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        if self.path.startswith("/board/thumb/"):
            return self._board_thumb(self.path[len("/board/thumb/"):])
        if self.path.startswith("/board/card/"):
            return self._board_card(self.path[len("/board/card/"):])
        self._proxy("GET")

    def _board_card(self, card_id):
        # Slim card detail for the board: {title, chapters:[{k,t,d}]}.
        card_id = card_id.split("?")[0].split("/")[0]
        code, resp = request("GET", "/card/" + card_id, None)
        out = "{}"
        if code == "200":
            try:
                card = (json.loads(resp).get("card")) or {}
                chs = (card.get("content") or {}).get("chapters") or []
                slim = {"title": card.get("title") or card_id,
                        "chapters": [{"k": c.get("key"),
                                      "t": c.get("title") or ("Chapter " + str(i + 1)),
                                      "d": int(c.get("duration") or 0)}
                                     for i, c in enumerate(chs)]}
                out = json.dumps(slim)
            except Exception as e:
                code = "500"; out = json.dumps({"error": str(e)})
        print(f"  [proxy] /board/card/{card_id} -> {code} ({len(out)} bytes)")
        try: status = int(code)
        except ValueError: status = 502
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(out.encode())

    def _board_thumb(self, rest):
        # /board/thumb/{id}[?w=&h=]  — default grid size; bigger covers for detail/now-playing.
        card_id = rest.split("?")[0].split("/")[0]
        w, h = THUMB_W, THUMB_H
        if "?" in rest:
            q = parse_qs(rest.split("?", 1)[1])
            try:
                if "w" in q: w = max(8, min(400, int(q["w"][0])))
                if "h" in q: h = max(8, min(640, int(q["h"][0])))
                elif "w" in q: h = round(w * 1011 / 638)   # keep cover aspect if only w given
            except ValueError:
                pass
        try:
            data = make_thumb(card_id, w, h)
        except Exception as e:
            data = None
            print(f"  [proxy] /board/thumb/{card_id} ERROR {e}")
        if not data:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        print(f"  [proxy] /board/thumb/{card_id} {w}x{h} -> 200 ({len(data)} bytes)")
    def do_POST(self):
        if self.path.startswith("/board/cmd/"):
            return self._board_cmd(self.path[len("/board/cmd/"):].split("?")[0])
        self._proxy("POST")

    def _board_library(self):
        # Slim, board-friendly library: [{id,title,lp}] sorted recently-played first.
        code, resp = request("GET", "/card/family/library", None)
        out = "[]"
        if code == "200":
            try:
                cards = json.loads(resp).get("cards", [])
                slim = [{"id": c.get("cardId"),
                         "title": ((c.get("card") or {}).get("title")) or c.get("cardId"),
                         "lp": c.get("lastPlayedAt") or ""} for c in cards]
                slim.sort(key=lambda x: x["lp"] or "", reverse=True)
                out = json.dumps(slim)
            except Exception as e:
                code = "500"; out = json.dumps({"error": str(e)})
        print(f"  [proxy] /board/library -> {code} ({len(out)} bytes)")
        try: status = int(code)
        except ValueError: status = 502
        self.send_response(status); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(out.encode())
    def log_message(self, *a): pass

def reclaim_port(port):
    """Kill a previous yoto_proxy instance still holding `port`, so re-running this
    script 'just works' instead of dying on 'Address already in use'. (SO_REUSEADDR,
    already set by HTTPServer, only helps with a closed socket — not a live listener.)
    We verify the PID is actually a yoto_proxy before killing, never a random app."""
    try:
        pids = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                              capture_output=True, text=True).stdout.split()
    except FileNotFoundError:
        return                                   # no lsof; let bind fail loudly instead
    killed = False
    for pid in pids:
        if pid == str(os.getpid()):
            continue
        cmd = subprocess.run(["ps", "-p", pid, "-o", "command="],
                             capture_output=True, text=True).stdout
        if "yoto_proxy.py" not in cmd:
            print(f"  [proxy] port {port} held by a NON-proxy process (pid {pid}); "
                  f"refusing to kill it — free it yourself."); sys.exit(1)
        print(f"  [proxy] reclaiming port {port} from stale instance (pid {pid})")
        try:
            os.kill(int(pid), signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            pass
    if killed:
        time.sleep(0.6)                          # let the OS release the socket

if __name__ == "__main__":
    reclaim_port(PORT)
    print(f"Yoto dev proxy listening on http://0.0.0.0:{PORT}  (LAN-reachable; Ctrl-C to stop)")
    print("Forwarding to", API, "with your stored token (auto-refresh on 401).")
    if HAVE_MQTT:
        threading.Thread(target=_mqtt_worker, daemon=True).start()
        print("  [mqtt] live-state thread started -> GET /board/state")
    else:
        print("  [mqtt] paho-mqtt not installed; /board/state will be empty (pip install paho-mqtt)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
