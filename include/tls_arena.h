#pragma once
// TLS heap arena — fixes "SSL - Memory allocation failed" on every HTTPS call after boot.
//
// Why: mbedTLS needs two ~17KB CONTIGUOUS internal-RAM buffers per TLS connection. The heap
// is contiguous enough at boot (library + covers fetch fine), but once the UI is up (49 grid
// tiles, labels, Strings, WebServer) internal RAM is fragmented: ~62KB free total with only
// ONE ~29KB block — the second TLS buffer can't be placed, so every runtime fetch fails
// instantly (chapters, detail covers, playback commands). Diagnosed live via /diag: plain
// HTTP 301 OK, WiFiClientSecure lastError = "SSL - Memory allocation failed".
//
// Fix: grab one contiguous block at boot while the heap is clean and HOLD it, so nothing
// else can fragment that region. Release it only for the duration of a TLS call (the hole
// is where mbedTLS lands), then re-grab. Guard is re-entrant (yoto::get refreshing mid-call
// nests fine) and best-effort on re-grab: if something nibbled the hole, take the biggest
// piece we can and try to grow back to full size on the next cycle.
#include <Arduino.h>
#include <esp_heap_caps.h>

namespace tlsarena {

static uint8_t* s_block = nullptr;
static size_t   s_size  = 0;
static int      s_depth = 0;
static bool     s_active = false;   // only reserve() arms the arena; Guards are inert otherwise
static constexpr size_t WANT  = 48 * 1024;   // ~40KB observed TLS peak + slack
static constexpr size_t FLOOR = 24 * 1024;   // below this, holding it helps less than hoping
static constexpr size_t STEP  =  4 * 1024;

inline void grab(){
  for(size_t sz = WANT; sz >= FLOOR; sz -= STEP){
    s_block = (uint8_t*)heap_caps_malloc(sz, MALLOC_CAP_INTERNAL);
    if(s_block){ s_size = sz; return; }
  }
  s_size = 0;
}

// DORMANT FALLBACK as of the mbedTLS-PSRAM fix (see tls_psram_calloc in main.cpp): routing
// the TLS buffers to PSRAM removed the internal-RAM pressure entirely, so reserve() is no
// longer called and every Guard is a no-op. Kept because the Guards also mark the TLS
// transaction boundaries, and one reserve() call at the top of setup() re-arms the whole
// mechanism if the PSRAM route ever misbehaves.
//
// (Measured while active: hole ≥~43KB succeeded, ~34-37KB was knife-edge — a TLS 1.3
// handshake peaks near 60KB total — and concurrent small allocations nibbled the released
// hole between cycles, degrading the reclaim. Workable but fragile; PSRAM is the real fix.)
inline void reserve(){ if(!s_active){ s_active = true; grab(); } }

// Scope this around a COMPLETE TLS transaction — connect through http.end(). The response
// stream reads from buffers living in the hole, so don't reclaim before the body is read.
// Close it BEFORE parsing/storing results: persistent allocations made while the hole is
// open land inside it and permanently split it.
struct Guard {
  Guard(){ if(s_depth++ == 0 && s_block){ free(s_block); s_block = nullptr; } }
  ~Guard(){ if(--s_depth == 0 && s_active && !s_block) grab(); }
};

} // namespace tlsarena
