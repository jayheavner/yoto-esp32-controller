// Standalone Yoto wall controller: the board talks TLS straight to api.yotoplay.com
// (token refresh in NVS — no Mac/proxy in the loop). Cover art comes from Yoto's image
// CDN pre-resized and is decoded (PNG/JPEG) to RGB565 on the MCU, cached to MicroSD.
// Runtime config (WiFi, Yoto credentials, parent knobs) lives in NVS: editable from the
// on-device "grown-ups" screen (hold the gear) and http://yoto-controller.local/settings;
// if WiFi can't connect the board raises a "Yoto-Setup" hotspot with the same portal.
// On-screen status labels make boot/network debuggable without serial.
// Waveshare ESP32-S3-Touch-LCD-4.3B · arduino-esp32 3.3.9 · LVGL 8.4 · ArduinoJson 7.

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoOTA.h>   // OTA path 1: push firmware from PlatformIO over WiFi
#include <WebServer.h>    // sync web server backing ElegantOTA (no async libs needed)
#include <ElegantOTA.h>   // OTA path 2: browser uploader at http://<board>/update
#include <ArduinoJson.h>
#include <lvgl.h>
#include <time.h>          // NTP clock for the screensaver / OK-to-wake
#include "esp_lcd_panel_rgb.h"
#include "esp_lcd_panel_ops.h"
#include "esp_heap_caps.h"
#include "FS.h"            // SD thumbnail cache (FS/SD/SPI ship with arduino-esp32 core)
#include "SD.h"
#include "SPI.h"
#include "secrets.h"
#include "app_config.h"   // NVS-backed runtime settings (cfg::)
#include "yoto_api.h"     // direct-to-Yoto client
#include "yoto_mqtt.h"    // on-board MQTT live-state feed (reflects what's REALLY playing)
#include "web_portal.h"   // /settings web page (WiFi + credentials without reflashing)
#include <pngle.h>        // PNG decoder for CDN cover art (book covers)
#include <JPEGDEC.h>      // JPEG decoder (podcast covers are square JPEGs, not PNG)
#include <mbedtls/platform.h>  // runtime allocator override (TLS buffers → PSRAM, see setup)

// mbedTLS allocator: route all but the tiniest blocks to PSRAM, where 8MB sits mostly idle.
// Internal RAM is critically scarce here (the concurrent MQTT TLS session + LVGL draw buffer +
// WiFi leave a low-water of only a few hundred bytes), so a 4KB threshold still piled the
// handshake's 1–4KB blocks into internal RAM and starved on-demand fetches into -5/-11 failures.
// Dropped to 512B: only the hottest sub-512B bignum temporaries stay internal for speed; the
// rest go to PSRAM. Each side falls back to the other, so an exhausted pool degrades into a
// slow handshake, never a failed one. Installed first thing in setup().
static void* tls_psram_calloc(size_t n, size_t size){
  size_t total = n * size;
  uint32_t pref = (total >= 512) ? MALLOC_CAP_SPIRAM : MALLOC_CAP_INTERNAL;
  void* p = heap_caps_calloc(n, size, pref);
  if(!p) p = heap_caps_calloc(n, size, pref == MALLOC_CAP_SPIRAM ? MALLOC_CAP_INTERNAL : MALLOC_CAP_SPIRAM);
  return p;
}

#define PIN_I2C_SDA 8
#define PIN_I2C_SCL 9
#define PIN_TP_INT  4
#define LCD_H_RES   800
#define LCD_V_RES   480

// ---------------- CH422G expander ----------------
static constexpr uint8_t CH422G_WR_SET = 0x24;
static constexpr uint8_t CH422G_WR_IO  = 0x38;
static constexpr uint8_t EXIO_TP_RST  = 1;
static constexpr uint8_t EXIO_BL_DISP = 2;
static constexpr uint8_t EXIO_LCD_RST = 3;
static constexpr uint8_t EXIO_SD_CS   = 4;   // CH422G EXIO4 = MicroSD chip-select (active low)
static uint8_t s_io_shadow = 0;
static uint32_t s_bl_recoveries = 0;   // times the self-heal watchdog re-inited a reset expander
static void ch422g_write(uint8_t a, uint8_t v){ Wire.beginTransmission(a); Wire.write(v); Wire.endTransmission(); }
static void ch422g_flush(){ ch422g_write(CH422G_WR_IO, s_io_shadow); }
static void ch422g_set(uint8_t bit, bool hi){ if(hi) s_io_shadow|=(1u<<bit); else s_io_shadow&=~(1u<<bit); ch422g_flush(); }

// ---------------- MicroSD (SPI) — persistent cover-thumbnail cache ----------------
// The 4.3B's TF slot is SPI on GPIO12/13/11, BUT SD_CS hangs off the CH422G (EXIO4), which the
// Arduino SD library can't toggle per byte over I2C. Since the card is the ONLY device on this
// bus we just HOLD EXIO4 low (card permanently selected — valid SPI-mode behaviour) and hand
// SD.begin an unused GPIO as a nominal CS it can wiggle harmlessly. Mounting is best-effort:
// on any failure s_sd_ok stays false and every cover falls back to a live CDN fetch exactly as
// before — the SD is purely a cache, never a dependency.
#define SD_PIN_SCK      12
#define SD_PIN_MISO     13
#define SD_PIN_MOSI     11
#define SD_PIN_CS_DUMMY 6        // unconnected GPIO; the REAL CS is EXIO4, held low below
#define SD_DIR          "/ythumbs"
static bool     s_sd_ok = false;
static SPIClass s_sdspi(HSPI);

static bool sd_begin(){
  ch422g_set(EXIO_SD_CS, false);                 // assert SD_CS via the expander (select the card)
  s_sdspi.begin(SD_PIN_SCK, SD_PIN_MISO, SD_PIN_MOSI, SD_PIN_CS_DUMMY);
  if(!SD.begin(SD_PIN_CS_DUMMY, s_sdspi, 20000000)) return false;
  if(SD.cardType() == CARD_NONE) return false;
  if(!SD.exists(SD_DIR)) SD.mkdir(SD_DIR);
  s_sd_ok = true;
  return true;
}

// One raw little-endian RGB565 blob per (cardId, width) — same bytes the panel blits.
static String sd_cache_path(const String& id, int W){ return String(SD_DIR) + "/" + id + "_" + W + ".565"; }

static bool sd_cache_read(const String& id, int W, uint8_t* buf, size_t want){
  if(!s_sd_ok) return false;
  File f = SD.open(sd_cache_path(id, W), FILE_READ);
  if(!f) return false;
  bool ok = ((size_t)f.size() == want) && (f.read(buf, want) == (int)want);
  f.close();
  return ok;
}
static void sd_cache_write(const String& id, int W, const uint8_t* buf, size_t want){
  if(!s_sd_ok) return;
  File f = SD.open(sd_cache_path(id, W), FILE_WRITE);   // "w" => truncate+write a fresh blob
  if(!f) return;
  f.write(buf, want);
  f.close();
}

// One small binary blob per card holding the slim chapter list, so the detail screen opens
// instantly (and offline) on repeat visits instead of re-fetching /card/{id} over TLS every
// tap. Format: u16 magic, title(len16+bytes), u16 count, then per chapter {i32 dur, key, title}.
static String sd_chap_path(const String& id){ return String(SD_DIR) + "/" + id + ".chap"; }
static const uint16_t CHAP_MAGIC = 0xC401;
static void sd_wr_str(File& f, const String& s){
  uint16_t n = (uint16_t)s.length();
  f.write((const uint8_t*)&n, 2);
  if(n) f.write((const uint8_t*)s.c_str(), n);
}
static bool sd_rd_str(File& f, String& out){
  uint16_t n;
  if(f.read((uint8_t*)&n, 2) != 2) return false;
  out = "";
  if(n){
    char* b = (char*)malloc(n + 1);
    if(!b) return false;
    if(f.read((uint8_t*)b, n) != (int)n){ free(b); return false; }
    b[n] = 0; out = b; free(b);
  }
  return true;
}

// ---------------- GT911 touch ----------------
static uint8_t gt_addr = 0x5D;
static bool gt_read(uint16_t reg, uint8_t* buf, size_t len){
  Wire.beginTransmission(gt_addr); Wire.write((uint8_t)(reg>>8)); Wire.write((uint8_t)(reg&0xFF));
  if(Wire.endTransmission(false)!=0) return false;
  size_t n=Wire.requestFrom((int)gt_addr,(int)len);
  for(size_t i=0;i<len && Wire.available();++i) buf[i]=Wire.read();
  return n==len;
}
static bool gt_write(uint16_t reg, uint8_t val){
  Wire.beginTransmission(gt_addr); Wire.write((uint8_t)(reg>>8)); Wire.write((uint8_t)(reg&0xFF)); Wire.write(val);
  return Wire.endTransmission()==0;
}
static bool gt_init(){
  pinMode(PIN_TP_INT, OUTPUT); digitalWrite(PIN_TP_INT, LOW);
  ch422g_set(EXIO_TP_RST,false); delay(12);
  ch422g_set(EXIO_TP_RST,true);  delay(12);
  digitalWrite(PIN_TP_INT,LOW);  delay(60);
  pinMode(PIN_TP_INT, INPUT);
  for(uint8_t a : {(uint8_t)0x5D,(uint8_t)0x14}){ gt_addr=a; uint8_t id[4]={0}; if(gt_read(0x8140,id,4)) return true; }
  return false;
}
// returns: -1 error · -2 no NEW data ready (caller must HOLD last state, NOT release) ·
//          0 finger lifted · >0 pressed (x,y updated). Treating "no new data" as a lift
//          is what made scrolling stutter — the GT911 doesn't refresh every poll.
static int gt_poll(int& x, int& y){
  uint8_t st=0; if(!gt_read(0x814E,&st,1)) return -1;
  if(!(st&0x80)) return -2;
  int n=st&0x0F;
  if(n>0){ uint8_t p[8]; if(gt_read(0x8150,p,8)){ x=p[0]|(p[1]<<8); y=p[2]|(p[3]<<8); } }
  gt_write(0x814E,0);
  return n;
}

// ---------------- RGB panel ----------------
// Single framebuffer + bounce buffer = the proven rock-solid config. (A double-fb
// direct-render experiment scrolled smoother but introduced a static-screen flicker that
// scaled with PSRAM use, so we reverted it. Smoother+tear-free is a future anti-tearing
// pass that needs serial-monitored iteration.)
static esp_lcd_panel_handle_t s_panel=nullptr;
static int rgb_panel_init(){
  esp_lcd_rgb_panel_config_t cfg={};
  cfg.clk_src=LCD_CLK_SRC_DEFAULT;
  cfg.timings.pclk_hz=16*1000*1000; cfg.timings.h_res=LCD_H_RES; cfg.timings.v_res=LCD_V_RES;
  // hsync_back_porch was 8 — far below this panel's reference (ESPHome 30, Arduino_GFX 88) at the
  // same 16MHz pclk. Too small a back porch starts clocking active pixels before horizontal retrace
  // finishes, so the first ~N px of every line render garbage => a persistent vertical band at the
  // LEFT edge on a static screen (the reported symptom). Raised 8 -> 30 to give the panel retrace time.
  cfg.timings.hsync_pulse_width=4; cfg.timings.hsync_back_porch=30; cfg.timings.hsync_front_porch=8;
  cfg.timings.vsync_pulse_width=4; cfg.timings.vsync_back_porch=8; cfg.timings.vsync_front_porch=8;
  cfg.timings.flags.pclk_active_neg=1;
  cfg.data_width=16; cfg.bits_per_pixel=16; cfg.num_fbs=1;
  cfg.bounce_buffer_size_px=LCD_H_RES*10;          // 8000px bounce buffer -> fixes the PSRAM-contention drift
  cfg.dma_burst_size=64;                            // bigger PSRAM read bursts -> bounce refill stays ahead of
                                                    // the scanline (combats the left-edge underrun flicker)
  cfg.flags.bb_invalidate_cache=1;                  // free DCache after each bounce copy -> less cache pressure;
                                                    // safe while content is static (no concurrent FB writes)
  cfg.hsync_gpio_num=46; cfg.vsync_gpio_num=3; cfg.de_gpio_num=5; cfg.pclk_gpio_num=7; cfg.disp_gpio_num=-1;
  const int dp[16]={14,38,18,17,10, 39,0,45,48,47,21, 1,2,42,41,40};
  for(int i=0;i<16;++i) cfg.data_gpio_nums[i]=dp[i];
  cfg.flags.fb_in_psram=1;
  if(esp_lcd_new_rgb_panel(&cfg,&s_panel)!=ESP_OK) return 1;
  if(esp_lcd_panel_reset(s_panel)!=ESP_OK) return 2;
  if(esp_lcd_panel_init(s_panel)!=ESP_OK) return 3;
  return 0;
}

// ---------------- LVGL glue ----------------
// Draw buffer in FAST internal RAM (not PSRAM): LVGL renders into SRAM ~2x faster, which
// is the main lever for scroll framerate. Falls back to PSRAM if internal alloc fails.
// Was 40 lines (64KB), but linking in the OTA stack (WebServer+ElegantOTA+ArduinoOTA) ate
// enough internal RAM that the Yoto TLS handshake (~40-50KB) could no longer allocate at
// boot -> "Library HTTP -1  heap=60160". 24 lines (38KB) frees ~26KB and restores the
// handshake headroom while keeping a buffer >2x the LVGL default. Tune up if scroll suffers
// AND free heap allows (watch the heap= readout on the library-error path).
#define DRAW_LINES 24
static lv_disp_draw_buf_t s_draw_buf;
static lv_color_t* s_buf1=nullptr;
static void flush_cb(lv_disp_drv_t* drv, const lv_area_t* area, lv_color_t* px){
  esp_lcd_panel_draw_bitmap(s_panel, area->x1, area->y1, area->x2+1, area->y2+1, px);
  lv_disp_flush_ready(drv);
}
static int s_lx=0, s_ly=0;
static lv_indev_state_t s_tstate=LV_INDEV_STATE_REL;
static uint32_t s_tlast=0;                 // millis() of the last fresh touch sample
static uint32_t s_touch_samples=0;         // total fresh PRESSED samples (diag: phantom-touch counter)
static void touch_cb(lv_indev_drv_t* drv, lv_indev_data_t* data){
  int x=-1,y=-1; int n=gt_poll(x,y);
  if(n>0 && x>=0 && y>=0 && x<LCD_H_RES && y<LCD_V_RES){ s_lx=x; s_ly=y; s_tstate=LV_INDEV_STATE_PR; s_tlast=millis(); s_touch_samples++; }
  else if(n==0) s_tstate=LV_INDEV_STATE_REL;             // genuine lift
  else {
    // n==-2 (no new data) / n==-1 (error): HOLD to bridge GT911 refresh gaps so scrolling
    // doesn't stutter — but force release after 100ms so a missing lift event can't stick
    // pressed (a stuck press keeps the screen redrawing -> flicker on the single fb).
    if(s_tstate==LV_INDEV_STATE_PR && millis()-s_tlast > 100) s_tstate=LV_INDEV_STATE_REL;
  }
  data->state=s_tstate;
  data->point.x=s_lx; data->point.y=s_ly;
}

// ---------------- display-flicker diagnostic: on-demand PSRAM bus load ----------------
// Hypothesis: the static-screen flicker confined to the LEFT ~1/10 (the START of each scanline)
// is RGB-DMA underrun — the bounce buffer can't refill the line in time because the framebuffer
// (in PSRAM) is losing the bus to other PSRAM traffic (mbedTLS bufs, MQTT JSON, covers). This
// task streams a 2MB read+write across PSRAM on demand so we can watch the left edge degrade and
// recover LIVE (curl /flick?stress=SECONDS) — proving or refuting the cause with NO reflash.
// Pinned to core 0 (off LVGL's core 1) so any change is attributable to BUS contention, not CPU
// starvation of the UI. Idle cost is zero until /flick?stress is hit (task is created lazily).
static volatile uint32_t s_flick_until = 0;       // millis() deadline; load runs while now < this
static TaskHandle_t      s_flick_task  = nullptr;
static void flick_stress_task(void*){
  const size_t SZ = 2*1024*1024;                  // >> the PSRAM cache, so every pass really hits the bus
  uint8_t* buf = (uint8_t*)heap_caps_malloc(SZ, MALLOC_CAP_SPIRAM);
  for(;;){
    if(buf && (int32_t)(s_flick_until - millis()) > 0){
      memcpy(buf, buf + SZ/2, SZ/2);              // 1MB read + 1MB write per pass = heavy PSRAM bus load
      vTaskDelay(1);                              // yield 1 tick each pass so core-0 IDLE/WDT runs
                                                  // (a no-yield 20s hog tripped the task watchdog -> reset)
    } else {
      vTaskDelay(pdMS_TO_TICKS(20));              // idle: yield, generate nothing
    }
  }
}

// ---------------- app state ----------------
#define THUMB_W   160
#define THUMB_H   254                       // 160 * 1011/638 — 4-up grid, ~19mm tiles (bigger tap target)
#define THUMB_SZ  (THUMB_W*THUMB_H*2)      // RGB565 bytes per cover (81280); ~4.0MB PSRAM for 49 covers
struct Card {
  String id; String title;
  String lp;                               // lastPlayedAt (direct-API path sorts on it)
  String cover;                            // cover imageL URL (direct-API: fetch+decode from CDN)
  bool   daily=false;                      // synthetic "Yoto Daily" tile — one-tap, plays today
  lv_obj_t* tile=nullptr;                  // the lv_img tile in the grid
  uint8_t*  thumb=nullptr;                 // PSRAM RGB565 buffer (persists for image lifetime)
  lv_img_dsc_t dsc{};                       // descriptor LVGL renders from
};

// Yoto Daily isn't a library card — it's the player's "Button Play" card (3nC80), whose
// "daily" chapter holds one track per day (trackKey = YYYYMMDD). We inject a tile for it and
// play TODAY's track directly, mirroring the player's physical button.
static const char* YOTO_DAILY_ID    = "3nC80";
static const char* YOTO_DAILY_COVER  =
  "https://card-content.yotoplay.com/yoto/pub/jC1GVU5Iwhl0yo5Lpcsn_OtXvp-5q2H2KwGAqeZR9ds";
static Card s_cards[64];
static int  s_card_count=0;
static lv_obj_t* s_status=nullptr;
static lv_obj_t* s_barlabel=nullptr;        // idle-state hint / cover-load progress (left side)
static lv_obj_t* s_bar=nullptr;             // home bottom bar — holds the mini now-playing

// screens (home persists so the 49 covers stay loaded; detail is rebuilt per card)
static lv_obj_t* s_scr_home=nullptr;
static lv_obj_t* s_scr_detail=nullptr;
static int s_cur_card=-1;                    // card being BROWSED (detail/now screens read this)

// card detail (chapters) — fetched on demand from /board/card/{id}
#define DCOVER_W  210
#define DCOVER_H  333                       // 210 * 1011/638, keeps cover aspect
#define DCOVER_SZ (DCOVER_W*DCOVER_H*2)
#define MAX_CH    40
struct Chapter { String key; String title; int dur; };
static Chapter   s_ch[MAX_CH];
static int       s_ch_count=0;
static String    s_detail_title;
static uint8_t*  s_dcover=nullptr;          // reusable PSRAM buffer for the detail cover
static lv_img_dsc_t s_dcover_dsc{};

// now-playing screen + optimistic playback state (no live device poll yet — needs the
// family:device-status:view scope; we track state locally like the app.html prototype)
#define NCOVER_W  170
#define NCOVER_H  270                       // 170 * 1011/638
#define NCOVER_SZ (NCOVER_W*NCOVER_H*2)
static lv_obj_t* s_scr_now=nullptr;
static int  s_cur_ch=-1;
static bool s_playing=false;
static int  s_pos=0;                        // optimistic seconds into the current chapter
static int  s_vol=30;

// playback IDENTITY — the card/chapter actually playing on the device, tracked separately
// from s_cur_card (which follows browsing). Drives the home mini-player + reopening now-playing
// after the user has browsed to other cards. -1 = nothing has been started this session.
static int    s_play_card=-1;
static int    s_play_ch=-1;
static String s_play_title;
static String s_play_chapter;
static uint8_t* s_ncover=nullptr;
static lv_img_dsc_t s_ncover_dsc{};
static lv_obj_t* s_np_barfill=nullptr;      // live now-playing widgets (null when off-screen)
static lv_obj_t* s_np_tcur=nullptr;
static lv_obj_t* s_np_trem=nullptr;
static lv_obj_t* s_np_playicon=nullptr;
static lv_obj_t* s_np_chapter=nullptr;
static lv_obj_t* s_np_sleep=nullptr;        // "Zz 23m" sleep-timer chip on the now screen
static int  s_play_dur=0;                   // duration (s) of the chapter actually playing —
                                            // lets the optimistic clock tick on ANY screen

// bedtime sleep timer: wall-clock seconds until we auto-stop the player (0 = off)
static int  s_sleep_sec=0;

// grown-ups settings screen + night-clock screensaver
static lv_obj_t* s_scr_settings=nullptr;
static lv_obj_t* s_set_sleep_lbl=nullptr;   // live countdown inside settings (null when closed)
static lv_timer_t* s_gear_timer=nullptr;    // hold-the-gear-to-open timer
static lv_obj_t* s_scr_saver=nullptr;
static lv_obj_t* s_sv_seg[4][7];            // big 7-segment clock: 4 digits x 7 bars
static lv_obj_t* s_sv_colon[2];
static lv_obj_t* s_sv_cont=nullptr;         // holds all the bars; slides to keep the time centered
static int      s_sv_x4=0, s_sv_x3=0;       // container x for 4 visible cells vs 3 (hour 1-9)
static int      s_sv_last4=-1;              // last layout used (-1 = unset)
static int      s_sv_lastmin=-999;          // last painted hh*60+mm (-999 = force repaint)
static uint32_t s_sv_lastcol=0;             // last painted color
static bool s_lib_ok=false;                 // library fetched + grid built
static bool s_net_ready=false;              // NTP/token init done after WiFi came up

static void status(const char* msg){
  if(!s_status){
    s_status=lv_label_create(lv_scr_act());
    lv_obj_set_style_text_font(s_status, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(s_status, lv_color_hex(0x23303A), 0);
    lv_obj_align(s_status, LV_ALIGN_CENTER, 0, 0);
  }
  lv_label_set_text(s_status, msg);
  lv_refr_now(lv_disp_get_default());      // paint immediately so we see progress
}

static void open_detail(int i);          // fwd
static void play_from(int chapterIdx);   // fwd
static void open_now();                  // fwd
static void np_toggle();                 // fwd
static void np_volume(int v);            // fwd
static void update_home_bar();           // fwd
static bool fetch_card_detail(int i);    // fwd
static bool sd_chap_read(const String& id);  // fwd (SD chapter cache read)
static void play_yoto_daily(int idx);    // fwd
static void open_settings();             // fwd
static void saver_open();                // fwd
static void saver_close();               // fwd
static void tile_cb(lv_event_t* e){
  int i=(int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
  if(i<0 || i>=s_card_count) return;
  if(s_cards[i].daily) play_yoto_daily(i);   // one tap → today's episode (no chapter list)
  else                 open_detail(i);
}

static lv_obj_t* s_grid=nullptr;

static void build_grid(){
  lv_obj_clean(lv_scr_act());
  s_status=nullptr;
  lv_obj_t* scr=lv_scr_act();
  s_scr_home=scr;
  lv_obj_set_style_bg_color(scr, lv_color_hex(0xFBF7F0), 0);

  // scrollable grid container
  lv_obj_t* cont=lv_obj_create(scr);
  s_grid=cont;
  lv_obj_set_size(cont, LCD_H_RES, 410);
  lv_obj_align(cont, LV_ALIGN_TOP_MID, 0, 0);
  lv_obj_set_style_bg_color(cont, lv_color_hex(0xFBF7F0), 0);
  lv_obj_set_style_border_width(cont, 0, 0);
  // 4 columns of 160px tiles: 4*160 + 3*20 gap = 700; centered in the 800-wide row with ~32px
  // side margins. Bigger tiles than the old 5-up 122px grid = an easier target for little fingers.
  lv_obj_set_style_pad_all(cont, 18, 0);
  lv_obj_set_style_pad_row(cont, 20, 0);
  lv_obj_set_style_pad_column(cont, 20, 0);
  lv_obj_set_flex_flow(cont, LV_FLEX_FLOW_ROW_WRAP);
  // main=CENTER (center tiles in each row), but track place = START so rows stack from the
  // TOP — centering the track block hides the top rows once content overflows (unscrollable).
  lv_obj_set_flex_align(cont, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
  lv_obj_set_scroll_dir(cont, LV_DIR_VER);

  for(int i=0;i<s_card_count;i++){
    // image tile (cover fills it once the thumb loads); red rounded placeholder until then
    lv_obj_t* tile=lv_img_create(cont);
    lv_obj_set_size(tile, THUMB_W, THUMB_H);
    lv_obj_set_style_bg_color(tile, lv_color_hex(0xE5352B), 0);
    lv_obj_set_style_bg_opa(tile, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(tile, 14, 0);
    lv_obj_set_style_clip_corner(tile, true, 0);
    lv_obj_add_flag(tile, LV_OBJ_FLAG_CLICKABLE);
    // tactile press feedback: the tile dips + dims while a finger is on it, so every
    // touch visibly reacts even before the (blocking) detail fetch starts
    lv_obj_set_style_translate_y(tile, 3, LV_STATE_PRESSED);
    lv_obj_set_style_opa(tile, LV_OPA_80, LV_STATE_PRESSED);
    lv_obj_set_user_data(tile, (void*)(intptr_t)i);
    lv_obj_add_event_cb(tile, tile_cb, LV_EVENT_CLICKED, NULL);
    s_cards[i].tile=tile;
  }

  // bottom status bar (mini now-playing) — populated by update_home_bar()
  s_bar=lv_obj_create(scr);
  lv_obj_set_size(s_bar, LCD_H_RES, 70);
  lv_obj_align(s_bar, LV_ALIGN_BOTTOM_MID, 0, 0);
  lv_obj_set_style_bg_color(s_bar, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_border_width(s_bar, 0, 0);
  lv_obj_set_style_radius(s_bar, 0, 0);
  lv_obj_set_style_pad_all(s_bar, 0, 0);
  lv_obj_clear_flag(s_bar, LV_OBJ_FLAG_SCROLLABLE);
  update_home_bar();
}

// Reopen the now-playing screen for the card that's actually PLAYING (which may differ from
// the card currently browsed). Re-fetch its chapters if we've since browsed elsewhere.
static void minibar_open_cb(lv_event_t*){
  if(s_play_card < 0 || s_play_card >= s_card_count) return;
  if(s_cards[s_play_card].daily){
    // Yoto Daily has no normal chapter list — rebuild the one-track state we play it with,
    // from the preserved playback identity (don't refetch, which would load the wrong chapters).
    s_cur_card = s_play_card; s_detail_title = "Yoto Daily";
    s_ch_count = 1; s_cur_ch = 0;
    s_ch[0].key = "daily"; s_ch[0].title = s_play_chapter; s_ch[0].dur = s_play_dur;
    open_now();
    return;
  }
  if(s_cur_card != s_play_card){
    s_cur_card = s_play_card;
    // repopulate s_ch + s_detail_title for the now screen — SD cache first, only hit the
    // network if this card's chapters somehow aren't cached yet (matches open_detail/warmer)
    if(!sd_chap_read(s_cards[s_play_card].id)) fetch_card_detail(s_play_card);
  }
  s_cur_ch = s_play_ch;
  open_now();
}
static void minibar_toggle_cb(lv_event_t*){ np_toggle(); }   // np_toggle() refreshes the bar

// settings gear: a deliberate 2.5s HOLD opens the grown-ups screen — a quick kid-tap does
// nothing. The pending timer is cancelled the moment the finger lifts or slides off.
static void gear_open_cb(lv_timer_t*){ s_gear_timer=nullptr; open_settings(); }
static void gear_event_cb(lv_event_t* e){
  lv_event_code_t c = lv_event_get_code(e);
  if(c==LV_EVENT_PRESSED && !s_gear_timer){
    s_gear_timer = lv_timer_create(gear_open_cb, 2500, NULL);
    lv_timer_set_repeat_count(s_gear_timer, 1);     // one-shot; self-deletes after firing
  } else if((c==LV_EVENT_RELEASED || c==LV_EVENT_PRESS_LOST) && s_gear_timer){
    lv_timer_del(s_gear_timer); s_gear_timer=nullptr;
  }
}

// Rebuild the home bottom bar from current state: left = device status; right = either the
// idle hint, or a mini now-playing (title + chapter, play/pause, tap-to-open).
static void update_home_bar(){
  if(!s_bar) return;
  // the gear is about to be deleted+rebuilt — if a finger was mid-hold on it, the deleted
  // button can't deliver RELEASED, so disarm the pending open here
  if(s_gear_timer){ lv_timer_del(s_gear_timer); s_gear_timer=nullptr; }
  lv_obj_clean(s_bar);

  // left: green "online" dot + device name
  lv_obj_t* dot=lv_obj_create(s_bar);
  lv_obj_set_size(dot, 12, 12);
  lv_obj_set_style_radius(dot, 6, 0);
  lv_obj_set_style_bg_color(dot, lv_color_hex(0x3AB06A), 0);
  lv_obj_set_style_border_width(dot, 0, 0);
  lv_obj_clear_flag(dot, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_align(dot, LV_ALIGN_LEFT_MID, 16, 0);

  lv_obj_t* name=lv_label_create(s_bar);
  lv_label_set_text(name, "Kiddo's Yoto");
  lv_obj_set_style_text_font(name, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(name, lv_color_hex(0x3A6B50), 0);
  lv_obj_align(name, LV_ALIGN_LEFT_MID, 36, 0);

  // far right: settings gear — subtle grey, HOLD 2.5s to open (gear_event_cb). Text color
  // set on the button (pressed state turns it red) and inherited by the icon label.
  lv_obj_t* gear=lv_btn_create(s_bar);
  lv_obj_set_size(gear, 52, 52);
  lv_obj_align(gear, LV_ALIGN_RIGHT_MID, -8, 0);
  lv_obj_set_style_bg_opa(gear, LV_OPA_TRANSP, 0);
  lv_obj_set_style_shadow_width(gear, 0, 0);
  lv_obj_set_style_text_color(gear, lv_color_hex(0xC9C2B4), 0);
  lv_obj_set_style_text_color(gear, lv_color_hex(0xE5352B), LV_STATE_PRESSED);
  lv_obj_add_event_cb(gear, gear_event_cb, LV_EVENT_ALL, NULL);
  lv_obj_t* gi=lv_label_create(gear);
  lv_label_set_text(gi, LV_SYMBOL_SETTINGS);
  lv_obj_set_style_text_font(gi, &lv_font_montserrat_28, 0);
  lv_obj_center(gi);

  if(s_play_card < 0){
    // idle — keep the hint label (load_thumbs also writes cover-load progress here)
    s_barlabel=lv_label_create(s_bar);
    lv_obj_set_style_text_font(s_barlabel, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(s_barlabel, lv_color_hex(0x9A9384), 0);
    lv_label_set_text(s_barlabel, "");   // idle: left dot + "Kiddo's Yoto" is enough
    lv_obj_align(s_barlabel, LV_ALIGN_LEFT_MID, 188, 0);
    return;
  }
  s_barlabel=nullptr;   // mini-player owns the right side while something is playing

  // play/pause button (right of the tap block, left of the gear) — quick toggle from the grid
  lv_obj_t* pp=lv_btn_create(s_bar);
  lv_obj_set_size(pp, 54, 54);
  lv_obj_align(pp, LV_ALIGN_RIGHT_MID, -68, 0);
  lv_obj_set_style_radius(pp, 27, 0);
  lv_obj_set_style_bg_color(pp, lv_color_hex(0xE5352B), 0);
  lv_obj_set_style_shadow_width(pp, 0, 0);
  lv_obj_add_event_cb(pp, minibar_toggle_cb, LV_EVENT_CLICKED, NULL);
  lv_obj_t* ppi=lv_label_create(pp);
  lv_label_set_text(ppi, s_playing ? LV_SYMBOL_PAUSE : LV_SYMBOL_PLAY);
  lv_obj_set_style_text_font(ppi, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(ppi, lv_color_hex(0xFFFFFF), 0);
  lv_obj_center(ppi);

  // tappable title/chapter block → opens the full Now Playing screen
  lv_obj_t* tap=lv_obj_create(s_bar);
  lv_obj_set_size(tap, 440, 64);
  lv_obj_align(tap, LV_ALIGN_RIGHT_MID, -132, 0);
  lv_obj_set_style_bg_opa(tap, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(tap, 0, 0);
  lv_obj_set_style_pad_all(tap, 0, 0);
  lv_obj_clear_flag(tap, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_flag(tap, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(tap, minibar_open_cb, LV_EVENT_CLICKED, NULL);

  lv_obj_t* tt=lv_label_create(tap);
  lv_label_set_long_mode(tt, LV_LABEL_LONG_DOT);
  lv_obj_set_width(tt, 440);
  lv_label_set_text_fmt(tt, LV_SYMBOL_AUDIO "  %s", s_play_title.c_str());
  lv_obj_set_style_text_font(tt, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(tt, lv_color_hex(0x23303A), 0);
  lv_obj_align(tt, LV_ALIGN_LEFT_MID, 0, -11);

  lv_obj_t* tc=lv_label_create(tap);
  lv_label_set_long_mode(tc, LV_LABEL_LONG_DOT);
  lv_obj_set_width(tc, 440);
  lv_label_set_text(tc, s_play_chapter.c_str());
  lv_obj_set_style_text_font(tc, &lv_font_montserrat_16, 0);
  lv_obj_set_style_text_color(tc, lv_color_hex(0x9A9384), 0);
  lv_obj_align(tc, LV_ALIGN_LEFT_MID, 28, 14);
}

// Keep big JSON work OUT of scarce internal RAM (which the TLS handshake + LVGL draw buffer
// fight over) by routing it to the idle 8MB PSRAM. Used for the raw response body AND the
// parsed DOM. This is what lets the draw buffer grow back toward smooth scroll.
struct PsramAllocator : ArduinoJson::Allocator {
  void* allocate(size_t n) override          { return heap_caps_malloc(n, MALLOC_CAP_SPIRAM); }
  void  deallocate(void* p) override          { free(p); }
  void* reallocate(void* p, size_t n) override{ return heap_caps_realloc(p, n, MALLOC_CAP_SPIRAM); }
};

// Read the full body of an OPEN HTTPClient response into a NUL-terminated PSRAM buffer
// (caller frees). Returns nullptr on alloc failure. Buffering the whole body is required
// because stream-parsing from the TLS socket trips ArduinoJson on partial arrival; doing it
// in PSRAM (vs getString()'s internal-RAM String) is the headroom win.
static char* read_body_psram(HTTPClient& http, size_t& outLen){
  outLen = 0;
  int len = http.getSize();
  WiFiClient* stream = http.getStreamPtr();
  size_t cap = (len > 0) ? (size_t)len + 1 : 16384;
  char* buf = (char*)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
  if(!buf) return nullptr;
  size_t got = 0; uint32_t t0 = millis();
  while(len < 0 || got < (size_t)len){
    size_t avail = stream->available();
    if(avail){
      if(got + avail + 1 > cap){
        size_t ncap = cap * 2;
        char* n = (char*)heap_caps_realloc(buf, ncap, MALLOC_CAP_SPIRAM);
        if(!n) break;
        buf = n; cap = ncap;
      }
      int n = stream->readBytes(buf + got, min(avail, cap - 1 - got));
      if(n > 0){ got += n; t0 = millis(); }
    } else if(!http.connected()){ break; }
    else if(millis() - t0 > 15000){ break; }
    else delay(1);
  }
  buf[got] = '\0';
  outLen = got;
  return buf;
}

static bool fetch_library(){
  // GET /card/family/library straight from Yoto. The payload is large, so we parse it
  // through a filter (only cardId/title/lastPlayedAt/cover survive) and sort
  // recently-played-first on the board.
  // Buffer the FULL body in PSRAM (not an internal-RAM String), THEN parse — streaming
  // straight from the secure socket trips ArduinoJson into IncompleteInput when the stream
  // briefly has no byte ready. The filter + PSRAM-backed DOM keep internal RAM untouched.
  // The arena guard closes BEFORE the parse: anything the parse allocates in internal RAM
  // (the Card Strings) must not land inside the arena hole or the reclaim degrades.
  size_t blen = 0;
  char* body = nullptr;
  {
    tlsarena::Guard tg;                     // connect → stream → end, nothing else
    WiFiClientSecure cli; HTTPClient http;
    int code = yoto::get(cli, http, "/card/family/library");
    if(code != 200){ status((String("Library HTTP ") + code + "  heap=" + ESP.getFreeHeap()).c_str()); if(code > 0) http.end(); return false; }
    body = read_body_psram(http, blen);
    http.end();
  }
  if(!body){ status("Library buf alloc fail"); return false; }
  JsonDocument filter;
  filter["cards"][0]["cardId"]                       = true;
  filter["cards"][0]["lastPlayedAt"]                 = true;
  filter["cards"][0]["card"]["title"]                = true;
  filter["cards"][0]["card"]["content"]["cover"]["imageL"] = true;
  PsramAllocator psram;
  JsonDocument doc(&psram);
  // const char* => ArduinoJson DUPLICATES strings into the (PSRAM) DOM, so freeing body is safe.
  DeserializationError err = deserializeJson(doc, (const char*)body, blen, DeserializationOption::Filter(filter));
  free(body);
  if(err){ status((String("JSON error: ") + err.c_str() + " (" + blen + "B)").c_str()); return false; }
  s_card_count = 0;
  for(JsonObject c : doc["cards"].as<JsonArray>()){
    if(s_card_count >= 64) break;
    s_cards[s_card_count].id    = (const char*)(c["cardId"] | "");
    s_cards[s_card_count].title = (const char*)(c["card"]["title"] | "");
    s_cards[s_card_count].lp    = (const char*)(c["lastPlayedAt"] | "");
    s_cards[s_card_count].cover = (const char*)(c["card"]["content"]["cover"]["imageL"] | "");
    s_card_count++;
  }
  // recently-played first — ISO-8601 timestamps sort lexically; "" (never played) sinks last
  for(int a=0;a<s_card_count;a++)
    for(int b=a+1;b<s_card_count;b++)
      if(s_cards[b].lp > s_cards[a].lp){ Card t=s_cards[a]; s_cards[a]=s_cards[b]; s_cards[b]=t; }
  // inject the "Yoto Daily" tile at the front (it's not in the library — see YOTO_DAILY_ID)
  if(s_card_count < 64){
    for(int k=s_card_count; k>0; k--) s_cards[k] = s_cards[k-1];
    s_cards[0] = Card{};
    s_cards[0].id = YOTO_DAILY_ID; s_cards[0].title = "Yoto Daily";
    s_cards[0].cover = YOTO_DAILY_COVER; s_cards[0].daily = true;
    s_card_count++;
  }
  return s_card_count > 0;
}

// ---- cover art: fetch a CDN-resized PNG (no auth) + decode to RGB565 on the MCU ----
// Yoto's image CDN resizes server-side via ?width=&quality= (only PNG out), so we ask for the
// exact tile size (~5-20KB) and stream it through pngle straight into the panel's pixel buffer.
// Decode target: a W×H RGB565 tile. xoff/yoff center a smaller (e.g. square podcast) image
// so it sits letterboxed in the tile instead of filling from the corner.
struct DecCtx { uint8_t* buf; int W; int H; int xoff; int yoff; };
static String s_thumb_diag;     // why the last cover decode failed (surfaced on-screen, no serial)
static bool   s_png_done;       // set by pngle's done-callback when IEND is reached

static inline void put_px(DecCtx* c, int px, int py, uint16_t v){
  if(px < 0 || px >= c->W || py < 0 || py >= c->H) return;
  size_t idx = ((size_t)py * c->W + px) * 2;
  c->buf[idx] = v & 0xFF; c->buf[idx+1] = v >> 8;     // little-endian RGB565
}
static void png_on_init(pngle_t* p, uint32_t iw, uint32_t ih){
  DecCtx* c = (DecCtx*)pngle_get_user_data(p);
  c->xoff = ((int)c->W - (int)iw) / 2; if(c->xoff < 0) c->xoff = 0;
  c->yoff = ((int)c->H - (int)ih) / 2; if(c->yoff < 0) c->yoff = 0;
}
static void png_on_draw(pngle_t* p, uint32_t x, uint32_t y, uint32_t w, uint32_t h, const uint8_t rgba[4]){
  DecCtx* c = (DecCtx*)pngle_get_user_data(p);
  uint16_t v = ((rgba[0] & 0xF8) << 8) | ((rgba[1] & 0xFC) << 3) | (rgba[2] >> 3);
  for(uint32_t yy=0; yy<h; yy++)
    for(uint32_t xx=0; xx<w; xx++)
      put_px(c, c->xoff + (int)(x+xx), c->yoff + (int)(y+yy), v);
}
static void png_on_done(pngle_t*){ s_png_done = true; }
static int jpg_draw(JPEGDRAW* d){
  DecCtx* c = (DecCtx*)d->pUser;
  uint16_t* px = (uint16_t*)d->pPixels;        // RGB565 little-endian (set below)
  for(int yy=0; yy<d->iHeight; yy++)
    for(int xx=0; xx<d->iWidth; xx++)
      put_px(c, c->xoff + d->x + xx, c->yoff + d->y + yy, px[yy*d->iWidth + xx]);
  return 1;
}

// Fetch coverBase resized to width W and decode into buf (W*H*2 RGB565). Handles BOTH formats
// Yoto serves: tall PNG book covers AND square JPEG podcast covers (sniffed by magic bytes).
static bool fetch_cover(const String& coverBase, int W, int H, uint8_t* buf){
  String base = coverBase;
  int q = base.indexOf('?'); if(q >= 0) base = base.substring(0, q);   // drop any existing query
  if(base.length() == 0){ s_thumb_diag = "no cover url"; return false; }
  String url = base + "?width=" + W + "&quality=70";
  uint8_t* img = nullptr;
  size_t got = 0;
  {
    tlsarena::Guard tg;                     // CDN fetch is TLS too; closed before the decode
    WiFiClientSecure cli; cli.setInsecure();
    HTTPClient http;
    if(!http.begin(cli, url)){ s_thumb_diag = "begin fail heap=" + String(ESP.getFreeHeap()); return false; }
    http.setTimeout(8000);
    int code = http.GET();
    if(code != 200){ s_thumb_diag = "HTTP " + String(code) + " heap=" + String(ESP.getFreeHeap()); http.end(); return false; }
    int len = http.getSize();
    WiFiClient* stream = http.getStreamPtr();
    // Buffer the whole (small, ~5-20KB) image, then dispatch by format — JPEG isn't streamable
    // the way pngle is, and buffering keeps both decoders simple.
    size_t cap = (len > 0) ? (size_t)len : 49152;
    img = (uint8_t*)heap_caps_malloc(cap, MALLOC_CAP_SPIRAM);
    if(!img){ s_thumb_diag = "img malloc fail heap=" + String(ESP.getFreeHeap()); http.end(); return false; }
    uint32_t t0 = millis();
    while(len < 0 || got < (size_t)len){
      size_t avail = stream->available();
      if(avail){
        if(got + avail > cap){                // grow only for unknown-length (chunked) responses
          size_t ncap = cap * 2; uint8_t* n = (uint8_t*)heap_caps_realloc(img, ncap, MALLOC_CAP_SPIRAM);
          if(!n) break; img = n; cap = ncap;
        }
        int n = stream->readBytes(img + got, min(avail, cap - got));
        got += n; t0 = millis();
      } else if(!http.connected()){ break; }
      else if(millis()-t0 > 15000){ break; }
      else delay(1);
    }
    http.end();
  }
  if(got < 8){ s_thumb_diag = "short (" + String(got) + "B)"; free(img); return false; }

  memset(buf, 0, (size_t)W * H * 2);          // black letterbox behind non-filling covers
  DecCtx ctx{ buf, W, H, 0, 0 };
  bool ok = false;
  if(img[0]==0x89 && img[1]=='P'){            // ---- PNG ----
    pngle_t* p = pngle_new();
    if(!p){ s_thumb_diag = "pngle null heap=" + String(ESP.getFreeHeap()); free(img); return false; }
    pngle_set_user_data(p, &ctx);
    pngle_set_init_callback(p, png_on_init);
    pngle_set_draw_callback(p, png_on_draw);
    pngle_set_done_callback(p, png_on_done);
    s_png_done = false;
    if(pngle_feed(p, img, got) < 0) s_thumb_diag = String("png: ") + pngle_error(p);
    ok = s_png_done;
    pngle_destroy(p);
  } else if(img[0]==0xFF && img[1]==0xD8){    // ---- JPEG (square podcast covers) ----
    static JPEGDEC jpg;
    if(jpg.openRAM(img, got, jpg_draw)){
      jpg.setPixelType(RGB565_LITTLE_ENDIAN);
      int iw = jpg.getWidth(), ih = jpg.getHeight();
      ctx.xoff = (W - iw) / 2; if(ctx.xoff < 0) ctx.xoff = 0;
      ctx.yoff = (H - ih) / 2; if(ctx.yoff < 0) ctx.yoff = 0;
      jpg.setUserPointer(&ctx);
      ok = (jpg.decode(0, 0, 0) == 1);
      jpg.close();
      if(!ok) s_thumb_diag = "jpg decode fail";
    } else s_thumb_diag = "jpg open fail";
  } else {
    s_thumb_diag = "unknown fmt " + String(img[0], HEX) + String(img[1], HEX);
  }
  free(img);
  return ok;
}

// Load a W×H RGB565 cover into buf: SD cache first (instant, no WiFi/decode), else fetch from
// the CDN and write-through to SD so the next boot is offline-fast. With no SD mounted this
// is exactly the plain fetch path. Keyed by (cardId,width) so grid/detail/now-playing each
// get their own cached size.
static bool load_cover_cached(const String& id, const String& coverUrl, int W, int H, size_t sz, uint8_t* buf){
  if(sd_cache_read(id, W, buf, sz)) return true;
  // Retry the CDN fetch a few times — like the API GET, the TLS handshake is flaky under the
  // memory pressure of the concurrent MQTT session, and one success caches the cover for good.
  bool ok = false;
  for(int a = 0; a < 3 && !ok; a++){
    ok = fetch_cover(coverUrl, W, H, buf);
    if(!ok) delay(350);
  }
  if(ok) sd_cache_write(id, W, buf, sz);
  return ok;
}

static void set_img(lv_img_dsc_t& dsc, lv_obj_t* obj, uint8_t* data, int w, int h, size_t sz){
  dsc.header.cf = LV_IMG_CF_TRUE_COLOR;
  dsc.header.always_zero = 0;
  dsc.header.w = w; dsc.header.h = h;
  dsc.data_size = sz; dsc.data = data;
  lv_img_set_src(obj, &dsc);
}

// Stream one cover thumbnail into a PSRAM buffer and blit it onto the grid tile.
static bool load_thumb(int i){
  Card& c = s_cards[i];
  if(!c.thumb){
    c.thumb = (uint8_t*)heap_caps_malloc(THUMB_SZ, MALLOC_CAP_SPIRAM);
    if(!c.thumb) return false;
  }
  if(!load_cover_cached(c.id, c.cover, THUMB_W, THUMB_H, THUMB_SZ, c.thumb)) return false;
  set_img(c.dsc, c.tile, c.thumb, THUMB_W, THUMB_H, THUMB_SZ);
  return true;
}

static void load_thumbs(){
  int ok=0; String firstErr;
  for(int i=0;i<s_card_count;i++){
    if(load_thumb(i)) ok++;
    else if(firstErr.length()==0){ firstErr = s_thumb_diag; }
    if(s_barlabel)
      lv_label_set_text_fmt(s_barlabel, "loading covers  %d/%d", i+1, s_card_count);
    lv_refr_now(lv_disp_get_default());     // show each cover as it arrives
  }
  // one retry pass for stragglers (transient CDN/WiFi hiccups) before reporting failure;
  // a failed tile is the one whose descriptor never got pixels
  for(int i=0;i<s_card_count && ok<s_card_count;i++){
    if(!s_cards[i].dsc.data && load_thumb(i)){ ok++; lv_refr_now(lv_disp_get_default()); }
  }
  if(s_barlabel){
    if(ok < s_card_count)
      lv_label_set_text_fmt(s_barlabel, "covers %d/%d  -  %s", ok, s_card_count, firstErr.c_str());
    else
      lv_label_set_text(s_barlabel, "");   // covers done: clear the loading progress, leave bar clean
  }
}

// ---------------- detail screen ----------------
static void fmt_mmss(char* b, int s){ if(s<0)s=0; sprintf(b, "%d:%02d", s/60, s%60); }
static void fmt_total(char* b, int s){ int h=s/3600, m=(s%3600)/60; if(h) sprintf(b,"%dh %dm",h,m); else sprintf(b,"%dm",m); }

// Persist the freshly-parsed chapter list (in the s_ch globals) for this card to SD.
static void sd_chap_write(const String& id){
  if(!s_sd_ok) return;
  File f = SD.open(sd_chap_path(id), FILE_WRITE);
  if(!f) return;
  f.write((const uint8_t*)&CHAP_MAGIC, 2);
  sd_wr_str(f, s_detail_title);
  uint16_t n = (uint16_t)s_ch_count;
  f.write((const uint8_t*)&n, 2);
  for(int k=0;k<s_ch_count;k++){
    int32_t d = s_ch[k].dur;
    f.write((const uint8_t*)&d, 4);
    sd_wr_str(f, s_ch[k].key);
    sd_wr_str(f, s_ch[k].title);
  }
  f.close();
}

// Load this card's chapter list from SD into the s_ch globals. Returns false on any miss/short
// read so the caller falls back to a live fetch exactly as before — SD is a cache, not a dep.
static bool sd_chap_read(const String& id){
  if(!s_sd_ok) return false;
  File f = SD.open(sd_chap_path(id), FILE_READ);
  if(!f) return false;
  bool ok = false;
  do {
    uint16_t magic;
    if(f.read((uint8_t*)&magic, 2) != 2 || magic != CHAP_MAGIC) break;
    if(!sd_rd_str(f, s_detail_title)) break;
    uint16_t n;
    if(f.read((uint8_t*)&n, 2) != 2) break;
    if(n > MAX_CH) n = MAX_CH;
    int k = 0;
    for(; k < n; k++){
      int32_t d;
      if(f.read((uint8_t*)&d, 4) != 4) break;
      s_ch[k].dur = d;
      if(!sd_rd_str(f, s_ch[k].key))   break;
      if(!sd_rd_str(f, s_ch[k].title)) break;
    }
    if(k == n){ s_ch_count = n; ok = true; }
  } while(0);
  f.close();
  return ok;
}

static bool fetch_card_detail(int i){
  Card& c = s_cards[i];
  // GET /card/{id}; filter to title + chapters[].{key,title,duration}.
  size_t blen = 0;
  char* body = nullptr;
  {
    tlsarena::Guard tg;                     // closed before the parse — see fetch_library
    WiFiClientSecure cli; HTTPClient http;
    int code = yoto::get(cli, http, "/card/" + c.id);
    if(code != 200){ if(code > 0) http.end(); return false; }
    body = read_body_psram(http, blen);
    http.end();
  }
  if(!body) return false;
  JsonDocument filter;
  filter["card"]["title"] = true;
  filter["card"]["content"]["chapters"][0]["key"]      = true;
  filter["card"]["content"]["chapters"][0]["title"]    = true;
  filter["card"]["content"]["chapters"][0]["duration"] = true;
  PsramAllocator psram;
  JsonDocument doc(&psram);
  DeserializationError err = deserializeJson(doc, (const char*)body, blen, DeserializationOption::Filter(filter));
  free(body);
  if(err) return false;
  JsonObject card = doc["card"];
  s_detail_title = (const char*)(card["title"] | c.title.c_str());
  s_ch_count = 0;
  for(JsonObject ch : card["content"]["chapters"].as<JsonArray>()){
    if(s_ch_count >= MAX_CH) break;
    s_ch[s_ch_count].key   = (const char*)(ch["key"]   | "");
    s_ch[s_ch_count].title = (const char*)(ch["title"] | "");
    s_ch[s_ch_count].dur   = (int)(ch["duration"] | 0);
    s_ch_count++;
  }
  sd_chap_write(c.id);                          // write-through so the next open is instant
  return true;
}

// ---------------- background library warmer ----------------
// Kicks in once at boot (and only re-does work when a NEW book appears — its SD files are simply
// absent), walking the catalog during idle to fetch whatever isn't cached yet: the slim chapter
// list and the detail-size cover. Result: every card's detail screen opens instantly and offline.
// One network fetch per call; the loop() caller gates it to idle home/screensaver time so the
// ~1s blocking fetch never interrupts a tap. A fully-warm library costs only cheap SD.exists()
// checks, so the scan completes in microseconds and latches s_pc_done.
static int  s_pc_cursor = 0;     // next card to inspect
static int  s_pc_fail   = 0;     // consecutive failures on the current item (bounded, then skip)
static bool s_pc_done   = false; // whole library warm — stop scanning until the next reboot

// Internal RAM is the board's tightest budget (a cover fetch = a TLS handshake + a PNG/JPEG
// decode, both hungry for contiguous internal RAM, all while the MQTT TLS session is live). The
// warmer fires back-to-back fetches, so during a cold mass-warm the low-water can dip to a few
// hundred bytes. Gate each step on a healthy largest-free-block: if internal RAM is already tight
// (a recent fetch/decode hasn't released yet, or MQTT is mid-burst), defer to a later idle tick so
// we never stack a fresh handshake onto an already-thin heap.
#define PC_MIN_INTERNAL_BLOCK 12288

static void precache_step(){
  if(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL) < PC_MIN_INTERNAL_BLOCK) return;
  while(s_pc_cursor < s_card_count){
    Card& c = s_cards[s_pc_cursor];
    if(c.daily){ s_pc_cursor++; s_pc_fail = 0; continue; }   // Daily has no static detail/chapters

    // 1) chapters → {id}.chap
    if(!SD.exists(sd_chap_path(c.id))){
      // fetch_card_detail overwrites the shared s_ch globals that tick_cb reads for playback
      // auto-advance. Single-threaded, so just save them and restore before returning — the next
      // tick (next loop iteration) then sees the original playing-card chapter list untouched.
      static Chapter sv[MAX_CH];
      int svc = s_ch_count; String svt = s_detail_title;
      for(int k=0;k<svc;k++) sv[k] = s_ch[k];
      bool ok = fetch_card_detail(s_pc_cursor);              // writes {id}.chap on success
      for(int k=0;k<svc;k++){ s_ch[k] = sv[k]; sv[k].key = String(); sv[k].title = String(); }
      s_ch_count = svc; s_detail_title = svt;
      if(!ok && ++s_pc_fail >= 3){ s_pc_cursor++; s_pc_fail = 0; }   // give up on this card till reboot
      return;                                                // one fetch per call — yield to the UI
    }
    // 2) detail-size cover → {id}_184.565
    if(!SD.exists(sd_cache_path(c.id, DCOVER_W))){
      if(!s_dcover) s_dcover = (uint8_t*)heap_caps_malloc(DCOVER_SZ, MALLOC_CAP_SPIRAM);
      bool ok = s_dcover && load_cover_cached(c.id, c.cover, DCOVER_W, DCOVER_H, DCOVER_SZ, s_dcover);
      if(!ok && ++s_pc_fail >= 3){ s_pc_cursor++; s_pc_fail = 0; }
      return;
    }
    // 3) now-playing cover → {id}_150.565 (reuse s_dcover as scratch — DCOVER_SZ > NCOVER_SZ,
    //    and no detail screen is live while the warmer runs on home/saver, so it's free)
    if(!SD.exists(sd_cache_path(c.id, NCOVER_W))){
      if(!s_dcover) s_dcover = (uint8_t*)heap_caps_malloc(DCOVER_SZ, MALLOC_CAP_SPIRAM);
      bool ok = s_dcover && load_cover_cached(c.id, c.cover, NCOVER_W, NCOVER_H, NCOVER_SZ, s_dcover);
      if(!ok && ++s_pc_fail >= 3){ s_pc_cursor++; s_pc_fail = 0; }
      return;
    }
    s_pc_cursor++; s_pc_fail = 0;                            // this card fully warm — next
  }
  s_pc_done = true;
}

static void back_cb(lv_event_t* e){
  lv_scr_load(s_scr_home);
  // async: we're deleting the screen that owns the button firing this event
  if(s_scr_detail){ lv_obj_del_async(s_scr_detail); s_scr_detail=nullptr; }
}

// (Re)build the tappable chapter rows from the current s_ch globals into `list`, clearing any
// existing rows first — so the same list can be refilled after a live fetch replaces the cached
// (or empty "loading") placeholder rendered when the screen first opened.
static void detail_fill_chapters(lv_obj_t* list){
  lv_obj_clean(list);
  for(int k=0;k<s_ch_count;k++){
    lv_obj_t* row = lv_obj_create(list);
    lv_obj_set_size(row, lv_pct(100), 62);
    lv_obj_set_style_bg_opa(row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_side(row, LV_BORDER_SIDE_BOTTOM, 0);
    lv_obj_set_style_border_width(row, 2, 0);
    lv_obj_set_style_border_color(row, lv_color_hex(0xE7E0D2), 0);
    lv_obj_set_style_radius(row, 0, 0);
    lv_obj_set_style_pad_all(row, 0, 0);
    lv_obj_set_style_pad_column(row, 16, 0);
    lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_clear_flag(row, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
    // press feedback: a soft red wash while the finger is down (rows are transparent at rest)
    lv_obj_set_style_bg_color(row, lv_color_hex(0xE5352B), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(row, LV_OPA_10, LV_STATE_PRESSED);
    lv_obj_set_user_data(row, (void*)(intptr_t)k);
    lv_obj_add_event_cb(row, [](lv_event_t* e){
      play_from((int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e)));
    }, LV_EVENT_CLICKED, NULL);

    lv_obj_t* badge = lv_obj_create(row);
    lv_obj_set_size(badge, 38, 38);
    lv_obj_set_style_radius(badge, 19, 0);
    lv_obj_set_style_bg_color(badge, lv_color_hex(0xFFB02E), 0);
    lv_obj_set_style_border_width(badge, 0, 0);
    lv_obj_clear_flag(badge, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t* bl = lv_label_create(badge);
    lv_label_set_text_fmt(bl, "%d", k+1);
    lv_obj_set_style_text_font(bl, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(bl, lv_color_hex(0x6A4400), 0);
    lv_obj_center(bl);

    lv_obj_t* ct = lv_label_create(row);
    lv_label_set_long_mode(ct, LV_LABEL_LONG_DOT);
    lv_obj_set_flex_grow(ct, 1);
    lv_label_set_text(ct, s_ch[k].title.c_str());
    lv_obj_set_style_text_font(ct, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(ct, lv_color_hex(0x23303A), 0);

    char db[16]; fmt_mmss(db, s_ch[k].dur);
    lv_obj_t* cd = lv_label_create(row);
    lv_label_set_text(cd, db);
    lv_obj_set_style_text_font(cd, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_color(cd, lv_color_hex(0x9A9384), 0);
  }
}

static void detail_set_summary(lv_obj_t* sum){
  if(s_ch_count <= 0){ lv_label_set_text(sum, "loading chapters..."); return; }
  int total=0; for(int k=0;k<s_ch_count;k++) total += s_ch[k].dur;
  char tb[32]; fmt_total(tb, total);
  lv_label_set_text_fmt(sum, "%d chapters   .   %s", s_ch_count, tb);
}

static void open_detail(int i){
  s_cur_card = i;
  Card& c = s_cards[i];

  // 1) Chapters: SD cache first — instant and offline. On a miss, open with an empty
  //    "loading" list and fetch live AFTER the screen is painted (slow path below).
  bool have_ch = sd_chap_read(c.id);
  if(!have_ch){ s_ch_count = 0; s_detail_title = c.title; }

  // 2) Detail cover: SD cache first. On a miss, fall back to the grid thumb (already decoded
  //    in PSRAM) upscaled in place as an instant placeholder; the sharp cover loads below.
  bool cover_sd = false;
  if(!s_dcover) s_dcover = (uint8_t*)heap_caps_malloc(DCOVER_SZ, MALLOC_CAP_SPIRAM);
  if(s_dcover) cover_sd = sd_cache_read(c.id, DCOVER_W, s_dcover, DCOVER_SZ);

  lv_obj_t* scr = lv_obj_create(NULL);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0xFBF7F0), 0);

  // ---- left white panel: cover + big Play ----
  lv_obj_t* left = lv_obj_create(scr);
  lv_obj_set_size(left, 286, 480);
  lv_obj_set_pos(left, 0, 0);
  lv_obj_set_style_bg_color(left, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_border_width(left, 0, 0);
  lv_obj_set_style_radius(left, 0, 0);
  lv_obj_set_style_pad_all(left, 20, 0);
  lv_obj_set_style_pad_row(left, 18, 0);
  lv_obj_set_flex_flow(left, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_flex_align(left, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  lv_obj_clear_flag(left, LV_OBJ_FLAG_SCROLLABLE);

  lv_obj_t* cover = lv_img_create(left);
  lv_obj_set_size(cover, DCOVER_W, DCOVER_H);
  lv_obj_set_style_radius(cover, 14, 0);
  lv_obj_set_style_clip_corner(cover, true, 0);
  lv_obj_set_style_bg_color(cover, lv_color_hex(0xE5352B), 0);
  lv_obj_set_style_bg_opa(cover, LV_OPA_COVER, 0);
  if(cover_sd){
    set_img(s_dcover_dsc, cover, s_dcover, DCOVER_W, DCOVER_H, DCOVER_SZ);
  } else if(c.thumb && c.dsc.data){
    // instant placeholder: reuse the grid thumb's (read-only) descriptor, upscaled to fill the
    // larger detail frame, until the full-res cover arrives in the slow path below
    lv_img_set_src(cover, &c.dsc);
    lv_img_set_pivot(cover, THUMB_W/2, THUMB_H/2);
    lv_img_set_zoom(cover, (uint16_t)(256 * DCOVER_W / THUMB_W));
  }

  lv_obj_t* play = lv_btn_create(left);
  lv_obj_set_size(play, DCOVER_W, 66);
  lv_obj_set_style_bg_color(play, lv_color_hex(0xE5352B), 0);
  lv_obj_set_style_radius(play, 18, 0);
  lv_obj_t* pl = lv_label_create(play);
  lv_label_set_text(pl, LV_SYMBOL_PLAY "  Play");
  lv_obj_set_style_text_font(pl, &lv_font_montserrat_24, 0);
  lv_obj_center(pl);
  lv_obj_add_event_cb(play, [](lv_event_t*){ play_from(0); }, LV_EVENT_CLICKED, NULL);

  // ---- right panel: title + chapter list ----
  lv_obj_t* right = lv_obj_create(scr);
  lv_obj_set_size(right, 514, 480);
  lv_obj_set_pos(right, 286, 0);
  lv_obj_set_style_bg_color(right, lv_color_hex(0xFBF7F0), 0);
  lv_obj_set_style_border_width(right, 0, 0);
  lv_obj_set_style_radius(right, 0, 0);
  lv_obj_set_style_pad_left(right, 18, 0);
  lv_obj_set_style_pad_right(right, 18, 0);
  lv_obj_set_style_pad_top(right, 18, 0);
  lv_obj_set_style_pad_bottom(right, 10, 0);
  lv_obj_set_style_pad_row(right, 4, 0);
  lv_obj_set_flex_flow(right, LV_FLEX_FLOW_COLUMN);
  lv_obj_clear_flag(right, LV_OBJ_FLAG_SCROLLABLE);

  lv_obj_t* title = lv_label_create(right);
  lv_label_set_long_mode(title, LV_LABEL_LONG_WRAP);
  lv_obj_set_width(title, 478);
  lv_label_set_text(title, s_detail_title.c_str());
  lv_obj_set_style_text_font(title, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(title, lv_color_hex(0xE5352B), 0);

  lv_obj_t* sum = lv_label_create(right);
  detail_set_summary(sum);
  lv_obj_set_style_text_font(sum, &lv_font_montserrat_16, 0);
  lv_obj_set_style_text_color(sum, lv_color_hex(0x3A6B50), 0);
  lv_obj_set_style_pad_bottom(sum, 4, 0);

  lv_obj_t* list = lv_obj_create(right);
  lv_obj_set_width(list, lv_pct(100));
  lv_obj_set_flex_grow(list, 1);
  lv_obj_set_style_bg_opa(list, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(list, 0, 0);
  lv_obj_set_style_pad_all(list, 0, 0);
  lv_obj_set_style_pad_right(list, 6, 0);
  lv_obj_set_style_pad_row(list, 0, 0);
  lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_scroll_dir(list, LV_DIR_VER);

  detail_fill_chapters(list);   // cached rows now, or empty until the fetch below returns

  // ---- back button (floats over the left panel) ----
  lv_obj_t* back = lv_btn_create(scr);
  lv_obj_set_size(back, 52, 52);
  lv_obj_set_pos(back, 14, 14);
  lv_obj_set_style_radius(back, 26, 0);
  lv_obj_set_style_bg_color(back, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_shadow_width(back, 8, 0);
  lv_obj_set_style_shadow_opa(back, LV_OPA_30, 0);
  lv_obj_add_event_cb(back, back_cb, LV_EVENT_CLICKED, NULL);
  lv_obj_t* bk = lv_label_create(back);
  lv_label_set_text(bk, LV_SYMBOL_LEFT);
  lv_obj_set_style_text_font(bk, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(bk, lv_color_hex(0x23303A), 0);
  lv_obj_center(bk);

  lv_obj_t* old = s_scr_detail;
  s_scr_detail = scr;
  lv_scr_load(scr);
  if(old) lv_obj_del(old);
  lv_refr_now(lv_disp_get_default());   // paint the screen NOW so the tap feels instant

  // ---- slow path: now that the screen is up, fill in whatever wasn't served from SD ----
  if(!have_ch && fetch_card_detail(i)){         // blocking TLS GET /card/{id} (+ writes SD cache)
    lv_label_set_text(title, s_detail_title.c_str());
    detail_set_summary(sum);
    detail_fill_chapters(list);
    lv_refr_now(lv_disp_get_default());
  }
  if(!cover_sd && s_dcover &&
     load_cover_cached(c.id, c.cover, DCOVER_W, DCOVER_H, DCOVER_SZ, s_dcover)){
    lv_img_set_zoom(cover, 256);                // undo the thumb-placeholder upscale
    lv_img_set_pivot(cover, 0, 0);
    set_img(s_dcover_dsc, cover, s_dcover, DCOVER_W, DCOVER_H, DCOVER_SZ);
    lv_refr_now(lv_disp_get_default());
  }
}

// ---------------- now-playing screen + playback ----------------
static int send_cmd(const char* name, const String& body){
  // Translate the slim app command into a real Yoto device command, clamp volume to the
  // parent-set cap, then POST to api.yotoplay.com.
  JsonDocument in;
  deserializeJson(in, body);            // body may be "{}" — that's fine
  String nm(name), cmd, payload;
  if(nm == "play"){
    cmd = "card/start";
    JsonDocument p;
    p["uri"] = String("https://yoto.io/") + (const char*)(in["id"] | "");
    const char* ck = in["chapterKey"]; if(ck && *ck) p["chapterKey"] = ck;
    const char* tk = in["trackKey"];   if(tk && *tk) p["trackKey"]   = tk;
    if(!in["secondsIn"].isNull()) p["secondsIn"] = in["secondsIn"];
    serializeJson(p, payload);
  } else if(nm == "pause" || nm == "resume" || nm == "stop"){
    cmd = "card/" + nm;
  } else if(nm == "volume"){
    int v = (int)(in["volume"] | 30); if(v < 0) v = 0; if(v > cfg::vol_max) v = cfg::vol_max;
    cmd = "volume/set";
    JsonDocument p; p["volume"] = v; serializeJson(p, payload);
  } else {
    return 404;
  }
  String path = String("/device-v2/") + cfg::device_id + "/command/" + cmd;
  String out;
  return yoto::post(path, payload.length() ? payload : String("{}"), out);
}

static void np_update_progress(){
  if(!s_np_barfill) return;
  int dur = s_play_dur;
  int pct = dur>0 ? (int)(100L*s_pos/dur) : 0;
  if(pct>100) pct=100;
  lv_obj_set_width(s_np_barfill, lv_pct(pct));
  if(s_np_tcur){ char b[16]; fmt_mmss(b, s_pos); lv_label_set_text(s_np_tcur, b); }
  if(s_np_trem){ char b[16]; fmt_mmss(b, dur-s_pos); lv_label_set_text_fmt(s_np_trem, "-%s", b); }
}
static void np_set_chapter_label(){
  if(s_np_chapter && s_cur_ch>=0 && s_cur_ch<s_ch_count)
    lv_label_set_text(s_np_chapter, s_ch[s_cur_ch].title.c_str());
}
static void np_set_playicon(){
  if(s_np_playicon) lv_label_set_text(s_np_playicon, s_playing? LV_SYMBOL_PAUSE : LV_SYMBOL_PLAY);
}

// Sleep-timer countdown as label text ("Off" / "23 min left").
static void fmt_sleep(char* b, size_t n){
  if(s_sleep_sec <= 0)       snprintf(b, n, "Off");
  else if(s_sleep_sec >= 60) snprintf(b, n, "%d min left", (s_sleep_sec + 59) / 60);
  else                       snprintf(b, n, "%d sec left", s_sleep_sec);
}

static void saver_tick();   // fwd (defined with the screensaver below)

// ---- live device state (MQTT) -> the optimistic playback vars the UI reads ----
// Find the grid index of a card by its Yoto cardId (-1 if not in the library / unknown).
static int card_index_by_id(const char* id){
  if(!id || !*id) return -1;
  for(int i = 0; i < s_card_count; i++) if(s_cards[i].id == id) return i;
  return -1;
}

// Pull the latest MQTT snapshot (only when it has advanced) and snap our local playback state
// to the device's truth — this is what makes "now playing" reflect playback started from the
// iOS app or the player's buttons, not just from this panel. Live is authoritative; the 1 Hz
// optimistic clock then fills the seconds between events. Runs on the LVGL thread (from
// tick_cb), so touching LVGL widgets here is safe. The home bar is a full rebuild, so only
// redraw it when the IDENTITY changes (card/chapter/play-state) — position alone just nudges
// the cheap progress widgets, even if the player streams a position event every second.
static uint32_t s_live_seq_seen = 0;
static void apply_live_state(){
  if(ymqtt::seq() == s_live_seq_seen) return;       // nothing new since last tick
  ymqtt::Live lv; ymqtt::snapshot(lv);
  s_live_seq_seen = lv.seq;

  bool   was_playing  = s_playing;
  int    prev_card    = s_play_card;
  String prev_chapter = s_play_chapter;

  if(lv.status == ymqtt::ST_PLAYING)                                  s_playing = true;
  else if(lv.status == ymqtt::ST_PAUSED || lv.status == ymqtt::ST_STOPPED) s_playing = false;

  int idx = card_index_by_id(lv.cardId);
  if(idx >= 0){ s_play_card = idx; s_play_title = s_cards[idx].title; }
  if(lv.chapterTitle[0]) s_play_chapter = lv.chapterTitle;
  if(lv.chapterKey[0]){ int k = atoi(lv.chapterKey) - 1; if(k >= 0) s_play_ch = k; }  // "01"->0
  if(lv.trackLength > 0) s_play_dur = lv.trackLength;
  if(lv.position >= 0){ s_pos = lv.position; if(s_play_dur > 0 && s_pos > s_play_dur) s_pos = s_play_dur; }
  if(lv.volume > 0) s_vol = lv.volume;
  if(s_cur_card == s_play_card && s_play_ch >= 0) s_cur_ch = s_play_ch;  // keep now-playing labels aligned

  np_set_chapter_label(); np_set_playicon(); np_update_progress();
  if(s_playing != was_playing || s_play_card != prev_card || s_play_chapter != prev_chapter)
    update_home_bar();
}

// 1 Hz heartbeat: optimistic playback clock (ticks on EVERY screen now, not only while the
// now-playing screen is visible — it's the source of truth the widgets read), chapter
// auto-advance, the bedtime sleep timer, and screensaver arming.
static void tick_cb(lv_timer_t*){
  apply_live_state();                       // device truth first; the optimistic clock fills gaps

  // periodic resync / keepalive: re-request state every ~4 min (also keeps the broker from
  // dropping the idle connection — belt-and-suspenders alongside esp-mqtt's own PINGREQ).
  static uint32_t s_ka = 0;
  if(millis() - s_ka > 240000u){ s_ka = millis(); ymqtt::request_state(); }

  if(s_playing && s_play_dur > 0){
    if(s_pos < s_play_dur) s_pos++;
    if(s_pos >= s_play_dur){
      // the real player rolls into the next chapter by itself — mirror it optimistically.
      // (we only know the chapter list while the browsed card IS the playing card;
      //  otherwise hold at 100% like before.)
      if(s_cur_card == s_play_card && s_play_ch >= 0 && s_play_ch < s_ch_count-1){
        s_play_ch++; s_cur_ch = s_play_ch; s_pos = 0;
        s_play_chapter = s_ch[s_play_ch].title;
        s_play_dur     = s_ch[s_play_ch].dur;
        np_set_chapter_label(); update_home_bar();
      } else if(s_cur_card == s_play_card){
        s_playing = false; np_set_playicon(); update_home_bar();  // last chapter finished
      }
    }
    np_update_progress();
  }

  // bedtime sleep timer — stop the player when it reaches zero
  if(s_sleep_sec > 0){
    s_sleep_sec--;
    if(s_sleep_sec == 0){
      if(s_playing){ s_playing = false; np_set_playicon(); update_home_bar(); }
      send_cmd("stop", "{}");
    }
    if(s_set_sleep_lbl){ char b[24]; fmt_sleep(b, sizeof b); lv_label_set_text(s_set_sleep_lbl, b); }
  }
  if(s_np_sleep){
    if(s_sleep_sec > 0) lv_label_set_text_fmt(s_np_sleep, "Zz %d min", (s_sleep_sec + 59) / 60);
    else                lv_label_set_text(s_np_sleep, "");
  }

  // night-clock screensaver after idle (any touch resets LVGL's inactivity clock).
  // s_scr_home gate: never cover the boot/status screen — go_home() needs a home to return to.
  if(cfg::saver_min > 0 && s_scr_home && !s_scr_saver &&
     lv_disp_get_inactive_time(NULL) > (uint32_t)cfg::saver_min * 60000u) saver_open();
  if(s_scr_saver) saver_tick();
}

// Every command below paints its new UI state FIRST (lv_refr_now) and only then does the
// blocking TLS POST — taps feel instant instead of "dead" for the network round-trip,
// which is exactly when a 7yo taps again and double-fires.
static void np_goto_chapter(int k){
  if(k<0 || k>=s_ch_count) return;
  s_cur_ch=k; s_pos=0; s_playing=true;
  s_play_card=s_cur_card; s_play_ch=k;                 // keep the mini-player in sync
  s_play_chapter=s_ch[k].title; s_play_dur=s_ch[k].dur;
  np_set_chapter_label(); np_set_playicon(); np_update_progress(); update_home_bar();
  lv_refr_now(lv_disp_get_default());
  send_cmd("play", String("{\"id\":\"")+s_cards[s_cur_card].id+"\",\"chapterKey\":\""+s_ch[k].key+"\"}");
}
static void np_toggle(){
  s_playing = !s_playing;
  np_set_playicon(); update_home_bar();
  lv_refr_now(lv_disp_get_default());
  send_cmd(s_playing ? "resume" : "pause", "{}");
}
static void np_next(){ if(s_cur_ch < s_ch_count-1) np_goto_chapter(s_cur_ch+1); }
static void np_prev(){ if(s_cur_ch > 0)           np_goto_chapter(s_cur_ch-1); }
static void np_volume(int v){ s_vol=v; send_cmd("volume", String("{\"volume\":")+v+"}"); }

static void go_home(){
  s_np_barfill=s_np_tcur=s_np_trem=s_np_playicon=s_np_chapter=s_np_sleep=nullptr;
  s_set_sleep_lbl=nullptr;
  lv_scr_load(s_scr_home);
  if(s_scr_detail){   lv_obj_del_async(s_scr_detail);   s_scr_detail=nullptr; }
  if(s_scr_now){      lv_obj_del_async(s_scr_now);      s_scr_now=nullptr; }
  if(s_scr_settings){ lv_obj_del_async(s_scr_settings); s_scr_settings=nullptr; }
}

// A one-second burst of soft confetti dots floating up the now-playing screen — the
// "your story is starting!" moment. Objects delete themselves when the rise finishes
// (lv_obj_del also kills the paired fade anim, so no dangling animations).
static void confetti_y(void* o, int32_t v){ lv_obj_set_y((lv_obj_t*)o, v); }
static void confetti_o(void* o, int32_t v){ lv_obj_set_style_opa((lv_obj_t*)o, v, 0); }
static void confetti_done(lv_anim_t* a){ lv_obj_del((lv_obj_t*)a->var); }
static void confetti(lv_obj_t* scr){
  static const uint32_t cols[4] = {0xFFB02E, 0xFFFFFF, 0xFBF7F0, 0xFF8A7E};
  for(int i=0;i<12;i++){
    int sz = 8 + (rand() % 8);
    lv_obj_t* d = lv_obj_create(scr);
    lv_obj_set_size(d, sz, sz);
    lv_obj_set_style_radius(d, sz/2, 0);
    lv_obj_set_style_bg_color(d, lv_color_hex(cols[i & 3]), 0);
    lv_obj_set_style_border_width(d, 0, 0);
    lv_obj_clear_flag(d, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(d, 80 + (rand() % 640), LCD_V_RES);
    uint32_t dur = 700 + (rand() % 500);
    lv_anim_t a; lv_anim_init(&a);
    lv_anim_set_var(&a, d);
    lv_anim_set_time(&a, dur);
    lv_anim_set_delay(&a, rand() % 250);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_set_exec_cb(&a, confetti_y);
    lv_anim_set_values(&a, LCD_V_RES, 60 + (rand() % 180));
    lv_anim_set_ready_cb(&a, confetti_done);
    lv_anim_start(&a);
    lv_anim_t b = a;                            // matching fade-out, no delete callback
    lv_anim_set_exec_cb(&b, confetti_o);
    lv_anim_set_values(&b, LV_OPA_COVER, LV_OPA_TRANSP);
    lv_anim_set_ready_cb(&b, NULL);
    lv_anim_start(&b);
  }
}

static void play_from(int chapterIdx){
  if(s_cur_card<0 || chapterIdx<0 || chapterIdx>=s_ch_count) return;
  s_cur_ch=chapterIdx; s_pos=0; s_playing=true;
  // snapshot what's now playing for the home mini-player (independent of later browsing)
  s_play_card=s_cur_card; s_play_ch=chapterIdx;
  s_play_title=s_detail_title; s_play_chapter=s_ch[chapterIdx].title;
  s_play_dur=s_ch[chapterIdx].dur;
  update_home_bar();
  open_now();                                  // show the screen (and confetti) right away…
  lv_refr_now(lv_disp_get_default());
  send_cmd("play", String("{\"id\":\"")+s_cards[s_cur_card].id+"\",\"chapterKey\":\""+s_ch[chapterIdx].key+"\"}");
  if(s_scr_now) confetti(s_scr_now);           // little celebration once the player obeys
}

// Yoto Daily: dig into card 3nC80's "daily" chapter and pick the track for `today` (YYYYMMDD).
// If today's isn't published yet, fall back to the most recent track on/before today (never a
// future-dated one). Returns the chosen trackKey + its title/duration.
static bool fetch_daily_track(const String& today, String& outKey, String& outTitle, int& outDur){
  size_t blen = 0; char* body = nullptr;
  {
    tlsarena::Guard tg;
    WiFiClientSecure cli; HTTPClient http;
    int code = yoto::get(cli, http, String("/card/") + YOTO_DAILY_ID);
    if(code != 200){ if(code > 0) http.end(); return false; }
    body = read_body_psram(http, blen);
    http.end();
  }
  if(!body) return false;
  JsonDocument filter;
  filter["card"]["content"]["chapters"][0]["key"]                  = true;
  filter["card"]["content"]["chapters"][0]["tracks"][0]["key"]      = true;
  filter["card"]["content"]["chapters"][0]["tracks"][0]["title"]    = true;
  filter["card"]["content"]["chapters"][0]["tracks"][0]["duration"] = true;
  PsramAllocator psram;
  JsonDocument doc(&psram);
  DeserializationError err = deserializeJson(doc, (const char*)body, blen, DeserializationOption::Filter(filter));
  free(body);
  if(err) return false;
  String bestKey, bestTitle; int bestDur = 0;
  for(JsonObject ch : doc["card"]["content"]["chapters"].as<JsonArray>()){
    if(String((const char*)(ch["key"] | "")) != "daily") continue;
    for(JsonObject tr : ch["tracks"].as<JsonArray>()){
      String k = (const char*)(tr["key"] | "");
      if(k.length() == 0) continue;
      if(k == today){                                   // exact: today's episode
        outKey = k; outTitle = (const char*)(tr["title"] | "Today's episode");
        outDur = (int)(tr["duration"] | 0);
        return true;
      }
      if(k <= today && k > bestKey){                    // newest published on/before today
        bestKey = k; bestTitle = (const char*)(tr["title"] | "Today's episode");
        bestDur = (int)(tr["duration"] | 0);
      }
    }
    break;
  }
  if(bestKey.length()){ outKey = bestKey; outTitle = bestTitle; outDur = bestDur; return true; }
  return false;
}

// One-tap Yoto Daily: resolve today's track and start it on the player. We present it as a
// single-"chapter" card so the now-playing screen + home mini-player render with no special cases.
static void play_yoto_daily(int idx){
  struct tm tmv;
  if(!getLocalTime(&tmv, 500)){ status("Clock not synced yet — try again in a moment"); return; }
  char today[9]; strftime(today, sizeof today, "%Y%m%d", &tmv);

  String key, title; int dur = 0;
  if(!fetch_daily_track(String(today), key, title, dur)){ status("Couldn't load Yoto Daily"); return; }

  s_cur_card = idx; s_detail_title = "Yoto Daily";
  s_ch_count = 1; s_cur_ch = 0;
  s_ch[0].key = "daily"; s_ch[0].title = title; s_ch[0].dur = dur;
  s_pos = 0; s_playing = true;
  s_play_card = idx; s_play_ch = 0;
  s_play_title = "Yoto Daily"; s_play_chapter = title; s_play_dur = dur;
  update_home_bar();
  open_now();
  lv_refr_now(lv_disp_get_default());
  send_cmd("play", String("{\"id\":\"") + YOTO_DAILY_ID + "\",\"chapterKey\":\"daily\",\"trackKey\":\"" + key + "\"}");
  if(s_scr_now) confetti(s_scr_now);
}

static void open_now(){
  bool okv=false;
  if(!s_ncover) s_ncover=(uint8_t*)heap_caps_malloc(NCOVER_SZ, MALLOC_CAP_SPIRAM);
  if(s_ncover){
    okv=load_cover_cached(s_cards[s_cur_card].id, s_cards[s_cur_card].cover, NCOVER_W, NCOVER_H, NCOVER_SZ, s_ncover);
  }

  lv_obj_t* scr=lv_obj_create(NULL);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0xE5352B), 0);
  lv_obj_set_style_bg_grad_color(scr, lv_color_hex(0xB3211A), 0);
  lv_obj_set_style_bg_grad_dir(scr, LV_GRAD_DIR_VER, 0);
  lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

  // top: chevron-down (home) + label
  lv_obj_t* chev=lv_btn_create(scr);
  lv_obj_set_size(chev, 56, 56); lv_obj_set_pos(chev, 12, 10);
  lv_obj_set_style_bg_opa(chev, LV_OPA_TRANSP, 0);
  lv_obj_set_style_shadow_width(chev, 0, 0);
  lv_obj_add_event_cb(chev, [](lv_event_t*){ go_home(); }, LV_EVENT_CLICKED, NULL);
  lv_obj_t* cl=lv_label_create(chev); lv_label_set_text(cl, LV_SYMBOL_DOWN);
  lv_obj_set_style_text_font(cl, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(cl, lv_color_hex(0xFFFFFF), 0); lv_obj_center(cl);

  lv_obj_t* top=lv_label_create(scr);
  lv_label_set_text(top, "Playing on Kiddo's Yoto");
  lv_obj_set_style_text_font(top, &lv_font_montserrat_18, 0);
  lv_obj_set_style_text_color(top, lv_color_hex(0xFFFFFF), 0);
  lv_obj_align(top, LV_ALIGN_TOP_MID, 0, 24);

  // sleep-timer chip (top right): "Zz 23 min" while the bedtime timer runs, blank otherwise
  s_np_sleep=lv_label_create(scr);
  if(s_sleep_sec > 0) lv_label_set_text_fmt(s_np_sleep, "Zz %d min", (s_sleep_sec + 59) / 60);
  else                lv_label_set_text(s_np_sleep, "");
  lv_obj_set_style_text_font(s_np_sleep, &lv_font_montserrat_18, 0);
  lv_obj_set_style_text_color(s_np_sleep, lv_color_hex(0xFFD9A0), 0);
  lv_obj_align(s_np_sleep, LV_ALIGN_TOP_RIGHT, -18, 24);

  // cover
  lv_obj_t* cover=lv_img_create(scr);
  lv_obj_set_size(cover, NCOVER_W, NCOVER_H); lv_obj_set_pos(cover, 44, 120);
  lv_obj_set_style_radius(cover, 14, 0); lv_obj_set_style_clip_corner(cover, true, 0);
  lv_obj_set_style_bg_color(cover, lv_color_hex(0xB3211A), 0);
  lv_obj_set_style_bg_opa(cover, LV_OPA_COVER, 0);
  if(okv) set_img(s_ncover_dsc, cover, s_ncover, NCOVER_W, NCOVER_H, NCOVER_SZ);

  const int ix=224, iw=536;
  lv_obj_t* tt=lv_label_create(scr);
  lv_label_set_long_mode(tt, LV_LABEL_LONG_DOT);
  lv_obj_set_width(tt, iw); lv_obj_set_pos(tt, ix, 124);
  lv_label_set_text(tt, s_detail_title.c_str());
  lv_obj_set_style_text_font(tt, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(tt, lv_color_hex(0xFFFFFF), 0);

  s_np_chapter=lv_label_create(scr);
  lv_label_set_long_mode(s_np_chapter, LV_LABEL_LONG_DOT);
  lv_obj_set_width(s_np_chapter, iw); lv_obj_set_pos(s_np_chapter, ix, 166);
  lv_obj_set_style_text_font(s_np_chapter, &lv_font_montserrat_24, 0);
  lv_obj_set_style_text_color(s_np_chapter, lv_color_hex(0xFFFFFF), 0);
  np_set_chapter_label();

  // progress
  lv_obj_t* track=lv_obj_create(scr);
  lv_obj_set_size(track, iw, 12); lv_obj_set_pos(track, ix, 224);
  lv_obj_set_style_radius(track, 6, 0);
  lv_obj_set_style_bg_color(track, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_bg_opa(track, LV_OPA_30, 0);
  lv_obj_set_style_border_width(track, 0, 0);
  lv_obj_set_style_pad_all(track, 0, 0);
  lv_obj_clear_flag(track, LV_OBJ_FLAG_SCROLLABLE);
  s_np_barfill=lv_obj_create(track);
  lv_obj_set_size(s_np_barfill, lv_pct(0), 12);
  lv_obj_align(s_np_barfill, LV_ALIGN_LEFT_MID, 0, 0);
  lv_obj_set_style_radius(s_np_barfill, 6, 0);
  lv_obj_set_style_bg_color(s_np_barfill, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_border_width(s_np_barfill, 0, 0);
  lv_obj_clear_flag(s_np_barfill, LV_OBJ_FLAG_SCROLLABLE);

  s_np_tcur=lv_label_create(scr);
  lv_obj_set_pos(s_np_tcur, ix, 242);
  lv_obj_set_style_text_font(s_np_tcur, &lv_font_montserrat_16, 0);
  lv_obj_set_style_text_color(s_np_tcur, lv_color_hex(0xFFFFFF), 0);
  s_np_trem=lv_label_create(scr);
  lv_obj_set_pos(s_np_trem, ix+iw-58, 242);
  lv_obj_set_style_text_font(s_np_trem, &lv_font_montserrat_16, 0);
  lv_obj_set_style_text_color(s_np_trem, lv_color_hex(0xFFFFFF), 0);

  // controls — big transport buttons (72/84px) with 36px glyphs for easy little-finger taps
  const int cy=292;
  lv_obj_t* prev=lv_btn_create(scr);
  lv_obj_set_size(prev, 72, 72); lv_obj_set_pos(prev, ix, cy);
  lv_obj_set_style_bg_opa(prev, LV_OPA_TRANSP, 0); lv_obj_set_style_shadow_width(prev,0,0);
  lv_obj_add_event_cb(prev, [](lv_event_t*){ np_prev(); }, LV_EVENT_CLICKED, NULL);
  lv_obj_t* pv=lv_label_create(prev); lv_label_set_text(pv, LV_SYMBOL_PREV);
  lv_obj_set_style_text_font(pv,&lv_font_montserrat_36,0);
  lv_obj_set_style_text_color(pv,lv_color_hex(0xFFFFFF),0); lv_obj_center(pv);

  lv_obj_t* big=lv_btn_create(scr);
  lv_obj_set_size(big, 84, 84); lv_obj_set_pos(big, ix+84, cy-6);
  lv_obj_set_style_radius(big, 42, 0);
  lv_obj_set_style_bg_color(big, lv_color_hex(0xFFFFFF), 0);
  lv_obj_add_event_cb(big, [](lv_event_t*){ np_toggle(); }, LV_EVENT_CLICKED, NULL);
  s_np_playicon=lv_label_create(big);
  lv_obj_set_style_text_font(s_np_playicon,&lv_font_montserrat_36,0);
  lv_obj_set_style_text_color(s_np_playicon,lv_color_hex(0xE5352B),0); lv_obj_center(s_np_playicon);
  np_set_playicon();

  lv_obj_t* next=lv_btn_create(scr);
  lv_obj_set_size(next, 72, 72); lv_obj_set_pos(next, ix+186, cy);
  lv_obj_set_style_bg_opa(next, LV_OPA_TRANSP, 0); lv_obj_set_style_shadow_width(next,0,0);
  lv_obj_add_event_cb(next, [](lv_event_t*){ np_next(); }, LV_EVENT_CLICKED, NULL);
  lv_obj_t* nx=lv_label_create(next); lv_label_set_text(nx, LV_SYMBOL_NEXT);
  lv_obj_set_style_text_font(nx,&lv_font_montserrat_36,0);
  lv_obj_set_style_text_color(nx,lv_color_hex(0xFFFFFF),0); lv_obj_center(nx);

  // volume — its own row below the transport, with a thicker, longer slider that's easy to drag
  lv_obj_t* vi=lv_label_create(scr);
  lv_label_set_text(vi, LV_SYMBOL_VOLUME_MID);
  lv_obj_set_style_text_font(vi,&lv_font_montserrat_24,0);
  lv_obj_set_style_text_color(vi,lv_color_hex(0xFFFFFF),0);
  lv_obj_set_pos(vi, ix, cy+108);
  lv_obj_t* slider=lv_slider_create(scr);
  lv_obj_set_size(slider, 260, 16); lv_obj_set_pos(slider, ix+44, cy+112);
  // the parent-set cap IS the top of the slider — full-right now means "as loud as allowed",
  // instead of showing 100 while the command silently clamps to the cap
  lv_slider_set_range(slider, 0, cfg::vol_max);
  lv_slider_set_value(slider, s_vol > cfg::vol_max ? cfg::vol_max : s_vol, LV_ANIM_OFF);
  lv_obj_set_style_bg_color(slider, lv_color_hex(0xFFFFFF), LV_PART_MAIN);
  lv_obj_set_style_bg_opa(slider, LV_OPA_40, LV_PART_MAIN);
  lv_obj_set_style_bg_color(slider, lv_color_hex(0xFFFFFF), LV_PART_INDICATOR);
  lv_obj_set_style_bg_color(slider, lv_color_hex(0xFFFFFF), LV_PART_KNOB);
  lv_obj_set_style_pad_all(slider, 10, LV_PART_KNOB);   // grow the knob (~36px) so it's easy to grab
  lv_obj_add_event_cb(slider, [](lv_event_t* e){
    np_volume(lv_slider_get_value(lv_event_get_target(e)));
  }, LV_EVENT_RELEASED, NULL);

  np_update_progress();

  lv_obj_t* oldd=s_scr_detail; s_scr_detail=nullptr;
  lv_obj_t* oldn=s_scr_now;    s_scr_now=scr;
  lv_scr_load(scr);
  if(oldd) lv_obj_del_async(oldd);   // detail screen owns the row firing this event
  if(oldn) lv_obj_del_async(oldn);
}

// ---------------- grown-ups settings screen ----------------
// Opened by HOLDING the gear in the home bar for 2.5s. Parent knobs live here; WiFi and
// Yoto credentials live on the web portal instead (typing a 64-char client secret on a
// wall panel is nobody's idea of fun).
static lv_obj_t* s_set_vol_lbl=nullptr;     // only touched by callbacks living on the
static lv_obj_t* s_set_saver_lbl=nullptr;   // settings screen itself, so they can't dangle
static lv_obj_t* s_set_wake_lbl=nullptr;    // (s_set_sleep_lbl is tick-updated -> go_home nulls it)
static lv_obj_t* s_set_night_lbl=nullptr;
static lv_obj_t* s_set_size_lbl=nullptr;

static void fmt_wake(char* b, size_t n){
  int h12 = ((cfg::wake_h + 11) % 12) + 1;
  snprintf(b, n, "%d:%02d %s", h12, cfg::wake_m, cfg::wake_h < 12 ? "am" : "pm");
}

static lv_obj_t* set_section(lv_obj_t* p, const char* t){
  lv_obj_t* l = lv_label_create(p);
  lv_label_set_text(l, t);
  lv_obj_set_style_text_font(l, &lv_font_montserrat_24, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(0x3A6B50), 0);
  lv_obj_set_style_pad_top(l, 12, 0);
  return l;
}
static lv_obj_t* set_row(lv_obj_t* p){
  lv_obj_t* r = lv_obj_create(p);
  lv_obj_set_size(r, lv_pct(100), LV_SIZE_CONTENT);
  lv_obj_set_style_bg_opa(r, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(r, 0, 0);
  lv_obj_set_style_pad_all(r, 0, 0);
  lv_obj_set_style_pad_column(r, 12, 0);
  lv_obj_set_flex_flow(r, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(r, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  lv_obj_clear_flag(r, LV_OBJ_FLAG_SCROLLABLE);
  return r;
}
static lv_obj_t* pill_btn(lv_obj_t* row, const char* txt, int ud, lv_event_cb_t cb){
  lv_obj_t* b = lv_btn_create(row);
  lv_obj_set_size(b, 88, 56);
  lv_obj_set_style_radius(b, 14, 0);
  lv_obj_set_style_bg_color(b, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_bg_color(b, lv_color_hex(0xF3E9D8), LV_STATE_PRESSED);
  lv_obj_set_style_shadow_width(b, 0, 0);
  lv_obj_set_style_border_width(b, 1, 0);
  lv_obj_set_style_border_color(b, lv_color_hex(0xE7E0D2), 0);
  lv_obj_set_user_data(b, (void*)(intptr_t)ud);
  lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, NULL);
  lv_obj_t* l = lv_label_create(b);
  lv_label_set_text(l, txt);
  lv_obj_set_style_text_font(l, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(0x23303A), 0);
  lv_obj_center(l);
  return b;
}

static void set_sleep_cb(lv_event_t* e){
  s_sleep_sec = 60 * (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
  if(s_set_sleep_lbl){ char b[24]; fmt_sleep(b, sizeof b); lv_label_set_text(s_set_sleep_lbl, b); }
}
static void set_saver_cb(lv_event_t* e){
  cfg::saver_min = (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
  cfg::save();
  if(s_set_saver_lbl){
    if(cfg::saver_min) lv_label_set_text_fmt(s_set_saver_lbl, "after %d min", cfg::saver_min);
    else               lv_label_set_text(s_set_saver_lbl, "Off");
  }
}
static void set_wake_cb(lv_event_t* e){
  int t = cfg::wake_h * 60 + cfg::wake_m + (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
  if(t < 5*60)  t = 5*60;                    // sane bedroom range: 5:00 – 10:00 am
  if(t > 10*60) t = 10*60;
  cfg::wake_h = t / 60; cfg::wake_m = t % 60;
  cfg::save();
  if(s_set_wake_lbl){ char b[16]; fmt_wake(b, sizeof b); lv_label_set_text(s_set_wake_lbl, b); }
}
static void fmt_night(char* b, size_t n){
  int h12 = ((cfg::night_h + 11) % 12) + 1;
  snprintf(b, n, "%d:%02d %s", h12, cfg::night_m, cfg::night_h < 12 ? "am" : "pm");
}
static void set_night_cb(lv_event_t* e){
  int t = cfg::night_h * 60 + cfg::night_m + (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
  if(t < 17*60) t = 17*60;                   // sane bedtime range: 5:00 – 11:00 pm
  if(t > 23*60) t = 23*60;
  cfg::night_h = t / 60; cfg::night_m = t % 60;
  cfg::save();
  if(s_set_night_lbl){ char b[16]; fmt_night(b, sizeof b); lv_label_set_text(s_set_night_lbl, b); }
}
static const char* SV_SIZE_NAME[3] = {"Small", "Medium", "Large"};
static void set_size_cb(lv_event_t* e){
  int v = (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
  cfg::saver_size = v < 0 ? 0 : v > 2 ? 2 : v;
  cfg::save();                               // applies the next time the clock comes on
  if(s_set_size_lbl) lv_label_set_text(s_set_size_lbl, SV_SIZE_NAME[cfg::saver_size]);
}
static void set_vol_drag_cb(lv_event_t* e){   // live readout while dragging
  if(s_set_vol_lbl) lv_label_set_text_fmt(s_set_vol_lbl, "%d", lv_slider_get_value(lv_event_get_target(e)));
}
static void set_vol_done_cb(lv_event_t* e){   // persist + apply on release
  int v = lv_slider_get_value(lv_event_get_target(e));
  cfg::vol_max = v; cfg::save();
  if(s_vol > v) np_volume(v);                 // pull the live volume under the new cap
}

static void open_settings(){
  lv_obj_t* scr = lv_obj_create(NULL);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0xFBF7F0), 0);

  lv_obj_t* back = lv_btn_create(scr);
  lv_obj_set_size(back, 52, 52);
  lv_obj_set_pos(back, 14, 14);
  lv_obj_set_style_radius(back, 26, 0);
  lv_obj_set_style_bg_color(back, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_shadow_width(back, 8, 0);
  lv_obj_set_style_shadow_opa(back, LV_OPA_30, 0);
  lv_obj_add_event_cb(back, [](lv_event_t*){ go_home(); }, LV_EVENT_CLICKED, NULL);
  lv_obj_t* bk = lv_label_create(back);
  lv_label_set_text(bk, LV_SYMBOL_LEFT);
  lv_obj_set_style_text_font(bk, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(bk, lv_color_hex(0x23303A), 0);
  lv_obj_center(bk);

  lv_obj_t* title = lv_label_create(scr);
  lv_label_set_text(title, "Grown-ups");
  lv_obj_set_style_text_font(title, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(title, lv_color_hex(0xE5352B), 0);
  lv_obj_set_pos(title, 80, 24);

  lv_obj_t* col = lv_obj_create(scr);
  lv_obj_set_size(col, LCD_H_RES - 40, LCD_V_RES - 84);
  lv_obj_set_pos(col, 20, 76);
  lv_obj_set_style_bg_opa(col, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(col, 0, 0);
  lv_obj_set_style_pad_all(col, 4, 0);
  lv_obj_set_style_pad_row(col, 10, 0);
  lv_obj_set_flex_flow(col, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_scroll_dir(col, LV_DIR_VER);

  // ---- volume limit ----
  set_section(col, "Volume limit");
  lv_obj_t* row = set_row(col);
  lv_obj_set_style_pad_all(row, 8, 0);
  lv_obj_t* vs = lv_slider_create(row);
  lv_obj_set_size(vs, 520, 18);
  lv_slider_set_range(vs, 10, 100);
  lv_slider_set_value(vs, cfg::vol_max, LV_ANIM_OFF);
  lv_obj_set_style_bg_color(vs, lv_color_hex(0xE5352B), LV_PART_INDICATOR);
  lv_obj_set_style_bg_color(vs, lv_color_hex(0xE5352B), LV_PART_KNOB);
  lv_obj_set_style_pad_all(vs, 10, LV_PART_KNOB);   // fat knob (~38px) — easy to grab
  lv_obj_add_event_cb(vs, set_vol_drag_cb, LV_EVENT_VALUE_CHANGED, NULL);
  lv_obj_add_event_cb(vs, set_vol_done_cb, LV_EVENT_RELEASED, NULL);
  s_set_vol_lbl = lv_label_create(row);
  lv_label_set_text_fmt(s_set_vol_lbl, "%d", cfg::vol_max);
  lv_obj_set_style_text_font(s_set_vol_lbl, &lv_font_montserrat_24, 0);
  lv_obj_set_style_text_color(s_set_vol_lbl, lv_color_hex(0x23303A), 0);

  // ---- bedtime sleep timer ----
  set_section(col, "Sleep timer  (stops the player)");
  row = set_row(col);
  pill_btn(row, "Off", 0,  set_sleep_cb);
  pill_btn(row, "15",  15, set_sleep_cb);
  pill_btn(row, "30",  30, set_sleep_cb);
  pill_btn(row, "45",  45, set_sleep_cb);
  pill_btn(row, "60",  60, set_sleep_cb);
  s_set_sleep_lbl = lv_label_create(row);
  { char b[24]; fmt_sleep(b, sizeof b); lv_label_set_text(s_set_sleep_lbl, b); }
  lv_obj_set_style_text_font(s_set_sleep_lbl, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(s_set_sleep_lbl, lv_color_hex(0x9A9384), 0);

  // ---- night clock (screensaver) ----
  set_section(col, "Night clock when idle");
  row = set_row(col);
  pill_btn(row, "Off", 0,  set_saver_cb);
  pill_btn(row, "5",   5,  set_saver_cb);
  pill_btn(row, "10",  10, set_saver_cb);
  pill_btn(row, "30",  30, set_saver_cb);
  s_set_saver_lbl = lv_label_create(row);
  if(cfg::saver_min) lv_label_set_text_fmt(s_set_saver_lbl, "after %d min", cfg::saver_min);
  else               lv_label_set_text(s_set_saver_lbl, "Off");
  lv_obj_set_style_text_font(s_set_saver_lbl, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(s_set_saver_lbl, lv_color_hex(0x9A9384), 0);

  // ---- bedtime ----
  set_section(col, "Bedtime  (clock turns red)");
  row = set_row(col);
  pill_btn(row, "-15", -15, set_night_cb);
  s_set_night_lbl = lv_label_create(row);
  { char b[16]; fmt_night(b, sizeof b); lv_label_set_text(s_set_night_lbl, b); }
  lv_obj_set_style_text_font(s_set_night_lbl, &lv_font_montserrat_24, 0);
  lv_obj_set_style_text_color(s_set_night_lbl, lv_color_hex(0x23303A), 0);
  pill_btn(row, "+15", 15, set_night_cb);

  // ---- OK-to-wake ----
  set_section(col, "OK-to-wake  (clock turns green)");
  row = set_row(col);
  pill_btn(row, "-15", -15, set_wake_cb);
  s_set_wake_lbl = lv_label_create(row);
  { char b[16]; fmt_wake(b, sizeof b); lv_label_set_text(s_set_wake_lbl, b); }
  lv_obj_set_style_text_font(s_set_wake_lbl, &lv_font_montserrat_24, 0);
  lv_obj_set_style_text_color(s_set_wake_lbl, lv_color_hex(0x23303A), 0);
  pill_btn(row, "+15", 15, set_wake_cb);

  // ---- clock size ----
  set_section(col, "Clock size");
  row = set_row(col);
  lv_obj_set_size(pill_btn(row, "Small",  0, set_size_cb), 124, 56);
  lv_obj_set_size(pill_btn(row, "Medium", 1, set_size_cb), 124, 56);
  lv_obj_set_size(pill_btn(row, "Large",  2, set_size_cb), 124, 56);
  s_set_size_lbl = lv_label_create(row);
  lv_label_set_text(s_set_size_lbl, SV_SIZE_NAME[cfg::saver_size < 0 ? 0 : cfg::saver_size > 2 ? 2 : cfg::saver_size]);
  lv_obj_set_style_text_font(s_set_size_lbl, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(s_set_size_lbl, lv_color_hex(0x9A9384), 0);

  // ---- this panel ----
  set_section(col, "This panel");
  String ip = WiFi.localIP().toString();
  String sd = s_sd_ok ? String((uint32_t)(SD.usedBytes()/(1024ULL*1024ULL))) + " MB used" : String("none");
  lv_obj_t* info = lv_label_create(col);
  lv_label_set_text_fmt(info,
    "WiFi: %s   (%s, %d dBm)\n"
    "WiFi, Yoto account, timezone:  http://%s/settings\n"
    "Firmware update:  http://%s/update\n"
    "Cards: %d     SD cache: %s\n"
    "Free RAM: %u KB     Built: %s",
    cfg::wifi_ssid.c_str(), ip.c_str(), (int)WiFi.RSSI(),
    ip.c_str(), ip.c_str(),
    s_card_count, sd.c_str(),
    (unsigned)(ESP.getFreeHeap()/1024), __DATE__);
  lv_obj_set_style_text_font(info, &lv_font_montserrat_18, 0);
  lv_obj_set_style_text_color(info, lv_color_hex(0x9A9384), 0);

  row = set_row(col);
  lv_obj_set_style_pad_top(row, 8, 0);
  lv_obj_t* rb = pill_btn(row, "Restart", 0, [](lv_event_t*){ ESP.restart(); });
  lv_obj_set_size(rb, 150, 56);
  lv_obj_set_style_text_color(rb, lv_color_hex(0xE5352B), 0);
  lv_obj_set_style_text_color(lv_obj_get_child(rb, 0), lv_color_hex(0xE5352B), 0);

  s_scr_settings = scr;
  lv_scr_load(scr);
}

// ---------------- night-clock screensaver + OK-to-wake ----------------
// After cfg::saver_min idle minutes the panel becomes a bedside clock: huge 7-segment
// digits drawn from plain lv_objs (LVGL's biggest built-in font is 48px — far too small
// to fill an 800x480 panel, and a 300px bitmap font would bloat flash; bars scale free).
// RED from bedtime to wake time ("stay in bed"), GREEN for two hours from wake time (the
// classic kids' "OK to get up" signal), dim cream the rest of the day. No words — the
// color IS the message. Tap anywhere to get the cards back.
static void saver_close_cb(lv_event_t*){ saver_close(); }

// segment bit order: A top, B top-right, C bottom-right, D bottom, E bottom-left,
// F top-left, G middle (the standard 7-seg encoding)
static const uint8_t SEG_MAP[10] = {0x3F,0x06,0x5B,0x4F,0x66,0x6D,0x7D,0x07,0x7F,0x6F};

static void sv_set_digit(int d, int v){   // v: 0-9 · -1 = dash (NTP not synced) · -2 = blank
  uint8_t m = v >= 0 ? SEG_MAP[v] : (v == -1 ? 0x40 : 0x00);
  for(int s = 0; s < 7; ++s){
    if(m & (1u << s)) lv_obj_clear_flag(s_sv_seg[d][s], LV_OBJ_FLAG_HIDDEN);
    else              lv_obj_add_flag(s_sv_seg[d][s], LV_OBJ_FLAG_HIDDEN);
  }
}

static void saver_tick(){
  if(!s_scr_saver) return;
  struct tm tmv;
  bool ok    = getLocalTime(&tmv, 0);
  int now_m  = ok ? tmv.tm_hour * 60 + tmv.tm_min : -1;
  int wake_t  = cfg::wake_h  * 60 + cfg::wake_m;
  int night_t = cfg::night_h * 60 + cfg::night_m;
  bool night   = ok && ((now_m >= night_t) || (now_m < wake_t));
  bool morning = ok && !night && (now_m < wake_t + 120);
  uint32_t col = night ? 0xE5352B : morning ? 0x3AB06A : 0xF2EDE0;
  if(now_m == s_sv_lastmin && col == s_sv_lastcol) return;   // only repaint on change —
  s_sv_lastmin = now_m; s_sv_lastcol = col;                  // this ticks at 1 Hz
  int hh = ok ? ((tmv.tm_hour + 11) % 12) + 1 : -1;
  int four = (!ok || hh >= 10) ? 1 : 0;    // dashes + 10-12 o'clock use all 4 cells
  if(four != s_sv_last4){                  // slide so the VISIBLE cells stay centered
    s_sv_last4 = four;
    lv_obj_set_x(s_sv_cont, four ? s_sv_x4 : s_sv_x3);
  }
  if(ok){
    sv_set_digit(0, hh >= 10 ? 1 : -2);    // blank leading digit for 1:00-9:59
    sv_set_digit(1, hh % 10);
    sv_set_digit(2, tmv.tm_min / 10);
    sv_set_digit(3, tmv.tm_min % 10);
  } else {
    for(int d = 0; d < 4; ++d) sv_set_digit(d, -1);          // ----  until NTP syncs
  }
  lv_color_t c = lv_color_hex(col);
  for(int d = 0; d < 4; ++d)
    for(int s = 0; s < 7; ++s) lv_obj_set_style_bg_color(s_sv_seg[d][s], c, 0);
  lv_obj_set_style_bg_color(s_sv_colon[0], c, 0);
  lv_obj_set_style_bg_color(s_sv_colon[1], c, 0);
}

static lv_obj_t* sv_bar(lv_obj_t* par, int x, int y, int w, int h){
  lv_obj_t* o = lv_obj_create(par);
  lv_obj_set_pos(o, x, y); lv_obj_set_size(o, w, h);
  lv_obj_set_style_radius(o, (w < h ? w : h) / 2, 0);
  lv_obj_set_style_border_width(o, 0, 0);
  lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);  // taps fall through to the screen
  return o;
}

static void saver_open(){
  lv_obj_t* scr = lv_obj_create(NULL);
  lv_obj_set_style_bg_color(scr, lv_color_hex(0x0A0E1A), 0);
  lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_add_event_cb(scr, saver_close_cb, LV_EVENT_CLICKED, NULL);

  // digit cell geometry from the configured size; Large fills most of the panel
  static const int DIG_H[3] = {200, 260, 320};
  int H = DIG_H[cfg::saver_size < 0 ? 0 : cfg::saver_size > 2 ? 2 : cfg::saver_size];
  int t = H / 8, W = H / 2, gap = H / 16;
  int vlen = (H - 3 * t) / 2;                      // vertical bar length (B,C,E,F)
  int total = 4 * W + t + 4 * gap;                 // 4 digits + colon column + gaps
  // all bars live in one transparent container that slides horizontally so the VISIBLE
  // time stays centered: hours 1-9 hide the leading digit, which would otherwise leave
  // the clock holding an empty cell on the left and sitting right of center all evening
  s_sv_x4 = (LCD_H_RES - total) / 2;                              // 10:00-12:59 and ----
  s_sv_x3 = (LCD_H_RES - (total - W - gap)) / 2 - (W + gap);      // 1:00-9:59
  lv_obj_t* cont = lv_obj_create(scr);
  lv_obj_set_size(cont, total, H);
  lv_obj_set_pos(cont, s_sv_x4, (LCD_V_RES - H) / 2);
  lv_obj_set_style_bg_opa(cont, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(cont, 0, 0);
  lv_obj_set_style_pad_all(cont, 0, 0);
  lv_obj_clear_flag(cont, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
  s_sv_cont = cont;
  int xs[4]; xs[0] = 0; xs[1] = W + gap;
  int xc = xs[1] + W + gap;                        // colon column
  xs[2] = xc + t + gap; xs[3] = xs[2] + W + gap;
  for(int d = 0; d < 4; ++d){
    int X = xs[d];
    s_sv_seg[d][0] = sv_bar(cont, X + t,     0,             W - 2*t, t);  // A
    s_sv_seg[d][1] = sv_bar(cont, X + W - t, t,             t, vlen);     // B
    s_sv_seg[d][2] = sv_bar(cont, X + W - t, (H + t)/2,     t, vlen);     // C
    s_sv_seg[d][3] = sv_bar(cont, X + t,     H - t,         W - 2*t, t);  // D
    s_sv_seg[d][4] = sv_bar(cont, X,         (H + t)/2,     t, vlen);     // E
    s_sv_seg[d][5] = sv_bar(cont, X,         t,             t, vlen);     // F
    s_sv_seg[d][6] = sv_bar(cont, X + t,     (H - t)/2,     W - 2*t, t);  // G
  }
  s_sv_colon[0] = sv_bar(cont, xc, H/3   - t/2, t, t);
  s_sv_colon[1] = sv_bar(cont, xc, 2*H/3 - t/2, t, t);

  s_sv_lastmin = -999; s_sv_lastcol = 0; s_sv_last4 = -1;   // force the first paint
  s_scr_saver = scr;                       // before saver_tick — it guards on this
  saver_tick();
  lv_scr_load(scr);
}

static void saver_close(){
  if(!s_scr_saver) return;
  lv_obj_t* s = s_scr_saver;
  s_scr_saver = nullptr;       // saver_tick guards on this, so the seg pointers can't be used stale
  go_home();                   // also clears any detail/now/settings screens left behind it
  lv_obj_del_async(s);         // we're inside this screen's own click event — defer deletion
}

// ---------------- OTA: wireless firmware updates ----------------
// Two doors into the same flash-over-WiFi mechanism (both need WiFi up first; USB-C stays
// the recovery path if a bad image ever wedges the app):
//   1. ArduinoOTA — push from the Mac:  pio run -e waveshare-s3-43b-ota -t upload
//   2. ElegantOTA — open http://yoto-controller.local/update (or http://<board-ip>/update)
//      in any browser and drop in .pio/build/waveshare-s3-43b/firmware.bin
// Updates land in the spare app slot and only swap over on success, and they DON'T touch NVS,
// so the Yoto tokens survive. mDNS advertises the board as "yoto-controller.local".
static WebServer s_ota_web(80);

// "Loading new software" splash for firmware updates. The hard constraint: on the ESP32-S3 every
// flash write/erase momentarily DISABLES the MMU cache, and the LCD framebuffer is read from PSRAM
// THROUGH that cache, so the panel literally scans garbage for the whole transfer no matter what's
// in the FB ("screen goes nuts"). We can't hold a live image during the write. So instead: paint
// the message ONCE (this fires before the first flash write, while the cache is still on, so it
// renders clean), hold it briefly so it's readable, then BLANK the backlight (EXIO_BL_DISP) so the
// transfer runs on a clean dark screen instead of garbage. On success the board reboots and setup()
// turns the backlight back on; on failure ota_restore_display() brings it back (see onError/onEnd).
static lv_obj_t* s_scr_ota = nullptr;
static void ota_splash_and_blank(){
  if(!s_scr_ota){
    s_scr_ota = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(s_scr_ota, lv_color_hex(0xE5352B), 0);   // red hero bg
    lv_obj_clear_flag(s_scr_ota, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t* t = lv_label_create(s_scr_ota);
    lv_label_set_text(t, "Loading new software");
    lv_obj_set_style_text_font(t, &lv_font_montserrat_28, 0);
    lv_obj_set_style_text_color(t, lv_color_hex(0xFFFFFF), 0);
    lv_obj_align(t, LV_ALIGN_CENTER, 0, -18);

    lv_obj_t* sub = lv_label_create(s_scr_ota);
    lv_label_set_text(sub, "Please wait...");
    lv_obj_set_style_text_font(sub, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(sub, lv_color_hex(0xFFE3E0), 0);
    lv_obj_align(sub, LV_ALIGN_CENTER, 0, 28);

    lv_scr_load(s_scr_ota);
  }
  lv_refr_now(lv_disp_get_default());   // clean paint: runs before the first flash write
  delay(1200);                          // hold long enough to read
  ch422g_set(EXIO_BL_DISP, false);      // blank: dark beats garbage during the cache-disabling writes
}
// Recover the display if an OTA FAILS (no reboot happens, loop() resumes — and its 1 Hz backlight
// self-heal would otherwise keep re-asserting the now-OFF shadow bit, leaving the screen dark).
static void ota_restore_display(){
  ch422g_set(EXIO_BL_DISP, true);
  if(s_scr_home) lv_scr_load(s_scr_home);
  lv_refr_now(lv_disp_get_default());
}

static void ota_begin(){
  ArduinoOTA.setHostname("yoto-controller");
  if(sizeof(OTA_PASSWORD) > 1) ArduinoOTA.setPassword(OTA_PASSWORD);   // empty string => no auth
  ArduinoOTA.onStart([](){ Serial.println("[OTA] push start"); ota_splash_and_blank(); });
  ArduinoOTA.onEnd([](){   Serial.println("[OTA] push done");  });   // success -> board reboots
  ArduinoOTA.onError([](ota_error_t e){ Serial.printf("[OTA] error %u\n", e); ota_restore_display(); });
  ArduinoOTA.begin();

  s_ota_web.on("/", [](){ s_ota_web.send(200, "text/plain",
      "Yoto Controller. Wireless firmware update at /update"); });
  // Telemetry for tuning the draw buffer vs internal-RAM headroom. cards>0 confirms the
  // library loaded; minFree is the boot-time low-water mark (worst dip during TLS handshakes).
  s_ota_web.on("/heap", [](){
    char b[240];
    snprintf(b, sizeof(b),
      "{\"drawLines\":%d,\"cards\":%d,\"freeInternal\":%u,\"largestInternal\":%u,\"minFreeInternal\":%u,\"tlsArena\":%u,\"pcCursor\":%d,\"pcDone\":%d}",
      DRAW_LINES, s_card_count,
      (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
      (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
      (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
      (unsigned)tlsarena::s_size, s_pc_cursor, s_pc_done ? 1 : 0);
    s_ota_web.send(200, "application/json", b);
  });
  // Display-flicker probe. GET /flick?stress=SECONDS injects heavy PSRAM bus load for that long
  // (0..120) so we can SEE whether the left-edge static flicker is PSRAM-bandwidth contention.
  // Watch the panel: edge worsens under load + recovers after = RGB-DMA underrun confirmed.
  // No-arg GET just reports the live display config + remaining stress time + free RAM.
  s_ota_web.on("/flick", [](){
    if(!s_flick_task)
      xTaskCreatePinnedToCore(flick_stress_task, "flick", 4096, nullptr, 1, &s_flick_task, 0);
    if(s_ota_web.hasArg("stress")){
      int sec = s_ota_web.arg("stress").toInt();
      if(sec < 0) sec = 0; if(sec > 120) sec = 120;
      s_flick_until = millis() + (uint32_t)sec*1000u;
    }
    int32_t left = (int32_t)(s_flick_until - millis()); if(left < 0) left = 0;
    char b[256];
    snprintf(b, sizeof(b),
      "{\"bouncePx\":%d,\"pclkHz\":%d,\"fbInPsram\":1,\"numFbs\":1,\"drawLines\":%d,"
      "\"stressMsLeft\":%d,\"freeSpiram\":%u,\"largestSpiram\":%u,\"freeInternal\":%u}",
      LCD_H_RES*10, 16*1000*1000, DRAW_LINES, (int)left,
      (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
      (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
      (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    s_ota_web.send(200, "application/json", b);
  });
  // SD cache health: is the card mounted, and is the cover/chapter cache actually populating?
  // Lists the first handful of files in /ythumbs with sizes so we can SEE whether detail
  // opens are writing {id}.chap (chapters) and {id}_184.565 (detail cover) blobs.
  s_ota_web.on("/sd", [](){
    String out = "{\"mounted\":" + String(s_sd_ok ? 1 : 0);
    if(s_sd_ok){
      out += ",\"type\":" + String((int)SD.cardType());
      out += ",\"sizeMB\":" + String((uint32_t)(SD.cardSize()/(1024ULL*1024ULL)));
      out += ",\"usedMB\":" + String((uint32_t)(SD.usedBytes()/(1024ULL*1024ULL)));
      int nchap=0, ncover=0, ntot=0;
      String sample = "[";
      File dir = SD.open(SD_DIR);
      if(dir){
        for(File f = dir.openNextFile(); f; f = dir.openNextFile()){
          String nm = f.name();
          if(nm.endsWith(".chap")) nchap++;
          else if(nm.endsWith(".565")) ncover++;
          if(ntot < 8){ if(ntot) sample += ","; sample += "\"" + nm + ":" + String((uint32_t)f.size()) + "\""; }
          ntot++;
          f.close();
        }
        dir.close();
      }
      sample += "]";
      out += ",\"files\":" + String(ntot) + ",\"chap\":" + String(nchap) +
             ",\"cover\":" + String(ncover) + ",\"sample\":" + sample;
    }
    out += "}";
    s_ota_web.send(200, "application/json", out);
  });
  // Live MQTT state probe: connection health, parsed playback snapshot, and the LAST RAW
  // event payload verbatim (the `event` field is the real wire JSON, embedded as-is) so the
  // parser in yoto_mqtt.h can be confirmed/corrected against what the player actually sends.
  // Play a card from the iOS app or the player's buttons, then curl this to see it land.
  s_ota_web.on("/mqtt", [](){
    if(s_ota_web.hasArg("kick")) ymqtt::kick();   // force reconnect+refresh to exercise stack peak
    ymqtt::Live lv; ymqtt::snapshot(lv);
    const char* raw = ymqtt::last_raw();
    String out = "{";
    out += "\"connected\":" + String(ymqtt::connected() ? 1 : 0);
    out += ",\"supHWM\":" + String(ymqtt::sup_hwm());
    out += ",\"events\":"   + String(ymqtt::events());
    out += ",\"errors\":"   + String(ymqtt::errors());
    out += ",\"seq\":"      + String(lv.seq);
    out += ",\"online\":"   + String(lv.online ? 1 : 0);
    out += ",\"cardId\":\"" + String(lv.cardId) + "\"";
    out += ",\"chapterKey\":\"" + String(lv.chapterKey) + "\"";
    out += ",\"status\":"   + String((int)lv.status);   // 0 unknown 1 stopped 2 paused 3 playing
    out += ",\"pos\":"      + String(lv.position);
    out += ",\"dur\":"      + String(lv.trackLength);
    out += ",\"vol\":"      + String(lv.volume);
    out += ",\"ageMs\":"    + String(lv.ts ? (millis() - lv.ts) : 0);
    out += ",\"topic\":\""  + String(ymqtt::last_topic()) + "\"";
    out += ",\"event\":";   out += (raw && raw[0]) ? raw : "null";
    out += "}";
    s_ota_web.send(200, "application/json", out);
  });
  // Screensaver-arming diagnostics: poll this hands-off and watch inactiveMs climb toward
  // saverMin*60000. If it resets with nobody touching the panel, touchSamples/lastTouchMs
  // will show whether the GT911 reported a phantom press (LVGL resets its inactivity clock
  // on every PRESSED sample). Reads shared state from loop() context — same thread as
  // lv_timer_handler, so no race.
  s_ota_web.on("/saver", [](){
    // resetReason (esp_reset_reason_t): 1 POWERON, 3 SW, 4 PANIC, 5 INT_WDT, 6 TASK_WDT,
    // 7 other WDT, 9 BROWNOUT — the key forensic bit for the 2026-06-10 reboot loop
    char b[360];
    snprintf(b, sizeof(b),
      "{\"uptimeMs\":%lu,\"inactiveMs\":%lu,\"saverMin\":%d,\"haveHome\":%d,\"saverShown\":%d,"
      "\"touchSamples\":%lu,\"lastTouchMs\":%lu,\"tstate\":%d,\"resetReason\":%d,"
      "\"blRecoveries\":%lu,\"freeInternal\":%u,\"minFreeInternal\":%u}",
      (unsigned long)millis(), (unsigned long)lv_disp_get_inactive_time(NULL),
      cfg::saver_min, s_scr_home ? 1 : 0, s_scr_saver ? 1 : 0,
      (unsigned long)s_touch_samples, (unsigned long)s_tlast, (int)s_tstate,
      (int)esp_reset_reason(),
      (unsigned long)s_bl_recoveries,
      (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
      (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    s_ota_web.send(200, "application/json", b);
  });
  // Live probe of the detail-screen fetch paths (authed /card/{id} + CDN cover), since the
  // failures only reproduce on-device. ?card=N picks a grid index; ?expire=1 corrupts the
  // cached access token first so the 403-refresh-retry path runs NOW instead of in an hour.
  // Blocks the UI for a few seconds while it runs — it's a diagnostic, not a feature.
  s_ota_web.on("/diag", [](){
    String out = "{\"freeInt\":" + String(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)) +
                 ",\"largest\":" + String(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)) +
                 ",\"arena\":" + String(tlsarena::s_size);
    size_t hole;
    {
      // what mbedTLS actually sees when the arena opens for a call (no String growth in
      // here — an alloc landing inside the open hole would split it permanently)
      tlsarena::Guard g;
      hole = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL);
    }
    out += ",\"holeLargest\":" + String(hole);
    if(s_card_count == 0){
      out += ",\"err\":\"no cards loaded\"}";
      s_ota_web.send(200, "application/json", out);
      return;
    }
    int i = s_ota_web.hasArg("card") ? s_ota_web.arg("card").toInt() : 0;
    if(i < 0 || i >= s_card_count) i = 0;
    Card& c = s_cards[i];
    out += ",\"id\":\"" + c.id + "\"";
    if(s_ota_web.arg("expire") == "1"){
      yoto::s_access = "expired-on-purpose";   // NOT persisted; next refresh overwrites
      out += ",\"forcedExpiry\":true";
    }
    int apiCode; size_t blen = 0; uint32_t apiMs;
    {
      tlsarena::Guard tg;
      WiFiClientSecure cli; HTTPClient http;
      uint32_t t0 = millis();
      apiCode = yoto::get(cli, http, "/card/" + c.id);
      if(apiCode == 200){ char* b = read_body_psram(http, blen); if(b) free(b); }
      if(apiCode > 0) http.end();
      apiMs = millis() - t0;
    }
    out += ",\"api\":" + String(apiCode) + ",\"apiBytes\":" + String((unsigned)blen) +
           ",\"apiMs\":" + String(apiMs);
    out += ",\"freeIntMid\":" + String(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)) +
           ",\"largestMid\":" + String(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    {
      // Plain-HTTP reachability (any code > 0 = DNS+TCP fine, isolating the failure to TLS)
      WiFiClient pc; HTTPClient ph;
      ph.begin(pc, "http://api.yotoplay.com/");
      ph.setTimeout(8000);
      out += ",\"plainHttp\":" + String(ph.GET());
      ph.end();
    }
    bool tlsOk; char eb[120] = "";
    {
      // Raw TLS connect + the mbedtls error string ("memory allocation failed" vs DNS etc.)
      tlsarena::Guard tg;
      WiFiClientSecure tc; tc.setInsecure();
      tlsOk = tc.connect("api.yotoplay.com", 443);
      if(!tlsOk) tc.lastError(eb, sizeof(eb));
      tc.stop();
    }
    out += ",\"tlsConnect\":" + String(tlsOk ? 1 : 0) + ",\"tlsErr\":\"" + String(eb) + "\"";
    if(s_ota_web.hasArg("status")){
      // Probe GET /device-v2/{id}/status with the board's CURRENT token — settles whether
      // live device state needs any extra scope or works with what we already hold.
      int stCode; String snip;
      {
        tlsarena::Guard tg;
        WiFiClientSecure cli; HTTPClient http;
        stCode = yoto::get(cli, http, "/device-v2/" + cfg::device_id + "/status");
        size_t sl = 0; char* sb = nullptr;
        if(stCode == 200){ sb = read_body_psram(http, sl); }
        if(stCode > 0) http.end();
        if(sb){ snip = String(sb).substring(0, 220); free(sb); }
      }
      snip.replace("\\", ""); snip.replace("\"", "'");   // keep the diag JSON parseable
      out += ",\"status\":" + String(stCode) + ",\"statusBody\":\"" + snip + "\"";
    }
    if(s_ota_web.hasArg("daily")){
      // Reproduce the exact Yoto Daily play path and surface what the firmware throws away:
      // the resolved trackKey AND the card/start response code+body. Settles whether the
      // failure is a bad/absent trackKey or the API rejecting a card/start for 3nC80.
      out += ",\"dailyId\":\"" + String(YOTO_DAILY_ID) + "\"";
      struct tm tmv;
      if(!getLocalTime(&tmv, 500)){
        out += ",\"dailyErr\":\"clock not synced\"";
      } else {
        char today[9]; strftime(today, sizeof today, "%Y%m%d", &tmv);
        out += ",\"today\":\"" + String(today) + "\"";
        String key, title; int dur = 0;
        bool found = fetch_daily_track(String(today), key, title, dur);
        out += ",\"dailyFound\":" + String(found ? 1 : 0) +
               ",\"trackKey\":\"" + key + "\",\"trackDur\":" + String(dur);
        if(found){
          // identical payload to send_cmd("play", {...}) for the daily tile
          String payload = String("{\"uri\":\"https://yoto.io/") + YOTO_DAILY_ID +
                           "\",\"chapterKey\":\"daily\",\"trackKey\":\"" + key + "\"}";
          String path = String("/device-v2/") + cfg::device_id + "/command/card/start";
          String rb; int sc;
          { tlsarena::Guard tg; sc = yoto::post(path, payload, rb); }
          rb.replace("\\", ""); rb.replace("\"", "'");   // keep diag JSON parseable
          out += ",\"startCode\":" + String(sc) + ",\"startBody\":\"" + rb.substring(0, 220) + "\"";
        }
      }
    }
    {
      uint8_t* buf = (uint8_t*)heap_caps_malloc(DCOVER_SZ, MALLOC_CAP_SPIRAM);
      bool ok = false;
      s_thumb_diag = "";
      uint32_t t0 = millis();
      if(buf){ ok = fetch_cover(c.cover, DCOVER_W, DCOVER_H, buf); free(buf); }
      else s_thumb_diag = "psram alloc fail";
      out += ",\"cover\":" + String(ok ? 1 : 0) + ",\"coverDiag\":\"" + s_thumb_diag +
             "\",\"coverMs\":" + String(millis() - t0);
    }
    out += "}";
    s_ota_web.send(200, "application/json", out);
  });
  s_ota_web.on("/bl", [](){
    // Manually re-assert the CH422G outputs (EXIO2 = backlight + DISP enable). If the screen is
    // black and hitting this brings it back, the expander's output latch was cleared by a supply
    // glitch (the ESP rides through the dip, so it never reboots) — not a dead backlight rail.
    // Full re-init (WR_SET + settle + WR_IO) so it recovers even a complete chip reset; the WR_SET
    // write momentarily blips EXIO1 (touch reset), acceptable for a manual one-shot diagnostic.
    // (The periodic keepalive in loop() deliberately does WR_IO only — see the note there.)
    ch422g_write(CH422G_WR_SET, 0x01);
    delay(20);
    ch422g_flush();
    s_ota_web.send(200, "application/json",
      String("{\"reasserted\":1,\"ioShadow\":") + String(s_io_shadow) + "}");
  });
  portal::begin(s_ota_web);   // /settings + /reboot ride the same server (see web_portal.h)
  if(sizeof(OTA_PASSWORD) > 1) ElegantOTA.setAuth("admin", OTA_PASSWORD);
  ElegantOTA.onStart([](){ Serial.println("[OTA] web upload start"); ota_splash_and_blank(); });
  ElegantOTA.onEnd([](bool ok){ Serial.printf("[OTA] web upload %s\n", ok ? "ok" : "FAILED"); if(!ok) ota_restore_display(); });
  ElegantOTA.begin(&s_ota_web);
  s_ota_web.begin();
}

// Bring up time + tokens + library once WiFi is available. Runs at boot when WiFi connects
// right away, and again from the retry timer if it only shows up later (router reboot,
// hotspot reconfiguration, transient outage).
static void net_init_and_fetch(){
  if(!s_net_ready){
    configTzTime(cfg::tz.c_str(), "pool.ntp.org", "time.nist.gov");   // screensaver clock
    yoto::refresh();                        // mint a fresh access token before the first call
    s_net_ready = true;
  }
  if(!s_lib_ok){
    status("Fetching library...");
    if(fetch_library()){ build_grid(); load_thumbs(); s_lib_ok = true; }
  }
  // Live device state over MQTT — start once the library is in (so event cardIds resolve to
  // grid tiles) and a token exists. begin() self-guards against a second start.
  if(s_lib_ok) ymqtt::begin(cfg::device_id);
  if(s_lib_ok && (WiFi.getMode() & WIFI_MODE_AP)){
    WiFi.softAPdisconnect(true);            // drop the setup hotspot once we're properly online
    WiFi.mode(WIFI_STA);
  }
}
static void net_retry_cb(lv_timer_t* t){
  if(s_lib_ok){ lv_timer_del(t); return; }
  if(WiFi.status() == WL_CONNECTED) net_init_and_fetch();
}

void setup(){
  Serial.begin(115200); Serial.setTxTimeoutMs(0);
  // Route mbedTLS's big allocations (the two ~17KB SSL buffers per connection) to PSRAM.
  // Internal RAM fragments once the UI is up and can no longer host them — every runtime
  // HTTPS call died with "SSL - Memory allocation failed" (chapters, covers, commands).
  // Small hot allocations (bignum math during the handshake) stay in internal RAM for speed.
  mbedtls_platform_set_calloc_free(tls_psram_calloc, free);
  cfg::load();                              // NVS-backed settings (secrets.h = defaults)

  // hardware: backlight + panel + touch
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000); delay(10);
  ch422g_write(CH422G_WR_SET, 0x01);
  s_io_shadow=(1u<<EXIO_BL_DISP)|(1u<<EXIO_TP_RST); ch422g_flush(); delay(20);
  ch422g_set(EXIO_LCD_RST,true); delay(120);
  rgb_panel_init();
  gt_init();

  // LVGL
  lv_init();
  size_t bufpx = LCD_H_RES*DRAW_LINES;
  s_buf1=(lv_color_t*)heap_caps_malloc(bufpx*sizeof(lv_color_t), MALLOC_CAP_INTERNAL);
  if(!s_buf1) s_buf1=(lv_color_t*)heap_caps_malloc(bufpx*sizeof(lv_color_t), MALLOC_CAP_SPIRAM);
  lv_disp_draw_buf_init(&s_draw_buf, s_buf1, NULL, bufpx);
  static lv_disp_drv_t dd; lv_disp_drv_init(&dd);
  dd.hor_res=LCD_H_RES; dd.ver_res=LCD_V_RES; dd.flush_cb=flush_cb; dd.draw_buf=&s_draw_buf;
  lv_disp_drv_register(&dd);
  static lv_indev_drv_t id; lv_indev_drv_init(&id);
  id.type=LV_INDEV_TYPE_POINTER; id.read_cb=touch_cb; lv_indev_drv_register(&id);
  lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xFBF7F0), 0);

  // MicroSD (cover cache) — best-effort. A write/read self-test proves the wiring; on failure
  // the app still runs, covers just fetch live each boot as before.
  if(sd_begin()){
    uint32_t mb = (uint32_t)(SD.cardSize() / (1024ULL * 1024ULL));
    const char* type = SD.cardType()==CARD_SDHC ? "SDHC" : SD.cardType()==CARD_SD ? "SD" : SD.cardType()==CARD_MMC ? "MMC" : "?";
    bool rw = false;                                   // round-trip a marker file
    File f = SD.open(SD_DIR "/.selftest", FILE_WRITE);
    if(f){ rw = (f.print("ok") == 2); f.close(); SD.remove(SD_DIR "/.selftest"); }
    Serial.printf("SD: mounted %s %u MB, used %llu MB, RW=%s\n",
                  type, mb, SD.usedBytes()/(1024ULL*1024ULL), rw ? "yes" : "NO");
    status((String("SD ") + type + "  " + mb + " MB  " + (rw ? "(cache ready)" : "(read-only?)")).c_str());
  } else {
    Serial.println("SD: not mounted — covers will fetch live each boot");
    status("SD not found - covers load over WiFi");
  }
  delay(700);                                          // let the self-test line be visible

  // WiFi — never dead-end here: if we can't join, raise a setup hotspot carrying the web
  // portal and keep retrying STA in the background. OTA + portal start either way, so the
  // board is ALWAYS wirelessly reachable (the old code returned on failure, which made a
  // wrong WiFi password recoverable only over USB at the wall).
  status("Connecting to WiFi...");
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("yoto-controller");
  WiFi.setAutoReconnect(true);
  WiFi.begin(cfg::wifi_ssid.c_str(), cfg::wifi_pass.c_str());
  uint32_t t0=millis();
  while(WiFi.status()!=WL_CONNECTED && millis()-t0 < 20000){ delay(200); }

  yoto::begin();                            // load tokens from NVS (seed refresh on first boot)
  lv_timer_create(tick_cb, 1000, NULL);     // 1 Hz heartbeat: progress/sleep timer/screensaver

  if(WiFi.status()==WL_CONNECTED){
    status((String("WiFi OK: ") + WiFi.localIP().toString() + "\nFetching library...").c_str());
    net_init_and_fetch();
    // (on fetch failure, status() already shows the reason; the retry timer keeps trying)
  } else {
    WiFi.mode(WIFI_AP_STA);                 // hotspot on top, STA keeps retrying underneath
    WiFi.softAP("Yoto-Setup");
    WiFi.begin(cfg::wifi_ssid.c_str(), cfg::wifi_pass.c_str());
    status(("WiFi not connected (2.4GHz only).\n\nJoin WiFi \"Yoto-Setup\" on a phone, open\nhttp://" +
            WiFi.softAPIP().toString() + "/settings\nand enter your network + password.").c_str());
  }
  lv_timer_create(net_retry_cb, 30000, NULL);   // self-deletes once the library is in

  // Start OTA LAST: the TLS handshakes to Yoto (refresh/library/covers above) are
  // internal-RAM hungry, and mDNS + the web server compete for that same RAM. Bringing
  // them up after the heavy boot fetch keeps the handshake headroom intact. OTA still
  // starts here even if the library failed, so a bad build is always recoverable wirelessly.
  ota_begin();
}

void loop(){
  ArduinoOTA.handle();        // cheap polls when idle; block only during an actual update
  s_ota_web.handleClient();
  ElegantOTA.loop();
  lv_timer_handler();

  // Warm the library cache during idle: only on the home screen or screensaver, and only after
  // a short lull, so the ~1s blocking fetch lands when nobody's mid-tap. Runs after
  // lv_timer_handler so any pending async screen deletes are already settled. Throttled to one
  // fetch every ~2.5s so internal RAM (shared with the concurrent MQTT TLS session) recovers
  // between handshakes instead of being held at the knife's edge by back-to-back fetches.
  static uint32_t s_pc_t = 0;
  if(s_lib_ok && s_sd_ok && !s_pc_done && millis() - s_pc_t > 2500){
    lv_obj_t* act = lv_scr_act();
    // Safety valve: only start a warming fetch with comfortable internal-RAM headroom. A TLS
    // handshake transiently consumes most of free internal RAM; if MQTT (or anything) already has
    // it tight, skip this round and let it recover rather than stacking a fetch on top and dipping
    // to a near-zero low-water. At-rest free is ~24-28KB, so a 16KB floor passes normally but
    // backs off during contention. (Rechecked next loop; this defers warming, never aborts it.)
    bool ram_ok = heap_caps_get_free_size(MALLOC_CAP_INTERNAL) > 16000;
    if(ram_ok && (act == s_scr_home || act == s_scr_saver) && lv_disp_get_inactive_time(NULL) > 1500){
      precache_step();
      s_pc_t = millis();
    }
  }
  // Backlight/DISP self-heal. A dip from the 12V supply can disturb the CH422G I2C expander —
  // EXIO2 (EXIO_BL_DISP) drives the backlight + panel DISP enable, EXIO1 (EXIO_TP_RST) the GT911
  // reset — while the higher-headroom ESP rides through and never reboots, so the screen goes
  // black with no reset. Two failure modes seen on-device:
  //   (1) output LATCH cleared  -> a bare WR_IO re-write (ch422g_flush) restores it. Touch-safe,
  //       so we do it every second while healthy.
  //   (2) full CHIP reset (output-enable config lost) -> WR_IO alone does nothing; the chip needs
  //       WR_SET re-issued. But WR_SET momentarily drops EVERY expander line (backlight blink +
  //       touch reset + panel reset), so it must NOT run on a blind timer — that was what killed
  //       touch when the keepalive re-issued it every second. Instead we DETECT it: a reset holds
  //       EXIO1 low, so the GT911 stops ACKing on I2C. On two consecutive missed probes we run the
  //       full re-init (WR_SET + shadow + gt_init for the just-reset touch chip). The RGB panel
  //       self-recovers once LCD_RST is driven high again (proven by the /bl endpoint).
  // NOTE: this recovers AFTER each brownout; it does not stop the rail from dipping. The real fix
  // is an adequate/regulated 12V supply (or USB-C 5V).
  static uint32_t s_bl_t = 0;
  static uint8_t  s_bl_fail = 0;
  if(millis() - s_bl_t > 1000){
    s_bl_t = millis();
    uint8_t id[4];
    if(gt_read(0x8140, id, 4)){          // GT911 reachable -> expander config intact
      s_bl_fail = 0;
      ch422g_flush();                    // refresh the output latch (covers a latch-only BL drop)
    } else if(++s_bl_fail >= 2){         // two consecutive misses -> the expander fully reset
      s_bl_fail = 0;
      ch422g_write(CH422G_WR_SET, 0x01); delay(20);                    // re-enable push-pull outputs
      s_io_shadow = (1u<<EXIO_BL_DISP)|(1u<<EXIO_TP_RST)|(1u<<EXIO_LCD_RST);
      ch422g_flush(); delay(20);                                       // backlight/DISP + resets high
      gt_init();                                                       // re-init the just-reset GT911
      s_bl_recoveries++;
    }
  }
  delay(5);
}
