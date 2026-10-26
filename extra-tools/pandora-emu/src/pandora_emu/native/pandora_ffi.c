/*
 * pandora_ffi.c - Thin, opaque C wrapper around the AGNOSTIC EFM32 emulator core
 * so the Python side can drive it over a trivial ctypes ABI (libpandora.so).
 *
 * The emulator core (emulator.c) is now AGNOSTIC of the firmware: there is ONE
 * hardware profile (PROFILE_PANDORA_HW), flash_used is DERIVED from the firmware
 * image length, and the unlock knock-code is FIRMWARE CONFIG provided by the host
 * (pe_set_knock). The SAME libpandora.so runs ANY .hex for this MCU.
 *
 * Flat function surface (opaque void* handle):
 *
 *     void*  pe_new(int profile);                     create+zero a handle
 *                                                     (profile IGNORED; HW is
 *                                                      single/agnostic -- kept for
 *                                                      ABI compat)
 *     int    pe_load_hex_file(void* h, const char*);  parse .hex -> flat -> init
 *     int    pe_load_flat(void* h, const uint8_t*, size_t);  flat image -> init
 *     void   pe_reset_unlock(void* h);                boot + (optional) knock
 *     void   pe_run(void* h, uint32_t insns);         run one burst
 *     void   pe_button(void* h, int btn, int pressed) raw button level
 *     int    pe_nav(void* h, int btn, int hold);      GPIO button press (no PC-sync)
 *     void   pe_get_fb(void* h, uint8_t* dst1472);    copy VISIBLE framebuffer
 *     int    pe_tx_pop(void* h, int* level, uint32_t* us);  pop one TX edge
 *     void   pe_rx_inject(void* h, int level, uint32_t us); inject one RX edge
 *     void   pe_set_knock(void* h, const uint8_t* seq, int len, int gate);
 *     int    pe_unlocked(void* h);                     OBSERVABLE unlock signal
 *     int    pe_flash_used(void* h);                   DERIVED flash size (post-load)
 *     uint32_t pe_rf_freq(void* h);                    Si4432 carrier Hz
 *     int    pe_rf_mode(void* h);                      PROTO_RF_* (idle/ready/rx/tx)
 *     int    pe_rf_mod(void* h);                       PROTO_MOD_* (OOK/FSK/GFSK)
 *     int    pe_buzzer_event(void* h);                 1 if the fw toggled the buzzer
 *     const char* pe_profile_name(void* h);
 *     void   pe_free(void* h);
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#include "emulator.h"
#include "ihex.h"

/* Cap for the flat flash buffer used by the HEX loader. Generous (256 KB) so it
 * covers any firmware for this MCU; emu_init derives the real flash_used from the
 * highest written address. */
#define PE_FLAT_CAP (256u * 1024u)

/* Opaque handle: the EmuState (heap-owned so the pointer is stable across the FFI
 * boundary). The hardware is a single agnostic profile. */
typedef struct {
    EmuState* emu;
    const ArmProfile* prof;
    int inited;   /* 1 once emu_init has run (a firmware is loaded) */
} PeHandle;

/* ------------------------------------------------------------ lifecycle ----- */

/* The 'profile' argument is IGNORED: the hardware is single/agnostic. The
 * parameter is kept so the ABI (and existing Python callers) do not change. */
void* pe_new(int profile) {
    (void)profile;
    PeHandle* h = (PeHandle*)calloc(1, sizeof(PeHandle));
    if(!h) return NULL;
    h->emu = (EmuState*)calloc(1, sizeof(EmuState));
    if(!h->emu) {
        free(h);
        return NULL;
    }
    h->prof = &PROFILE_PANDORA_HW; /* the ONE hardware profile */
    h->inited = 0;
    return h;
}

void pe_free(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h) return;
    if(h->inited && h->emu) emu_deinit(h->emu);
    free(h->emu);
    free(h);
}

/* ------------------------------------------------------------ firmware ------- */

/* (Re)initialise the core from a flat firmware image. Returns 0 on success,
 * <0 on error (propagated from emu_init). Re-loading tears down the previous
 * core first so a handle can be re-used for a different firmware. */
int pe_load_flat(void* handle, const uint8_t* data, size_t len) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu) return -1;
    if(h->inited) {
        emu_deinit(h->emu);
        memset(h->emu, 0, sizeof(EmuState));
        h->inited = 0;
    }
    int rc = emu_init(h->emu, h->prof, data, (uint32_t)len);
    if(rc == 0) h->inited = 1;
    return rc;
}

/* Parse an Intel HEX file from disk into a flat buffer, then init the core with
 * the REAL image size (ihex highest address). AGNOSTIC: the buffer is sized to a
 * generous cap (not a per-firmware flash_used, which no longer exists); emu_init
 * derives flash_used from the highest address actually written. Returns 0 on
 * success, <0 on error (file / parse / init). */
int pe_load_hex_file(void* handle, const char* path) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !path) return -1;

    uint32_t cap = PE_FLAT_CAP;
    uint8_t* flat = (uint8_t*)malloc(cap);
    if(!flat) return -2;
    memset(flat, 0xFF, cap); /* erased-flash / open-bus filler (per ihex.h) */

    FILE* f = fopen(path, "rb");
    if(!f) {
        free(flat);
        return -3;
    }

    ihex_parser_t p;
    ihex_init(&p, flat, cap);

    /* Stream the file in chunks through the incremental parser. */
    uint8_t buf[4096];
    size_t n;
    int prc = IHEX_OK;
    while((n = fread(buf, 1, sizeof(buf), f)) > 0) {
        prc = ihex_feed(&p, buf, (uint32_t)n);
        if(prc != IHEX_OK) break;
    }
    fclose(f);

    if(prc != IHEX_OK) {
        free(flat);
        return -4;
    }

    /* Init with the REAL firmware length (highest flat address written), so
     * emu_init derives flash_used from it. Fall back to cap if nothing parsed. */
    int rc = pe_load_flat(handle, flat, p.highest ? p.highest : cap);
    free(flat); /* emu_init copied the bytes into its own RAM-resident flash */
    return rc;
}

/* ------------------------------------------------------------ run/control --- */

void pe_reset_unlock(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return;
    emu_reset_and_unlock(h->emu);
}

void pe_run(void* handle, uint32_t insns) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return;
    emu_run_burst(h->emu, insns);
}

void pe_button(void* handle, int btn, int pressed) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return;
    emu_set_button(h->emu, btn, pressed ? true : false);
}

/* A navigation button press over GPIO (press + hold + release). AGNOSTIC: pure
 * GPIO, no PC-sync. hold!=0 -> long press. Returns 1 if completed. */
int pe_nav(void* handle, int btn, int hold) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return emu_press_button(h->emu, btn, hold ? true : false) ? 1 : 0;
}

/* Set the unlock knock-code (FIRMWARE CONFIG, provided by the host). seq = array
 * of B_* button indices, len = its length (0 disables auto-unlock), gate = B_*
 * index of the final held gate button (<0 = none). Call before pe_reset_unlock. */
void pe_set_knock(void* handle, const uint8_t* seq, int len, int gate) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return;
    if(len < 0) len = 0;
    emu_set_knock(h->emu, seq, (uint8_t)len, gate);
}

/* ------------------------------------------------------------ framebuffer --- */

void pe_get_fb(void* handle, uint8_t* dst) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited || !dst) return;
    emu_get_fb(h->emu, dst); /* OLED_FB_SIZE (1472) bytes */
}

/* Render the visible framebuffer as ASCII art into `out` (cap bytes). Returns
 * the number of bytes written. Lets tooling "read" the screen for RE. */
size_t pe_fb_ascii(void* handle, char* out, size_t cap) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited || !out || cap == 0) return 0;
    return emu_fb_ascii(h->emu, out, cap);
}

/* ------------------------------------------------------------ radio ---------- */

/* Pop one captured TX edge (firmware bit-banged PB3). Returns 1 if an edge was
 * written to the level/us out-params, 0 if the ring is empty. */
int pe_tx_pop(void* handle, int* level, uint32_t* us) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    RfEdge e;
    if(!emu_tx_pop_edge(h->emu, &e)) return 0;
    if(level) *level = e.level ? 1 : 0;
    if(us) *us = e.duration;
    return 1;
}

/* Inject one RX edge into the firmware demodulator: real PB0 edge + TIMER1 CNT
 * pulse-width capture (the demod's stopwatch). */
void pe_rx_inject(void* handle, int level, uint32_t us) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return;
    emu_rx_inject_edge(h->emu, level ? true : false, us);
}

/* Radio state the firmware configured on the Si4432, so the HackRF bridge can
 * follow it (the core computes these on each Si4432 register write). */
uint32_t pe_rf_freq(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0u;
    return h->emu->rf_freq_hz;
}

int pe_rf_mode(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return (int)h->emu->rf_mode; /* PROTO_RF_* */
}

int pe_rf_mod(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return (int)h->emu->rf_mod; /* PROTO_MOD_* */
}

/* Consume the buzzer-activity flag (1 if the firmware toggled the buzzer GPIO
 * since the last call). Lets the frontend beep. */
int pe_buzzer_event(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    int ev = h->emu->buzzer_event ? 1 : 0;
    h->emu->buzzer_event = false;
    return ev;
}

/* ------------------------------------------------------------ introspection - */

int pe_unlocked(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return h->emu->unlocked ? 1 : 0;
}

/* The DERIVED flash size (valid AFTER a firmware is loaded). Before load it is
 * 0. No longer a per-profile constant. */
int pe_flash_used(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return (int)h->emu->flash_used;
}

const char* pe_profile_name(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->prof) return "";
    return h->prof->name; /* always "pandora-d650-hw" (agnostic) */
}

/* Framebuffer geometry, so Python never hard-codes the numbers. */
int pe_fb_size(void) { return OLED_FB_SIZE; }      /* 1472 */
int pe_fb_cols(void) { return OLED_COLS; }         /* 92 */
int pe_fb_visible_cols(void) { return OLED_VISIBLE_COLS; } /* 88 */
int pe_fb_pages(void) { return OLED_PAGES; }       /* 16 */
