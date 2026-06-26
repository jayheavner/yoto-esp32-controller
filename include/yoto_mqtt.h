#pragma once
// On-board live device state via Yoto's MQTT feed (AWS IoT, custom JWT authorizer).
// This is the NO-BACKEND port of yoto_proxy.py's MQTT block: the panel holds its own
// persistent websocket to the broker so "now playing" reflects what's ACTUALLY playing on
// Kiddo's Yoto — including playback started from the iOS app or the player's own buttons,
// which the optimistic local clock could never see.
//
// Design:
//  - esp-mqtt over WSS (port 443, path /mqtt). Auth = the plain access-token JWT as the MQTT
//    password (scope family:devices:control, which our grant already has); the AWS IoT custom
//    authorizer name rides in the username. TLS verified against the Mozilla cert bundle.
//    mbedTLS already routes its big buffers to PSRAM (see setup()), so a second persistent TLS
//    socket is affordable.
//  - A supervisor task owns the lifecycle with esp-mqtt's own auto-reconnect DISABLED, so WE
//    rebuild every connection with a freshly-minted JWT. The token expires ~hourly; AWS drops
//    the socket when it does, which is our signal to refresh (mirrors the proxy: refresh ONLY
//    after an up-then-dropped cycle, never on a plain network failure — the refresh token is
//    single-use/rotating and must not be burned on every retry).
//  - Events land in the esp-mqtt task; we copy parsed playback into a mutex-guarded snapshot.
//    The LVGL thread reads the snapshot in tick_cb and drives the UI — all LVGL stays on one
//    thread. The last raw event JSON is kept verbatim for the /mqtt diag endpoint so the wire
//    format can be confirmed on-device instead of guessed.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/event_groups.h>
#include <freertos/task.h>
#include "mqtt_client.h"
#include "esp_crt_bundle.h"
#include "esp_mac.h"
#include "yoto_api.h"

namespace ymqtt {

#define YOTO_MQTT_BROKER "aqrphjqbp3u2z-ats.iot.eu-west-2.amazonaws.com"
static const int HW_VOL_MAX = 16;   // player volume is a raw 0..16 scale; events report it raw

enum Status { ST_UNKNOWN = 0, ST_STOPPED, ST_PAUSED, ST_PLAYING };

// Last-known live playback snapshot. Strings are fixed buffers so the whole struct copies by
// value under the mutex (no heap, no dangling). seq bumps on every applied event so the LVGL
// side can detect "something changed" cheaply.
struct Live {
  char     cardId[48];
  char     chapterKey[16];
  char     chapterTitle[120];
  int      position;       // seconds into the current track
  int      trackLength;    // seconds
  int      volume;         // 0..100 (converted from the raw 0..16 the player reports)
  Status   status;
  bool     online;
  uint32_t seq;            // bumped on each applied update (0 = nothing ever received)
  uint32_t ts;             // millis() of the last update
};

static esp_mqtt_client_handle_t s_cli = nullptr;
static SemaphoreHandle_t   s_mtx = nullptr;
static EventGroupHandle_t  s_eg  = nullptr;
static TaskHandle_t        s_sup_task = nullptr;   // supervisor handle (for stack high-water probe)
static const int           BIT_DOWN = BIT0;     // set by on-disconnect; wakes the supervisor
static Live   s_live = {};
static volatile bool s_conn   = false;          // currently CONNECTED
static volatile bool s_was_up = false;          // got CONNECTED at least once this cycle
static String s_devid, s_user, s_clientid, s_uri, s_pass;   // kept alive for the config
static char   s_rawev[1024] = {0};              // last complete payload, verbatim (for /mqtt)
static char   s_topic[96]   = {0};
static char   s_acc[2048];  static int s_acclen = 0, s_acctotal = 0;   // chunk reassembly
static uint32_t s_evt_count = 0, s_err_count = 0;   // diag counters (32-bit r/w is atomic here)

static void cpystr(char* d, size_t n, const char* s){ if(s) strlcpy(d, s, n); }

// Route ArduinoJson's per-event allocations to PSRAM. Events arrive every couple seconds while
// the player is active; on the internal heap, repeatedly allocating and freeing a parse document
// fragmented the scarce internal RAM (largest free block collapsed toward a few KB) and starved
// LVGL/TLS — the panel went glitchy and touch lagged badly DURING playback. PSRAM is plentiful
// and off the internal-RAM critical path (mbedTLS's big buffers are already routed there too).
struct PsramAllocator : ArduinoJson::Allocator {
  void* allocate(size_t n)            override { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM); }
  void  deallocate(void* p)           override { heap_caps_free(p); }
  void* reallocate(void* p, size_t n) override { return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM); }
};
static PsramAllocator s_json_alloc;

// Parse one data/events (or data/status) payload into the live snapshot. Fields are copied
// only when PRESENT, so a partial event doesn't wipe what we already know. Field names follow
// the proxy / cdnninja-yoto_api spec; verify against /mqtt's raw capture and adjust if needed.
static void parse_event(const char* json, int len){
  JsonDocument doc(&s_json_alloc);
  if(deserializeJson(doc, json, len)){ s_err_count++; return; }
  JsonObjectConst o = doc.as<JsonObjectConst>();
  if(o.isNull()) return;

  xSemaphoreTake(s_mtx, portMAX_DELAY);
  cpystr(s_live.cardId,       sizeof s_live.cardId,       o["cardId"]);
  cpystr(s_live.chapterKey,   sizeof s_live.chapterKey,   o["chapterKey"]);
  cpystr(s_live.chapterTitle, sizeof s_live.chapterTitle, o["chapterTitle"]);
  if(!o["position"].isNull())    s_live.position    = o["position"]    | s_live.position;
  if(!o["trackLength"].isNull()) s_live.trackLength = o["trackLength"] | s_live.trackLength;
  if(!o["volume"].isNull()){
    int raw = o["volume"] | 0;                  // raw 0..16 -> percent; pass through if already 0..100
    s_live.volume = (raw <= HW_VOL_MAX) ? (int)(raw * 100.0 / HW_VOL_MAX + 0.5) : raw;
  }
  const char* ps = o["playbackStatus"];
  if(ps){
    s_live.status = !strcmp(ps, "playing") ? ST_PLAYING
                  : !strcmp(ps, "paused")  ? ST_PAUSED  : ST_STOPPED;
  }
  s_live.online = true;
  s_live.seq++;
  s_live.ts = millis();
  xSemaphoreGive(s_mtx);
}

static void on_event(void*, esp_event_base_t, int32_t id, void* data){
  esp_mqtt_event_handle_t e = (esp_mqtt_event_handle_t)data;
  switch((esp_mqtt_event_id_t)id){
    case MQTT_EVENT_CONNECTED: {
      s_conn = true; s_was_up = true;
      String base = "device/" + s_devid + "/";
      esp_mqtt_client_subscribe(s_cli, (base + "data/events").c_str(), 0);
      esp_mqtt_client_subscribe(s_cli, (base + "data/status").c_str(), 0);
      esp_mqtt_client_subscribe(s_cli, (base + "response").c_str(),    0);
      // nudge the player to push its state now (it won't otherwise)
      esp_mqtt_client_publish(s_cli, (base + "command/events/request").c_str(), "", 0, 0, 0);
      esp_mqtt_client_publish(s_cli, (base + "command/status/request").c_str(), "", 0, 0, 0);
      break;
    }
    case MQTT_EVENT_DISCONNECTED:
      s_conn = false;
      if(s_eg) xEventGroupSetBits(s_eg, BIT_DOWN);
      break;
    case MQTT_EVENT_DATA:
      if(e->current_data_offset == 0){          // first (or only) chunk of a message
        s_acclen = 0; s_acctotal = e->total_data_len;
        if(e->topic && e->topic_len > 0 && e->topic_len < (int)sizeof(s_topic)){
          memcpy(s_topic, e->topic, e->topic_len); s_topic[e->topic_len] = 0;
        }
      }
      if(s_acclen + e->data_len < (int)sizeof(s_acc)){
        memcpy(s_acc + s_acclen, e->data, e->data_len);
        s_acclen += e->data_len; s_acc[s_acclen] = 0;
      }
      if(s_acclen >= s_acctotal && s_acctotal > 0){   // message complete
        s_evt_count++;
        strlcpy(s_rawev, s_acc, sizeof s_rawev);       // verbatim capture for /mqtt
        parse_event(s_acc, s_acclen);
      }
      break;
    case MQTT_EVENT_ERROR:
      s_err_count++;
      break;
    default: break;
  }
}

// Own one connection at a time; rebuild with a fresh JWT after each up-then-dropped cycle.
static void supervisor(void*){
  for(;;){
    if(yoto::access().isEmpty()) yoto::refresh();   // need a token to authenticate the socket
    s_pass = yoto::access();
    if(s_pass.isEmpty()){ vTaskDelay(pdMS_TO_TICKS(5000)); continue; }

    esp_mqtt_client_config_t cfg = {};
    cfg.broker.address.uri               = s_uri.c_str();
    cfg.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
    cfg.credentials.username             = s_user.c_str();
    cfg.credentials.client_id            = s_clientid.c_str();
    cfg.credentials.authentication.password = s_pass.c_str();
    cfg.session.keepalive                = 60;       // PINGREQ keeps the WSS alive under AWS idle
    cfg.network.disable_auto_reconnect   = true;     // WE reconnect, always with a fresh token
    cfg.network.timeout_ms               = 10000;
    cfg.task.stack_size                  = 6144;     // WSS+TLS runs in esp-mqtt's own task (its
                                                     // documented default; big TLS bufs are in PSRAM)

    s_conn = false; s_was_up = false;
    if(s_eg) xEventGroupClearBits(s_eg, BIT_DOWN);
    s_cli = esp_mqtt_client_init(&cfg);
    if(!s_cli){ vTaskDelay(pdMS_TO_TICKS(5000)); continue; }
    esp_mqtt_client_register_event(s_cli, (esp_mqtt_event_id_t)ESP_EVENT_ANY_ID, on_event, nullptr);
    esp_mqtt_client_start(s_cli);

    xEventGroupWaitBits(s_eg, BIT_DOWN, pdTRUE, pdTRUE, portMAX_DELAY);   // block until dropped

    esp_mqtt_client_stop(s_cli);
    esp_mqtt_client_destroy(s_cli);
    s_cli = nullptr;
    {                                                // truth unknown while disconnected
      xSemaphoreTake(s_mtx, portMAX_DELAY); s_live.online = false; xSemaphoreGive(s_mtx);
    }
    if(s_was_up) yoto::refresh();                    // up-then-dropped => JWT likely expired
    vTaskDelay(pdMS_TO_TICKS(s_was_up ? 1000 : 5000));
  }
}

// Start the client. Safe to call once WiFi + a token are available (the supervisor also
// refreshes on its own). A unique client id (MAC-suffixed) avoids AWS kicking off the iOS
// app's own DASH{deviceId} connection.
inline void begin(const String& deviceId){
  if(s_mtx) return;                                  // already started
  s_devid    = deviceId;
  s_user     = "_?x-amz-customauthorizer-name=PublicJWTAuthorizer";
  s_uri      = String("wss://") + YOTO_MQTT_BROKER + ":443/mqtt";
  uint8_t mac[6] = {0}; esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char cid[40];
  snprintf(cid, sizeof cid, "YOTOAPI%02X%02X%02X%02X%02X%02X",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  s_clientid = cid;
  s_mtx = xSemaphoreCreateMutex();
  s_eg  = xEventGroupCreate();
  // Stack must hold an mbedTLS handshake (yoto::refresh on reconnect) + an NVS token write, so it
  // can't live in PSRAM and can't be tiny. Measured on-device via sup_hwm() across a forced
  // reconnect+refresh: peak usage ~4172 bytes (4020 free of 8192). 6144 = that peak + ~2KB margin
  // for TLS handshake variance. (Started at 12288 — nearly 3x the real need.)
  xTaskCreatePinnedToCore(supervisor, "yoto_mqtt", 6144, nullptr, 5, &s_sup_task, 1);
}

inline bool     connected(){ return s_conn; }
// Minimum bytes of supervisor stack ever left free (ESP-IDF reports the high-water mark in bytes).
// Lower = closer to overflow; used to size the stack down to its real peak after a refresh cycle.
inline uint32_t sup_hwm(){ return s_sup_task ? (uint32_t)uxTaskGetStackHighWaterMark(s_sup_task) : 0; }
// Force a disconnect so the supervisor rebuilds the connection AND runs yoto::refresh() — the
// stack-heaviest path. Diagnostic only: it rotates the refresh token, so don't spam it.
inline void kick(){ if(s_cli && s_conn) esp_mqtt_client_disconnect(s_cli); }
inline uint32_t seq(){ return s_live.seq; }
inline uint32_t events(){ return s_evt_count; }
inline uint32_t errors(){ return s_err_count; }
inline const char* last_raw(){ return s_rawev; }
inline const char* last_topic(){ return s_topic; }

// Copy the current snapshot for the LVGL thread.
inline void snapshot(Live& out){
  if(!s_mtx){ out = Live{}; return; }
  xSemaphoreTake(s_mtx, portMAX_DELAY); out = s_live; xSemaphoreGive(s_mtx);
}

// Ask the player to re-push its state (periodic keepalive + manual resync). Thread-safe.
inline void request_state(){
  if(s_cli && s_conn)
    esp_mqtt_client_publish(s_cli, ("device/" + s_devid + "/command/events/request").c_str(), "", 0, 0, 0);
}

} // namespace ymqtt
