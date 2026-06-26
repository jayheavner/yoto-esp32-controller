#pragma once
// Runtime app settings — NVS-backed, editable without reflashing (via the on-device
// grown-ups screen and the web portal at http://yoto-controller.local/settings).
// secrets.h still provides the compile-time DEFAULTS; NVS overrides once saved, so a
// flashed board can be re-pointed at new WiFi / new Yoto credentials in the field.
// Header-only, included by main.cpp only (single TU), matching yoto_api.h style.
#include <Arduino.h>
#include <Preferences.h>
#include "secrets.h"

namespace cfg {

static Preferences s_prefs;

// ---- connectivity / identity (web portal) ----
static String wifi_ssid;       // 2.4GHz only
static String wifi_pass;
static String client_id;       // Yoto OAuth confidential client
static String client_secret;
static String device_id;       // the Yoto player this panel commands

// ---- parent knobs (on-device settings screen) ----
static int    vol_max   = 60;  // hard volume cap for little ears
static int    saver_min = 10;  // idle minutes before the night-clock screensaver (0 = off)
static int    wake_h    = 7;   // OK-to-wake: clock turns green at this time...
static int    wake_m    = 0;
static int    night_h   = 19;  // bedtime: clock turns red from this time...
static int    night_m   = 0;
static int    saver_size= 2;   // night-clock digit size: 0 small / 1 medium / 2 large
static String tz;              // POSIX TZ for the clock (default US Eastern)

static void load(){
  s_prefs.begin("appcfg", false);
  wifi_ssid     = s_prefs.getString("ssid",  WIFI_SSID);
  wifi_pass     = s_prefs.getString("pass",  WIFI_PASSWORD);
  client_id     = s_prefs.getString("cid",   YOTO_CLIENT_ID);
  client_secret = s_prefs.getString("csec",  YOTO_CLIENT_SECRET);
  device_id     = s_prefs.getString("devid", YOTO_DEVICE_ID);
  tz            = s_prefs.getString("tz",    "EST5EDT,M3.2.0,M11.1.0");
  vol_max       = s_prefs.getInt("volmax", 60);
  saver_min     = s_prefs.getInt("saver",  10);
  wake_h        = s_prefs.getInt("wakeh",  7);
  wake_m        = s_prefs.getInt("wakem",  0);
  night_h       = s_prefs.getInt("nighth", 19);
  night_m       = s_prefs.getInt("nightm", 0);
  saver_size    = s_prefs.getInt("svsize", 2);
}

// Persist everything (NVS only rewrites changed keys, so blanket-saving is cheap).
static void save(){
  s_prefs.putString("ssid",  wifi_ssid);
  s_prefs.putString("pass",  wifi_pass);
  s_prefs.putString("cid",   client_id);
  s_prefs.putString("csec",  client_secret);
  s_prefs.putString("devid", device_id);
  s_prefs.putString("tz",    tz);
  s_prefs.putInt("volmax", vol_max);
  s_prefs.putInt("saver",  saver_min);
  s_prefs.putInt("wakeh",  wake_h);
  s_prefs.putInt("wakem",  wake_m);
  s_prefs.putInt("nighth", night_h);
  s_prefs.putInt("nightm", night_m);
  s_prefs.putInt("svsize", saver_size);
}

} // namespace cfg
