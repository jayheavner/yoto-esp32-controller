# Yoto Controller

A wall-mounted **touchscreen remote** that lets a kid self-serve play books from a
[Yoto](https://yoto.dev) library on their bedroom Yoto Player — instead of asking a
parent to start cards from the phone app.

It's a standalone ESP32-S3 appliance: an 800×480 touchscreen showing the child's own
card library with cover art. Tap a book → pick a chapter → it starts playing on the
real Player. No phone, no laptop, no cloud backend — the board talks straight to the
Yoto API over WiFi and self-refreshes its own OAuth token.

| Screen | What it does |
| --- | --- |
| **My Cards** | The full card library as a scrollable grid of cover thumbnails (recently played first). |
| **Card detail** | Large cover, a big ▶ Play, and a scrollable chapter list — tap any chapter to start there. |
| **Now playing** | Cover, title/chapter, progress bar, ⏮ / ⏯ / ⏭, and a volume slider (capped for little ears). |

Plus: a **night-clock screensaver** with OK-to-wake colors (red "stay in bed" → green
"time to get up"), a **bedtime sleep timer**, a hidden **grown-ups settings screen**, and
a **web settings portal** for WiFi/credentials — all configurable without reflashing.

> **Note:** This is a personal/hobby project shared as a reference. It targets one specific
> board and one Yoto account's setup. Identifiers in the sample data files (`devices.json`,
> `fam.json`, etc.) are placeholders — supply your own via `secrets.h` and the settings portal.

---

## Hardware — the board

**[Waveshare ESP32-S3-Touch-LCD-4.3B](https://www.waveshare.com/wiki/ESP32-S3-Touch-LCD-4.3B)** (Rev 1.1).

| | |
| --- | --- |
| **SoC** | ESP32-S3 (dual-core Xtensa LX7) |
| **Display** | 4.3" 800×480 IPS, **RGB565 parallel** panel (native landscape, no rotation) |
| **Touch** | **GT911** capacitive controller (I²C, origin top-left) |
| **Memory** | 16 MB QIO flash + **8 MB OPI PSRAM** |
| **I/O expander** | **CH422G** (I²C) drives backlight + the LCD/touch reset lines |
| **USB** | single native USB-C = **USB-Serial/JTAG** (no UART bridge, no driver) — enumerates as `/dev/cu.usbmodem*` |
| **Power** | 5V via USB-C (wall adapter for always-on use) |

### Hard-won board notes (read before touching the display driver)

These cost real debugging time — they're baked into `src/main.cpp` and worth knowing if you fork:

- **RGB pixel clock must be 16 MHz.** At 12 MHz the panel won't lock → white screen. The init also
  needs `pclk_active_neg = 1` and the exact pin map / back-porch values in `rgb_panel_init`.
- **Single framebuffer (`num_fbs = 1`) + a bounce buffer**, with the framebuffer in PSRAM. A double-FB
  direct-render setup scrolled smoother but flickered as PSRAM filled. Single-FB + bounce buffer is the
  stable choice on this board.
- **Left-edge flicker = bounce-buffer DMA underrun**, not a missing buffer. Fixed with two free flags:
  `cfg.dma_burst_size = 64` (wider PSRAM read bursts keep the refill ahead of the scanline) and
  `cfg.flags.bb_invalidate_cache = 1` (less DCache pressure).
- **LVGL draw buffer lives in *internal* RAM** (`MALLOC_CAP_INTERNAL`), not PSRAM — rendering into SRAM
  is ~2× faster and was the big scroll-smoothness win. Internal RAM is the tightest budget on the board.
- **GT911: hold the press state between polls, release on timeout.** The controller doesn't return fresh
  coords every poll; treating "no new data" as finger-up makes scrolling stutter and snap back.
- **CH422G init sequence matters:** backlight = EXIO2, LCD reset = EXIO3, touch reset = EXIO1, written via
  I²C "register = address" writes to `0x24` / `0x38`.
- **TLS lives in PSRAM.** mbedTLS needs large contiguous internal-RAM buffers per HTTPS connection, which
  fragment once the UI + WiFi are up. `mbedtls_platform_set_calloc_free` routes TLS allocations to PSRAM so
  on-demand fetches and a persistent MQTT socket can coexist. Check `/heap` `minFreeInternal` before adding
  any more concurrent TLS work.

---

## Build & flash

Uses **PlatformIO**. The board config is the **[pioarduino](https://github.com/pioarduino/platform-espressif32)**
fork of the ESP32 platform (needed for the RGB bounce buffer) — pinned in `platformio.ini`, so PlatformIO
fetches it automatically.

```bash
# 1. Provide your secrets (gitignored, never committed)
cp include/secrets.h.example include/secrets.h
$EDITOR include/secrets.h        # WiFi, Yoto client id/secret, device id, OTA password

# 2. Build + flash over USB-C
pio run -t upload                # auto-enters download mode; no button dance normally

# 3. Watch the serial console (run in a real terminal, then tap RST)
pio device monitor -b 115200
```

If a flash ever bricks USB enumeration: unplug → hold **BOOT** → plug in → release, then flash again.

### Wireless updates (OTA)

After one USB flash of OTA-capable firmware, push future builds over WiFi:

```bash
pio run -e waveshare-s3-43b-ota -t upload --upload-port <board-ip>
```

Use the board's IP (shown on its boot screen) — `espota`'s resolver can be flaky with the
`yoto-controller.local` mDNS name even when `ping` resolves it.

---

## Configuration (no reflash needed)

`secrets.h` provides only the **first-boot defaults**. Once the board saves settings they live in NVS
and override the compiled-in values, so a flashed board can be re-pointed in the field:

- **Web settings portal** — `http://yoto-controller.local/settings` (basic-auth = `OTA_PASSWORD`):
  WiFi creds, Yoto client id/secret, target device id, timezone, and a **paste-a-refresh-token** field
  to hand the board a fresh OAuth grant.
- **On-device "Grown-ups" screen** — hold the gear icon (home bar, far right) for 2.5 s:
  volume cap, bedtime sleep timer, screensaver delay, OK-to-wake time, clock size/bedtime, device info,
  and restart.
- **Setup hotspot fallback** — if WiFi fails at boot the board raises an AP (`Yoto-Setup`) with the portal
  at `192.168.4.1/settings`, and keeps retrying the real network in the background.

---

## How it talks to Yoto

Official API, [yoto.dev](https://yoto.dev). The board is a **confidential OAuth client** (authorization-code
+ PKCE) and holds `client_id` + `client_secret` + a rotating `refresh_token` in NVS, self-refreshing hourly.

- **Library:** `GET /card/family/library` → cards (cover, title, last-played).
- **Card detail:** `GET /card/{cardId}` → chapters (key, title, duration, tracks).
- **Devices:** `GET /device-v2/devices/mine`.
- **Control:** `POST /device-v2/{deviceId}/command/{command}` — `card/start` (`uri` + optional
  `chapterKey`/`secondsIn`), `card/pause`, `card/resume`, `card/stop`, `volume/set`. There's no native
  next/prev/seek — those are synthesized by re-issuing `card/start` with a recomputed chapter.
- **Live status:** **MQTT** over WSS to AWS IoT (`device/{id}/data/events`), authed with the same access
  token — so the panel mirrors playback started from *any* source (phone app, the Player's own buttons),
  not just its own taps.

Covers are 638×1011 PNGs; the firmware caches chapter lists and downscaled covers to SD and warms the whole
library in the background while idle, so repeat opens are instant and offline.

---

## Repository layout

```
src/main.cpp            The whole app — display/touch init, the 3 LVGL screens, screensaver, diag endpoints
include/secrets.h.example  Template for the gitignored secrets.h (copy + fill in)
include/app_config.h    NVS-backed runtime settings (cfg::) — secrets.h are the first-boot seeds
include/yoto_api.h       Yoto REST client: OAuth, token refresh/rotation, library/detail/control
include/yoto_mqtt.h      MQTT live-status client (AWS IoT over WSS)
include/web_portal.h     The /settings web portal
include/lv_conf.h        LVGL build config
platformio.ini          Board, platform pin, libs, OTA env
catalog.json / fam.json / card.json / devices.json   Sample API data (placeholder identifiers)
app.html / mock.html    Browser prototypes of the UI (the UX spec the firmware matches)
oauth_login.py          One-shot browser OAuth login → tokens
yoto_proxy.py           Desktop dev probe: forwards the Yoto API + validates MQTT before porting to firmware
```

The HTML prototypes (`app.html`, `mock.html`) are a click-through of the full app on the real catalog —
handy for iterating on the UI design without flashing the board.

---

## Status

Working end to end on the real hardware: all three screens, live playback control, OTA, the settings portal,
the night clock, and MQTT live status. Polish is ongoing.

---

*Built with the help of [Claude Code](https://claude.com/claude-code).*
