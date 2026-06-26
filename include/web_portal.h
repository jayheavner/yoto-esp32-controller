#pragma once
// Web settings portal — http://yoto-controller.local/settings (or http://192.168.4.1/settings
// when the board is in Yoto-Setup hotspot mode). Rides the SAME WebServer that ElegantOTA
// uses, so it costs no extra sockets/RAM. Lets you change WiFi + Yoto credentials + timezone
// WITHOUT reflashing: values save to NVS (cfg::), which overrides the secrets.h defaults.
// Pasting a refresh token reseeds yoto:: (e.g. after a re-login that added scopes).
// Plain HTTP on the home LAN; reuses the OTA password as HTTP basic auth when one is set.
// Header-only, included by main.cpp only.
#include <WebServer.h>
#include <WiFi.h>
#include "app_config.h"
#include "yoto_api.h"

namespace portal {

static bool auth_ok(WebServer& w){
  if(sizeof(OTA_PASSWORD) <= 1) return true;          // empty define => open (trusted LAN)
  if(w.authenticate("admin", OTA_PASSWORD)) return true;
  w.requestAuthentication();
  return false;
}

// Minimal HTML-attribute escape so an SSID with quotes can't break the form.
static String esc(const String& s){
  String o; o.reserve(s.length() + 8);
  for(char c : s){
    if(c=='&') o += "&amp;"; else if(c=='<') o += "&lt;";
    else if(c=='>') o += "&gt;"; else if(c=='"') o += "&quot;";
    else o += c;
  }
  return o;
}

static String text_row(const char* label, const char* name, const String& val, const char* hint=nullptr, const char* list=nullptr){
  String h = "<label>" + String(label) + "<input name='" + name + "' value=\"" + esc(val) + "\"";
  if(list){ h += " list='"; h += list; h += "'"; }
  h += ">";
  if(hint){ h += "<small>"; h += hint; h += "</small>"; }
  h += "</label>";
  return h;
}
static String secret_row(const char* label, const char* name, const char* hint){
  return "<label>" + String(label) + "<input name='" + name +
         "' placeholder='(unchanged — leave blank to keep)'><small>" + hint + "</small></label>";
}

// Friendly timezone dropdown. Values are the POSIX TZ rule strings the ESP32's C library
// needs (it has no zoneinfo database), but the user only ever sees city names. If NVS holds
// a string that isn't one of the presets (hand-set custom zone), it appears as a selected
// "Custom" entry so saving the form never silently rewrites it.
static String tz_select(){
  static const struct { const char* label; const char* val; } zones[] = {
    {"US Eastern (New York)",    "EST5EDT,M3.2.0,M11.1.0"},
    {"US Central (Chicago)",     "CST6CDT,M3.2.0,M11.1.0"},
    {"US Mountain (Denver)",     "MST7MDT,M3.2.0,M11.1.0"},
    {"Arizona (no DST)",         "MST7"},
    {"US Pacific (Los Angeles)", "PST8PDT,M3.2.0,M11.1.0"},
    {"Alaska",                   "AKST9AKDT,M3.2.0,M11.1.0"},
    {"Hawaii",                   "HST10"},
  };
  String h = "<label>Timezone<select name='tz'>";
  bool found = false;
  for(auto& z : zones){
    bool sel = (cfg::tz == z.val); found |= sel;
    h += String("<option value=\"") + z.val + "\"" + (sel ? " selected" : "") + ">" + z.label + "</option>";
  }
  if(!found)
    h += "<option value=\"" + esc(cfg::tz) + "\" selected>Custom (" + esc(cfg::tz) + ")</option>";
  h += "</select><small>Sets the night clock and OK-to-wake to local time.</small></label>";
  return h;
}

static void handle_get(WebServer& w){
  if(!auth_ok(w)) return;
  String h =
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>Yoto Controller</title><style>"
    "body{font-family:-apple-system,system-ui,sans-serif;background:#fbf7f0;color:#23303a;max-width:560px;margin:0 auto;padding:18px}"
    "h1{color:#e5352b;font-size:1.5em}h2{font-size:1.05em;margin:1.4em 0 .3em;color:#3a6b50}"
    "label{display:block;margin:.7em 0;font-weight:600;font-size:.92em}"
    "input,select{display:block;width:100%;box-sizing:border-box;padding:9px;margin-top:4px;border:1px solid #d8d0c0;border-radius:9px;font-size:1em;background:#fff}"
    "small{font-weight:400;color:#9a9384}"
    "button{background:#e5352b;color:#fff;border:0;border-radius:11px;padding:12px 22px;font-size:1.05em;font-weight:700;margin-top:14px}"
    "a{color:#e5352b}.info{background:#fff;border-radius:11px;padding:10px 14px;font-size:.9em;color:#666}"
    "</style></head><body><h1>Yoto Controller settings</h1>"
    "<div class=info>IP " + WiFi.localIP().toString() +
    " &middot; WiFi " + String(WiFi.RSSI()) + " dBm"
    " &middot; free RAM " + String(ESP.getFreeHeap()/1024) + " KB"
    " &middot; <a href='/update'>firmware update</a></div>"
    "<form method='POST' action='/settings'>"
    "<h2>WiFi (2.4 GHz only)</h2>" +
    text_row("Network name (SSID)", "ssid", cfg::wifi_ssid) +
    secret_row("Password", "pass", "Saved on the board, used at next boot.") +
    "<h2>Yoto account</h2>" +
    text_row("Client ID", "cid", cfg::client_id) +
    secret_row("Client secret", "csec", "From the Yoto developer dashboard.") +
    secret_row("Refresh token", "rt", "Paste a fresh one (from .yoto_tokens.json after oauth_login.py) to re-seed — e.g. after a re-login that added scopes.") +
    text_row("Device ID", "devid", cfg::device_id, "The player this panel controls (GET /device-v2/devices/mine).") +
    "<h2>Clock</h2>" +
    tz_select() +
    "<button type='submit'>Save</button></form>"
    "<p><small>WiFi and credential changes take effect after a reboot: "
    "<a href='/reboot'>reboot now</a>.</small></p>"
    "</body></html>";
  w.send(200, "text/html; charset=utf-8", h);
}

static void handle_post(WebServer& w){
  if(!auth_ok(w)) return;
  // Blank secret fields mean "keep what's stored"; visible fields update when non-empty.
  String v;
  v = w.arg("ssid");  if(v.length()) cfg::wifi_ssid     = v;
  v = w.arg("pass");  if(v.length()) cfg::wifi_pass     = v;
  v = w.arg("cid");   if(v.length()) cfg::client_id     = v;
  v = w.arg("csec");  if(v.length()) cfg::client_secret = v;
  v = w.arg("devid"); if(v.length()) cfg::device_id     = v;
  v = w.arg("tz");    if(v.length()) cfg::tz            = v;
  cfg::save();
  v = w.arg("rt");
  if(v.length()) yoto::set_refresh(v);                  // also clears the cached access token
  w.send(200, "text/html; charset=utf-8",
    "<!doctype html><body style='font-family:system-ui;background:#fbf7f0;padding:24px'>"
    "<h2 style='color:#3a6b50'>Saved.</h2>"
    "<p>WiFi / credential changes apply at next boot.</p>"
    "<p><a href='/reboot'>Reboot now</a> &middot; <a href='/settings'>Back to settings</a></p></body>");
}

static void handle_reboot(WebServer& w){
  if(!auth_ok(w)) return;
  w.send(200, "text/html; charset=utf-8",
    "<!doctype html><body style='font-family:system-ui;background:#fbf7f0;padding:24px'>"
    "<h2>Rebooting&hellip;</h2><p>Give it ~15 seconds, then <a href='/settings'>reload</a>.</p></body>");
  delay(300);                                           // let the response flush
  ESP.restart();
}

static void begin(WebServer& w){
  w.on("/settings", HTTP_GET,  [&w](){ handle_get(w);    });
  w.on("/settings", HTTP_POST, [&w](){ handle_post(w);   });
  w.on("/reboot",   HTTP_GET,  [&w](){ handle_reboot(w); });
}

} // namespace portal
