# Yoto Controller — project guide

A wall-mounted **touchscreen remote** so **Kiddo (7.5)** can self-serve play books from his Yoto
library on his bedroom Yoto Player, instead of asking a parent to start cards from the iOS app.
Hardware: **Waveshare ESP32-S3-Touch-LCD-4.3B Rev 1.1** (800×480 landscape touch).

Deeper background lives in the user's memory files (project-yoto-controller, user-context,
reference-yoto-api, reference-board-flashing, reference-ui-design).

## ☀️ MORNING REPORT — overnight progress (built while the user slept; NO playback sent, Kiddo undisturbed)
- **Git** initialized; snapshot + progress committed on `master`. Secrets/venv/images gitignored.
- **Full catalog cached** → `catalog.json` (via `fetch_catalog.py` through the proxy, read-only): all 49
  cards with real chapter titles/durations (402 chapters) + descriptions. App/firmware can run offline.
- **Interactive prototype built + verified → `app.html`.** A working click-through of the whole app on the
  real catalog: My Cards grid → tap a card → detail w/ real chapters → tap a chapter → now-playing (correct
  chapter/title/remaining time). Approved landscape + red design. **Visual only — does NOT command the real
  player.** I verified every screen by screenshot. This is the precise UX spec for the LVGL build.
  - **To view it:** `cd ~/claude/yoto && source .venv/bin/activate && python3 -m http.server 8190`
    then open **http://localhost:8190/app.html** (note: `index.html` is a copy of `app.html`).
- ~~`oauth_login.py` SCOPES now includes `family:device-status:view`~~ **STALE TWICE OVER (final
  correction 2026-06-09 late): live status doesn't need that scope at all — it's MQTT, and MQTT auths
  with `family:devices:control`, which the current grant already has.** See "Live device status: use
  MQTT" in the Yoto API section. (The user confirmed the app registration has ALL scopes enabled; the
  REST status endpoint that wanted the extra scope is deprecated upstream anyway.)
- Kiddo's Yoto was left **paused** (user paused it). Nothing left running.

## Status (2026-06-08): all 3 screens LIVE on the board — full flow works end to end
- ✅ Board: flash custom firmware, run, PSRAM, drive the 800×480 display, GT911 touch — all proven.
- ✅ Yoto: OAuth login (+ refresh token), full 49-card library, chapters, device list, **and live
  playback control on the real device** — all proven end to end.
- ✅ Design: landscape, 3 screens, red palette — **approved**. Reference mockup = `mock.html`.
- ✅ **Firmware — ALL THREE SCREENS WORKING on the board** (`src/main.cpp`), verified on Kiddo's real
  player. Board ↔ local proxy (`yoto_proxy.py`) ↔ Yoto; WiFi creds in gitignored `include/secrets.h`.
  - **My Cards grid** — real 49-card library over WiFi with **cover thumbnails** (proxy serves RGB565;
    `lv_img` tiles, no MCU PNG decode). Tap → detail.
  - **Card detail** — large cover + ▶ Play + scrollable chapter list (yellow badges, titles, durations).
    Tap Play or any chapter row → starts that chapter on the real player + opens now-playing. Back ← returns.
  - **Now playing** — red-gradient screen: cover + title/chapter + progress bar (optimistic 1 Hz local
    tick) + ⏮/⏯/⏭ + volume slider. Controls POST to the proxy: pause/resume/stop, next/prev (re-issues
    `card/start` with the new chapterKey), volume/set (proxy clamps to `VOL_MAX`). ⌄ returns home.
  - Screens are separate LVGL screens; **home persists** so the 49 covers aren't re-downloaded on nav
    (they ARE re-fetched on every boot — persistent thumb caching to flash/SD is a polish item).
- ⬜ **NEXT (finishing touches / polish):** persist thumb cache across boots; live device-state sync
  (via **MQTT** — no new scope/login needed, see "Live device status" in the Yoto API section);
  error states (player offline / WiFi drop / token refresh); productionize the backend off the Mac
  (Pi or Home Assistant).
- ✅ **Scroll + display tuned (2026-06-08); left-edge static flicker SOLVED (2026-06-25):** grid scroll
  smooth & flicker-free, and the residual left-edge static flicker is fixed via `dma_burst_size=64` +
  `bb_invalidate_cache` (bounce-buffer underrun — see the "Display / touch — hard-won" note below). No
  multi-buffer rewrite needed.
- ⬜ Testing with Kiddo (initial live playback test PASSED 2026-06-08).

## Update (2026-06-09): settings + standalone hardening + kid features (FLASHED + partially verified)
- **Runtime config in NVS (`include/app_config.h`, `cfg::`)** — WiFi, Yoto client id/secret, device id,
  TZ, volume cap, screensaver delay, OK-to-wake time. `secrets.h` values are now only FIRST-BOOT seeds;
  saved settings override them, so reflashing is no longer needed to re-point the board.
- **Web settings portal (`include/web_portal.h`)** at `http://yoto-controller.local/settings` (same
  WebServer as OTA): WiFi creds, Yoto creds, **paste-a-refresh-token reseed** (`yoto::set_refresh()` —
  use this to hand the board the NEW scope-bearing grant for live device status, since its NVS token
  chain descends from the old seed), device id, TZ. Uses the OTA password as basic auth when set.
- **Setup hotspot fallback:** WiFi fail at boot no longer dead-ends `setup()` (which also used to skip
  OTA — USB-only recovery). The board raises AP **"Yoto-Setup"** → portal at `192.168.4.1/settings`,
  keeps retrying STA every 30s (`net_retry_cb`), drops the AP once online. OTA + portal ALWAYS start.
- **On-device "Grown-ups" screen** — HOLD the gear (home bar, far right) 2.5s: volume-cap slider,
  **bedtime sleep timer** (15/30/45/60 min → `card/stop`, countdown chip on now-playing), screensaver
  delay, OK-to-wake time, info panel (IP/RSSI/heap/SD/build), restart.
- **Night-clock screensaver + OK-to-wake:** after idle, a dim clock (NTP via `configTzTime`, TZ default
  US Eastern): moon at night (from `night_h` 19:00), sun by day, **green glow + "time to get up" for 2h
  from wake time** — the classic kids' wake clock, free on his wall. Tap → home. montserrat 48 enabled.
- **Quality fixes:** volume slider ranges 0..cap (no more lying 100); commands paint UI optimistically +
  `lv_refr_now` BEFORE the blocking TLS POST (taps feel instant — no more double-tap double-fire);
  progress clock ticks on every screen (was: only on now-playing → stale bar on return) and optimistically
  auto-advances chapters; failed covers get a retry pass; pressed-state feedback on tiles/chapter rows;
  confetti burst when a story starts; **proxy code path REMOVED from firmware** (it had already bit-rotted:
  `s_thumb_diag` broke the `USE_DIRECT_API=0` build — `yoto_proxy.py` stays as a desktop dev probe).
- **Evening session (same day) — flashed + live-verified:**
  - OTA-flashed to the board 3× (base build, then two portal fixes). ⚠️ **espota can't resolve
    `yoto-controller.local`** (its Python resolver fails even when `ping` resolves it) — flash with
    `pio run -e waveshare-s3-43b-ota -t upload --upload-port 192.168.1.x` (board IP, DHCP on <WIFI_SSID>).
  - Verified after each flash via `curl http://192.168.1.x/heap` → `cards:49` (library + TLS healthy;
    free internal ~63KB, boot low-water ~20-24KB) and `/settings` → HTTP 200.
  - **Token reseed PROVEN:** user pasted the refresh token from `.yoto_tokens.json` into the portal;
    after reboot the board redeemed it and loaded the library → **the board now OWNS that token chain**
    (its NVS copy rotates; the Mac's `.yoto_tokens.json` copy is stale — for desktop dev, mint a separate
    grant with a new `oauth_login.py` login; grants coexist). Secret fields in the portal are write-only
    by design (never echoed back; blank = keep) — expected, not a bug.
  - Portal fixes: UTF-8 charset declared (em dash rendered as `â€"` mojibake — 4d16395); timezone is now
    a friendly dropdown (US Eastern/Central/Mountain/Arizona/Pacific/Alaska/Hawaii; POSIX strings are
    option values only; unknown stored value surfaces as selected "Custom (…)" — f599003).
  - ⬜ Still needs hands-on testing: grown-ups screen (gear hold), sleep timer, screensaver colors at
    night/morning + OK-to-wake green, hotspot mode (wrong-SSID test), confetti/press feedback with Kiddo.

## Update (2026-06-09, late): runtime-TLS regression FIXED (chapters/covers/commands all dead)
- **User-reported regressions** (detail screen showed no chapters and no cover; "panel stopped
  showing what's playing") were ONE bug: **every HTTPS call made after boot failed instantly**
  with mbedTLS "SSL - Memory allocation failed". Boot-time calls worked (library loaded,
  cards:49), so it looked like only the detail screen was broken — actually playback commands
  were dead too. Likely shipped in ee62f41/e010485 (WebServer/portal/etc. grew the internal-RAM
  footprint); the detail screen was never hands-on tested after those flashes.
- **Root cause:** mbedTLS needs two ~17KB CONTIGUOUS internal-RAM buffers per connection
  (TLS 1.3 handshake peaks ~60KB total). Once the UI is up, internal RAM is fragmented
  (~62KB free but largest block ~29KB) → the second buffer can't be placed. Diagnosed
  live via the new **`/diag` endpoint** (see below): plainHttp 301 OK ruled out DNS/TCP;
  `WiFiClientSecure.lastError()` named the alloc failure.
- **Fix (the one that works): route mbedTLS allocations ≥4KB to PSRAM** via
  `mbedtls_platform_set_calloc_free(tls_psram_calloc, free)` first thing in `setup()`
  (main.cpp). The prebuilt IDF mbedtls has `MBEDTLS_PLATFORM_MEMORY` enabled, so the runtime
  override links. Small hot allocs stay internal; each side falls back to the other. Verified
  8/8 diag cycles: chapters 200, covers decoded, handshakes ~0.7-1.0s (no real PSRAM penalty),
  minFreeInternal 25KB steady. **A "TLS arena" approach was tried first and is kept DORMANT**
  (`include/tls_arena.h` + inert Guards at call sites): reserving/releasing a contiguous block
  worked only at knife-edge margins. Re-arm with one `tlsarena::reserve()` call if ever needed.
- **Second bug found by the forced-expiry test: token refresh could never recover.** In
  `yoto::get`/`post`, `http.end()` keeps the TLS session for reuse (~33KB held), so the nested
  `refresh()` couldn't allocate its own connection → after the hourly access-token expiry,
  auth 403s stuck forever. Fixed: `cli.stop()` before `refresh()` (yoto_api.h). The hourly
  expiry path is now PROVEN on-device (forced via `/diag?expire=1`, recovered with 200).
- **`/diag` HTTP endpoint** (main.cpp, rides the OTA WebServer): runs the real fetch paths on
  demand and returns JSON — authed `GET /card/{id}` (code/bytes/ms), CDN cover fetch+decode,
  plain-HTTP control, raw TLS connect + mbedtls error string, heap/largest/arena stats.
  `?card=N` picks a grid index, `?expire=1` corrupts the access token first to exercise the
  403→refresh→retry path NOW. Blocks the UI a few seconds — diagnostic, not a feature.
- **"Live status":** the firmware doesn't read device state yet — the home-bar now-playing reflects
  panel-initiated playback, tracked locally. THAT regressed (commands were dead) and is back.
  Showing iOS-app/player-button-initiated playback = the MQTT work (see "Live device status" in the
  Yoto API section — **no new scope or login needed**, available with the token already on the board).
  `/diag?status=1` probes the deprecated REST status endpoint (403 with this grant — don't chase it).

## Update (2026-06-09, night): screensaver-not-firing report — instrumented, NOT reproduced hands-off
- User report: night-clock screensaver doesn't fire after the 5 idle minutes it's set to.
- Added **`/saver` diag endpoint** (rides the OTA WebServer, like `/heap`): live
  `inactiveMs` (LVGL inactivity clock), `saverMin`, `haveHome`/`saverShown` gates,
  `touchSamples` (fresh GT911 PRESSED samples since boot), `lastTouchMs`, `tstate`.
  OTA-flashed ~23:12 and polled hands-off: **setting persisted (saverMin:5) and the saver
  FIRED at 307s uptime** — config, NVS, arming gate, and `saver_open` all healthy from a cold boot.
- Hypothesis for the user's failure: LVGL resets its inactivity clock on EVERY indev sample
  with state PRESSED (lv_indev.c) — a GT911 phantom/stuck press after a REAL touch session
  would hold `inactiveMs` near 0 forever with no visible UI effect. The hands-off test never
  touched the panel, so the post-touch path is unexercised.
- **Overnight poller running on the Mac** (detached `nohup` loop): curls `/saver` every 20s for
  12h → `/tmp/yoto_saver_overnight.log`. NEXT: after the panel gets real use, read the log —
  if `inactiveMs` stays low / `touchSamples` climbs with nobody touching, it's the
  phantom-press theory (fix in `touch_cb`/`gt_poll`); if it climbs to 300000 and
  `saverShown:1`, the saver works post-touch too and the report needs re-observation
  (e.g. an unnoticed touch reset the 5-min window).

## Update (2026-06-09, night): night clock redesigned — giant 7-seg digits, red/green, new knobs
- Per user request: clock digits now fill most of the screen, no words/icons, **red from
  bedtime → wake** ("stay in bed"), **green wake → wake+2h** (OK-to-wake), dim cream otherwise.
- LVGL's biggest built-in font is 48px, so the clock is drawn as **7-segment bars from plain
  lv_objs** (`sv_bar`/`SEG_MAP`/`sv_set_digit` in main.cpp) — scales to any size, zero font
  flash cost. Large = 320px digits (~760px wide total). Off segments are hidden; repaints only
  on minute/color change. NTP-not-synced shows four dashes.
- **New grown-ups knobs:** "Bedtime (clock turns red)" ±15 min (clamped 5–11pm; `cfg::night_h/m`,
  NVS `nightm`) and "Clock size" Small/Medium/Large (200/260/320px; `cfg::saver_size`, NVS
  `svsize`, default Large). Size applies next time the clock comes on.
- **Centering (2026-06-10):** all bars live in one transparent container that slides between
  two precomputed x positions (`s_sv_x4`/`s_sv_x3`) so the VISIBLE time stays centered —
  hours 1-9 hide the leading digit, which used to leave the clock right of center all evening.
- **Geometry verified visually** without the panel via `sevenseg_sim.html` (dev-only HTML that
  mirrors the firmware's exact layout math + segment map; open through the preview server).
  OTA-flashed ~23:30, board healthy (cards:49, saverMin:5). On-device fire of the new saver
  watched via `/saver` after the flash.

## Update (2026-06-25): detail-screen perf + RAM root-cause fix + background library warmer (FLASHED, not yet committed)
- **User report:** big delay between tapping a book and the detail screen appearing; art + chapters
  missing on first open. Two compounding causes found and fixed.
- **ROOT CAUSE (the big one): internal RAM starvation from the concurrent MQTT TLS session.** Since
  MQTT landed in firmware (`include/yoto_mqtt.h`, persistent TLS), internal-RAM low-water fell from the
  old ~63KB-free baseline to a **236-byte** low-water, largest contiguous block ~7.6KB. That starved
  every *on-demand* HTTPS call — chapters, covers, AND playback commands — into intermittent `-5`/`-11`
  failures (diagnosed via `/diag`: raw TLS connect succeeded but the GET failed = contention, not DNS).
  **Fix: `tls_psram_calloc` threshold dropped 4096 → 512** (main.cpp) so the handshake's 1–4KB blocks
  land in PSRAM, not scarce internal RAM. This alone moved largest-internal 7.6KB→17KB, low-water
  236B→18KB, and fetch latency from 9s/timeout → **sub-second**. **Internal RAM is now the tightest
  budget on the board — check `/heap` `minFreeInternal` before adding any more concurrent TLS work.**
- **Perceived-speed + caching fixes (main.cpp, yoto_api.h):**
  - `open_detail` is now **optimistic**: paints the screen instantly (grid thumbnail upscaled via
    `lv_img` zoom as a cover placeholder + any cached chapters), THEN runs the network fetch and patches
    the widgets in place. Same pattern the playback commands already use. No more blocking-before-draw.
  - **Chapters are now cached to SD** (`{id}.chap` binary blob via `sd_chap_read`/`sd_chap_write`), not
    just covers. Repeat detail opens are instant + offline.
  - **Fetch retries:** `yoto::get` retries transport errors (code ≤ 0), not only token expiry; timeout
    cut 15s→8s. `load_cover_cached` retries the CDN fetch 3×. One success caches permanently.
- **Background library warmer ("fetch on load" — user-requested):** `precache_step()` in main.cpp,
  called from `loop()` only when idle (home or screensaver, after a short lull, throttled to 1 fetch /
  2.5s so internal RAM recovers between handshakes). On boot it walks the catalog and fetches only what's
  **missing** from SD — chapter list + `_184` detail cover — keyed on `SD.exists()`, so it warms once and
  re-does work ONLY when a new book appears (its files are simply absent). Idempotent; a warm boot
  finishes the scan in microseconds and latches `s_pc_done`. Protects the playback clock by saving/
  restoring the shared `s_ch` globals around each `fetch_card_detail` (single-threaded — restored before
  the next tick). Verified fully warm: `chap:48` (all non-Daily cards), `cover:97` (49 grid `_122` + 48
  detail `_184`), `pcDone:1`, `minFreeInternal` 13–18KB.
- **New/updated diag endpoints (ride the OTA WebServer):** `GET /sd` (mount status + per-type cache file
  counts + a sample listing); `GET /heap` now also reports `pcCursor`/`pcDone`. `/diag` unchanged (still
  the on-demand fetch probe).
- **Net effect:** first tap on a cold card → screen instant, art + chapters fill in <1s and cache; every
  later tap → fully instant + offline; after a couple idle minutes the WHOLE library is warm, so there's
  no per-card first-tap wait at all. ⬜ Not yet committed (work on `master`); ⬜ hands-on test with Kiddo.

## Hardware / flashing (the historical pain — now solved)
- Single native USB-C = **USB-Serial/JTAG** (no UART bridge, no driver). Enumerates as `/dev/cu.usbmodem*`.
- Build/flash: **PlatformIO Core in `.venv`** (`source .venv/bin/activate`). `pio run -t upload` auto-enters
  download mode — no button dance normally. If a flash bricks USB: unplug → hold **BOOT** → plug in → release.
- Config (see `platformio.ini`): `esp32-s3-devkitc-1`, `memory_type=qio_opi` (16MB flash + 8MB OPI PSRAM),
  `partitions=default_16MB.csv`, USB-CDC console on.
- **Platform = pioarduino `55.03.39` (arduino-esp32 3.3.9 / ESP-IDF 5.5.4)**, NOT the frozen official
  `espressif32`. Needed for the **RGB bounce buffer** (`cfg.bounce_buffer_size_px = LCD_H_RES*10`) that fixes
  display drift/tearing under LVGL load. RGB **pclk must be 16 MHz** (12 MHz → white screen; panel won't lock).
  Libs: `lvgl@8.4`, `ArduinoJson@7`. Build/flash works headless: `pio run -t upload`.
- Known-good firmware = `src/main.cpp` (the full 3-screen app). It has the verified
  RGB panel init (pclk 16MHz, the exact pin map, `pclk_active_neg=1`) and **CH422G** expander sequence
  (backlight=EXIO2, LCD reset=EXIO3, touch reset=EXIO1, via I2C "register=address" writes 0x24/0x38).
- **Serial monitor: run `pio device monitor -b 115200` in a REAL terminal** (then tap RST). The agent's
  headless shell can't (pio monitor needs a TTY; raw pyserial resets the S3). So: agent flashes, user watches.

### Display / touch — hard-won, DON'T re-tread (2026-06-08)
- ✅ **LEFT-EDGE STATIC FLICKER SOLVED (2026-06-25) — it was bounce-buffer DMA underrun, NOT a missing
  multi-buffer.** Symptom: a flickering band in the left ~1/10 (the START of each scanline) on a static
  screen, worse under PSRAM load. Root cause: the bounce-buffer refill (FB lives in PSRAM) loses the bus
  race at line start, so the LCD FIFO underruns and the first pixels go stale. **Fix (two free, zero
  internal-RAM-cost flags in `rgb_panel_init`, both proven on-device): `cfg.dma_burst_size=64` (wider
  PSRAM read bursts keep the refill ahead of the scanline) + `cfg.flags.bb_invalidate_cache=1` (frees
  DCache after each bounce copy → less cache pressure; safe here — FB is only written by the LVGL/loop
  core).** Verified: static band gone AND scroll still rock-solid. Diagnosed with a `/flick?stress=SEC`
  endpoint (main.cpp) that injects on-demand PSRAM bus load so you can watch the edge degrade/recover live
  — keep it, it's lazy + watchdog-safe (yields each pass). **`hsync_back_porch` was bumped 8→30 (closer to
  the panel's reference) during diagnosis but had NO effect — the flicker is temporal underrun, not a
  positional back-porch artifact; don't chase porch values.** 120MHz PSRAM was rejected (octal 120M is
  experimental + temp-dependent crashes — bad for an always-on panel); bigger bounce buffer was rejected
  (internal RAM is the tightest budget, ~15-17KB low-water).
- **Keep `num_fbs=1` (single framebuffer) + bounce buffer.** A double-fb direct-render experiment
  (`num_fbs=2` + `full_refresh` + the panel's own FBs) scrolled smoother BUT introduced a **static-screen
  flicker that scaled with PSRAM use** (worsened as the 49 covers filled PSRAM). Reverted — and with the
  underrun fix above, single-fb is now clean, so the multi-buffer rewrite is no longer needed.
- **LVGL draw buffer goes in INTERNAL RAM, not PSRAM** (`MALLOC_CAP_INTERNAL`, 40 lines = 64KB, PSRAM
  fallback). Rendering into SRAM ~2x faster — this was the big scroll-smoothness win. Bigger = faster but
  must coexist with WiFi's internal-RAM needs.
- **GT911 touch: hold press state between refreshes, release on timeout.** The controller doesn't return
  fresh coords every poll; treating "no new data" as finger-up made scrolling stutter/snap-back. Fix
  (`gt_poll`/`touch_cb`): only update on a data-ready sample, HOLD the last press to bridge gaps, force
  release after 100ms (a stuck press = endless redraw = flicker on the single fb).
- **Grid flex-align: `track place = START`, not CENTER.** Centering the row block hid the top rows once
  content overflowed (couldn't scroll up to them).
- Do NOT block the LVGL loop waiting on VSYNC in `flush_cb` — it starves touch polling → scroll snaps back.

## Yoto API (official, https://yoto.dev) — all confirmed working via the dev proxy
- Auth: confidential client (id + secret). client_id `<YOTO_CLIENT_ID>`; **secret is in
  gitignored `.yoto_token`** (never commit/print). Device-code flow is NOT enabled for this app → we use
  **authorization-code + PKCE** via `python3 oauth_login.py` (browser login). Tokens (access + **refresh**)
  saved to gitignored `.yoto_tokens.json`. Refresh tokens ROTATE — persist the new one each refresh.
  - ⚠️ **An EXPIRED access token returns `403 {"error":{"code":"unauthorized"}}`, NOT 401.** The proxy now
    refreshes on that 403 too (distinguished from a real missing-SCOPE 403, which says "forbidden"/"scope").
    Before this fix, library/playback silently died ~hourly. See `request()`/`_needs_refresh()` in the proxy.
- Endpoints (base `https://api.yotoplay.com`, `Authorization: Bearer <access_token>`):
  - Library: `GET /card/family/library` → `{cards:[...]}` (49). cover=`card.content.cover.imageL`,
    title=`card.title`, plus `lastPlayedAt`. (NOT `/content/mine` — that's a 2-card subset.)
  - Card detail + chapters: `GET /card/{cardId}` → `card.content.chapters[]` (key,title,duration,tracks).
  - Devices: `GET /device-v2/devices/mine`. **Target = "Kiddo's Yoto", deviceId
    `<DEVICE_ID>` (v3e)**. (Also a Mini "Kiddo", offline.)
  - Control (verified, returns `{"status":"ok"}`): `POST /device-v2/{deviceId}/command/{command}`:
    - `volume/set` body `{"volume":0-100}` · `card/start` body `{"uri":"https://yoto.io/{cardId}"}`
      (+ optional `chapterKey`/`trackKey`/`secondsIn`) · `card/pause` · `card/resume` · `card/stop`.
    - No native next/prev/seek — synthesize via `card/start` with recomputed chapter/track.
- ✅ **Live device status: use MQTT — available NOW with the token already on the board** (CORRECTED
  2026-06-09 late; the old "blocked on scope" story was wrong — the user confirmed the app registration
  has ALL scopes enabled, and MQTT doesn't need the extra scope anyway). Per official docs
  (https://yoto.dev/players-mqtt/connecting-to-players/ + /players-mqtt/mqtt-docs/):
  - Broker `wss://aqrphjqbp3u2z-ats.iot.eu-west-2.amazonaws.com/mqtt` (AWS IoT). Auth = the plain
    **access token (JWT) as the MQTT password**, scope **`family:devices:control`** — which our grant
    ALREADY HAS. Username `{deviceId}?x-amz-customauthorizer-name=PublicJWTAuthorizer`, client id
    `DASH{deviceId}`.
  - Subscribe `device/{id}/data/events` (real-time playback/interaction pushes), `device/{id}/data/status`
    (sent only on request → publish to `device/{id}/command/status/request`, ~150ms reply; same for
    `events/request`), `device/{id}/response`. Commands publish to `device/{id}/command/...` (same names
    as REST). Keepalive: publish an events request every ~4m55s or the broker drops idle connections.
  - The REST alternative `GET /device-v2/{id}/status` is **DEPRECATED** upstream and DOES need
    `family:device-status:view` — verified 403 on-device with the current token (`/diag?status=1`).
    Don't chase it; MQTT is the supported path. (If ever wanted: add the scope to `oauth_login.py`
    SCOPES, re-login, reseed the board via `/settings`. The earlier `access_denied` is unexplained —
    possibly pre-dated the dashboard scopes being saved — but moot.)
  - ESP32 path: `esp_mqtt` supports WSS; mbedTLS now allocates from PSRAM (see the TLS fix), so a
    persistent TLS websocket is affordable. Validate topics/payloads from the Mac first if useful.
- **Architecture (DECIDED 2026-06-08): everything runs ON the ESP32 board — no Mac, no Pi, no Home
  Assistant.** A wall appliance can't depend on a laptop that sleeps/reboots. The board holds
  `client_id` + `client_secret` + `refresh_token` in NVS/flash and talks directly to Yoto over HTTPS,
  self-refreshing (port the proxy's `refresh_token()` rotation logic). The `yoto_proxy.py` is DEMOTED to
  a dev probe only (a window into the live API + a desktop place to validate MQTT before porting).
  - ⚠️ The old "secret must NOT live in firmware" rule is STRUCK as security theater for this project.
    Realized risk of a token in flash on a bedroom wall panel: scoped to library-view + device-control
    only (no password/PII/payments), revocable in seconds, and only extractable by someone physically
    in the room with flash-dumping gear. Annoyance-tier, local-only. Not worth a backend.

## Dev tools (in this repo)
- `yoto_proxy.py` — run in a terminal: holds the token, forwards `api.yotoplay.com` on **0.0.0.0:8123**
  (LAN-reachable for the board), auto-refreshes on 401. The agent can `curl http://127.0.0.1:8123/...`
  freely (localhost, no secret in the command, so the sandbox allows it). Window into the live API AND the
  backend. **Slim board endpoints** (keep MCU JSON/decoding tiny):
  - `GET /board/library` → `[{id,title,lp}]` recently-played first.
  - `GET /board/thumb/{id}[?w=]` → raw little-endian **RGB565** cover (default 122×193 grid tile; `?w=184`
    detail, `?w=150` now-playing — h derived from the 638:1011 aspect). Cached to `thumbs/` (gitignored) +
    memory. Needs `Pillow`+`numpy` in `.venv`.
  - `GET /board/card/{id}` → `{title, chapters:[{k,t,d}]}` (slim card detail).
  - `POST /board/cmd/{play|pause|resume|stop|volume}` → forwards to the device (`DEVICE_ID` held here);
    `play` body `{id,chapterKey?}`, `volume` body `{volume}` **clamped to `VOL_MAX`=60**.
- `oauth_login.py` — one-shot browser login → tokens. `gen_mock.py`→`mock.html` (landscape design mockup,
  uses `fam.json`+`card.json`+local `images/`). `gen_mock_portrait.py` = portrait variant (not chosen).
- Preview/screenshot loop: `.claude/launch.json` server "mock" (`python3 -m http.server 8190`, serves
  `index.html` — `cp mock.html index.html`). Restart the preview (or `location.reload()`) to bust its cache
  before screenshotting; `preview_resize` tall to capture all 3 frames.
- ⚠️ Sandbox rule: a single Bash command that BOTH reads a secret file AND hits the network is blocked
  (anti-exfiltration). So token/secret-using API calls are run by the USER's terminal or via the proxy.

## Design (approved — `mock.html` is the source of truth)
- **Landscape 800×480** (native — no rotation). Font: Fredoka (rounded). Palette: **red `#e5352b` hero**
  (Play, now-playing, chapter cues), yellow `#ffb02e` chapter-number badges, green dot = "online", cream
  `#fbf7f0` bg. Covers are uniform **638×1011 PNG**; render as FULL fixed-size tiles (no crop).
- Screen 1 **My Cards**: no title header (Kiddo knows they're his); recently-played first; bottom bar =
  "● Kiddo's Yoto" status + what's playing. Screen 2 **Detail**: split — cover + big Play left, scrollable
  chapter list right (tap a chapter to start there). Screen 3 **Now playing**: cover + chapter-skip /
  play-pause + volume.

## Implementation plan (HISTORICAL — superseded; kept for context)
> Written before the build. The app is now live on the board, the architecture is on-board direct
> (no proxy/backend — step 0 was decided differently), and the scope claim in step 1 is obsolete
> (live status = MQTT, no extra scope; see the Yoto API section). Current next steps live in the
> Status section's ⬜ NEXT line.

**0. Decide where the backend lives (the Phase-3 planning question).** Dev = `yoto_proxy.py` on the Mac.
Production options: a Raspberry Pi, or **Home Assistant** (the user HAS HA — could run the proxy as an
add-on, or use the `cdnninja/yoto_ha` integration and have the board talk to HA). Pick before hardening.

**1. Auth + proxy for the board.** Re-run `python3 oauth_login.py` (now requests `family:device-status:view`)
→ fresh token with live-state access. For the board to reach the proxy, change `yoto_proxy.py` to bind
**`0.0.0.0`** (not 127.0.0.1) and note the Mac's LAN IP; board will hit `http://<lan-ip>:8123`.
Need from user: **2.4GHz WiFi SSID + password** (board is 2.4GHz only).

**2. Offload image work to the backend (recommended).** Covers are 638×1011 PNG — too big to decode 49× on
the MCU. Add a proxy endpoint that returns a **pre-resized thumbnail** (e.g. ~122×193) and ideally raw
RGB565 the panel can blit directly. Saves the firmware from PNG decoding + scaling. (Tile size 122×193;
detail/now-playing covers larger.)

**3. LVGL toolchain.** Add `lvgl` (v8.x) to `platformio.ini` `lib_deps`; add `lv_conf.h`
(`LV_COLOR_DEPTH 16`, draw buffers in PSRAM). Wire LVGL to the **working** display/touch from `src/main.cpp`:
flush callback → `esp_lcd_panel_draw_bitmap`; input read → GT911 poll (already correct, origin top-left).
Get a trivial LVGL screen rendering on the board FIRST (verify with the user) before building real UI.

**4. Build screens (match `app.html` exactly), talking to the proxy not Yoto:**
   - **My Cards** — ✅ DONE (text tiles) via the proxy's slim `GET /board/library` (sorted recently-played
     first). ⬜ NEXT: swap text for **cover thumbnails** — add a proxy `/board/thumb/{id}` endpoint (Pillow
     resize 638×1011 → ~120×190 RGB565) + board-side image load. Bottom bar = "● Kiddo's Yoto" + now-playing.
   - **Detail** — `GET /card/{cardId}` → cover + Play + scrollable chapter list (tap row = start there).
   - **Now playing** — controls POST to proxy: `volume/set`, `card/start` (+chapterKey/trackKey for skip),
     `card/pause`/`resume`/`stop`. Poll `GET /device-v2/{id}/status` (needs the new scope) for live state,
     or use MQTT later.

**5. Polish/robustness:** thumbnail caching to SD/flash, reconnect/refresh-token handling (rotation!),
error states (offline, player offline), and the volume cap for little ears.

Reference: `app.html` is the exact UX; `mock.html` is the static design; `catalog.json` is offline data.
