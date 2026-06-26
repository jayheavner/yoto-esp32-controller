#pragma once
// Direct-to-Yoto API client for the board — NO proxy/Mac in the loop (the decided
// architecture; yoto_proxy.py remains a desktop dev probe only).
// Holds tokens in NVS (flash), refreshes against login.yotoplay.com, and makes authed
// TLS calls to api.yotoplay.com. Credentials come from cfg:: (NVS-overridable via the
// web portal) with secrets.h as the compile-time default.
// Token-in-flash is an accepted risk for this single-home appliance (scoped to
// library-view + device-control, revocable, only extractable with physical+JTAG access).
#include "secrets.h"
#include "app_config.h"
#include "tls_arena.h"

#include <Arduino.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

namespace yoto {

static const char* AUTH_HOST = "https://login.yotoplay.com";
static const char* API_HOST  = "https://api.yotoplay.com";

static Preferences s_prefs;
static String s_access;        // current access token (JWT)
static String s_refresh;       // current rotating refresh token (single-use; persist on rotation)
// Serializes refresh() across threads. Since the MQTT supervisor task landed, BOTH it and the
// LVGL thread (via get/post expiry) can call refresh(). The refresh token is single-use and
// rotates: two concurrent refreshes spending the SAME token would log us out. This mutex makes
// the second caller wait, then read the freshly-rotated token inside the lock. (A benign race
// remains on plain s_access reads in get/post — at worst a transient bad header that the
// existing 403→refresh retry self-heals; not worth guarding the streaming get() path for.)
static SemaphoreHandle_t s_tok_mtx = nullptr;

// The current access token (JWT) — used by the MQTT client as its broker password.
inline const String& access(){ return s_access; }

// Load tokens from NVS; first boot seeds the refresh token from the compile-time constant,
// after which NVS is the source of truth (so rotation survives reboots).
inline void begin(){
  if(!s_tok_mtx) s_tok_mtx = xSemaphoreCreateMutex();
  s_prefs.begin("yoto", false);
  s_refresh = s_prefs.getString("refresh", "");
  if(s_refresh.isEmpty()){
    s_refresh = YOTO_SEED_REFRESH;
    s_prefs.putString("refresh", s_refresh);
  }
  s_access = s_prefs.getString("access", "");
}

// Reseed the refresh token at runtime (web portal "paste a fresh token" path — e.g. after
// a re-login that added scopes). Clears the cached access token so the next call refreshes.
inline void set_refresh(const String& rt){
  s_refresh = rt; s_prefs.putString("refresh", s_refresh);
  s_access = "";  s_prefs.putString("access", s_access);
}

// A 401, or a 403 whose body says "unauthorized" but NOT "scope", means the access token
// expired (Yoto returns 403 for that, distinct from a real missing-scope 403). Refresh on it.
inline bool isExpired(int code, const String& body){
  if(code == 401) return true;
  return code == 403 && body.indexOf("unauthorized") >= 0 && body.indexOf("scope") < 0;
}

// Exchange the refresh token for a new access token. Persists the rotated refresh token.
// Thread-safe: serialized by s_tok_mtx so the MQTT task and the LVGL thread can't spend the
// same single-use refresh token at once (s_refresh is read INSIDE the lock, so a queued second
// caller picks up the token the first one just rotated to).
inline bool refresh(){
  if(s_tok_mtx) xSemaphoreTake(s_tok_mtx, portMAX_DELAY);
  bool ok = false;
  do {
    tlsarena::Guard tg;          // no-op when nested inside an already-guarded get/post
    WiFiClientSecure cli; cli.setInsecure();
    HTTPClient http;
    if(!http.begin(cli, String(AUTH_HOST) + "/oauth/token")) break;
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");
    http.setTimeout(15000);
    String form = "grant_type=refresh_token";
    form += "&client_id=";     form += cfg::client_id;
    form += "&client_secret="; form += cfg::client_secret;
    form += "&refresh_token="; form += s_refresh;
    int code = http.POST(form);
    String resp = (code > 0) ? http.getString() : "";
    http.end();
    if(code != 200) break;
    JsonDocument doc;
    if(deserializeJson(doc, resp)) break;
    const char* at = doc["access_token"];
    if(!at) break;
    s_access = at; s_prefs.putString("access", s_access);
    const char* rt = doc["refresh_token"];          // rotates — persist the new one or we get logged out
    if(rt && *rt){ s_refresh = rt; s_prefs.putString("refresh", s_refresh); }
    ok = true;
  } while(false);
  if(s_tok_mtx) xSemaphoreGive(s_tok_mtx);
  return ok;
}

// Authed GET. On success (200) leaves `http` OPEN so the caller can stream-parse from
// http.getStream() and must then call http.end(). `cli` must outlive `http`. Auto-refreshes
// once on expiry. Returns the final HTTP code (<=0 on transport error).
// CALLER must hold a tlsarena::Guard across the whole transaction (connect → body read →
// http.end()) — the open response streams from buffers living in the arena hole.
inline int get(WiFiClientSecure& cli, HTTPClient& http, const String& path){
  cli.setInsecure();
  bool refreshed = false;
  // Up to 3 attempts. Transport errors (code<=0: -5 conn lost / -11 timeout) are COMMON under
  // memory pressure now that the MQTT TLS session runs concurrently — a brief backoff + retry
  // almost always lands, and the caller caches the result so one success sticks for good.
  for(int attempt = 0; attempt < 3; attempt++){
    http.begin(cli, String(API_HOST) + path);
    http.addHeader("Authorization", "Bearer " + s_access);
    http.setTimeout(8000);                          // was 15s — keep a UI-blocking call short
    int code = http.GET();
    if(code == 200) return code;                    // caller streams + ends
    String body = (code > 0) ? http.getString() : "";
    http.end();
    cli.stop();   // end() keeps the TLS session for reuse (~33KB!) — drop it BEFORE refresh()
                  // opens its own connection, or refresh can't allocate and auth never recovers
    if(!refreshed && isExpired(code, body) && refresh()){ refreshed = true; continue; }
    if(code <= 0){ delay(350); continue; }          // transport hiccup — back off and retry
    return code;                                    // a real HTTP error (404 etc.) — don't spin
  }
  return -1;
}

// Authed POST with a JSON body; returns the response in `out`. Auto-refreshes once on expiry.
inline int post(const String& path, const String& jsonBody, String& out){
  tlsarena::Guard tg;
  for(int attempt = 0; attempt < 2; attempt++){
    WiFiClientSecure cli; cli.setInsecure();
    HTTPClient http;
    http.begin(cli, String(API_HOST) + path);
    http.addHeader("Authorization", "Bearer " + s_access);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(10000);
    int code = http.POST(jsonBody);
    out = (code > 0) ? http.getString() : "";
    http.end();
    cli.stop();                                     // same keep-alive trap as get() above
    if(attempt == 0 && isExpired(code, out) && refresh()) continue;
    return code;
  }
  return -1;
}

} // namespace yoto
