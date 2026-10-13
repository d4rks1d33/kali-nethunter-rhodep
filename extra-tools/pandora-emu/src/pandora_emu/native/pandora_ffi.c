/*
 * pandora_ffi.c - Thin, opaque C wrapper around the EFM32 emulator core so the
 * Python side can drive it over a trivial ctypes/cffi ABI.
 *
 * The emulator core (emulator.c) exposes a large EmuState struct and the
 * ArmProfile symbols. Rather than mirror that struct in Python, we hide it
 * behind an opaque handle (void*) and offer a flat function surface:
 *
 *     void*  pe_new(int profile);                     create+zero a handle
 *     int    pe_load_hex_file(void* h, const char*);  parse .hex -> flat -> init
 *     int    pe_load_flat(void* h, const uint8_t*, size_t);  flat image -> init
 *     void   pe_reset_unlock(void* h);                boot + auto-unlock
 *     void   pe_run(void* h, uint32_t insns);         run one burst
 *     void   pe_button(void* h, int btn, int pressed) raw button level
 *     int    pe_nav(void* h, int btn, int hold);      synchronized nav press
 *     void   pe_get_fb(void* h, uint8_t* dst1472);    copy VISIBLE framebuffer
 *     int    pe_tx_pop(void* h, int* level, uint32_t* us);  pop one TX edge
 *     void   pe_rx_inject(void* h, int level, uint32_t us); inject one RX edge
 *     int    pe_unlocked(void* h);                     unlock flag after boot
 *     int    pe_flash_used(void* h);                   profile flash window
 *     const char* pe_profile_name(void* h);
 *     void   pe_free(void* h);
 *
 * A handle owns one heap-allocated EmuState plus whatever the core malloc'd for
 * it (flash, ram, cpu). pe_free tears all of that down.
 *
 * Profiles: 0 = pandora, 1 = pandoramax (anything else -> pandora).
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#include "emulator.h"
#include "ihex.h"

/* Opaque handle: the EmuState plus the profile we initialised it with. The
 * EmuState itself is heap-owned so the handle is a stable pointer across the
 * FFI boundary. */
typedef struct {
    EmuState* emu;
    const ArmProfile* prof;
    int inited;   /* 1 once emu_init has run (a firmware is loaded) */
} PeHandle;

static const ArmProfile* pick_profile(int profile) {
    return (profile == 1) ? &PROFILE_PANDORAMAX : &PROFILE_PANDORA;
}

/* ------------------------------------------------------------ lifecycle ----- */

void* pe_new(int profile) {
    PeHandle* h = (PeHandle*)calloc(1, sizeof(PeHandle));
    if(!h) return NULL;
    h->emu = (EmuState*)calloc(1, sizeof(EmuState));
    if(!h->emu) {
        free(h);
        return NULL;
    }
    h->prof = pick_profile(profile);
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

/* Parse an Intel HEX file from disk straight into a flat buffer sized to the
 * profile's flash window, then init the core with it. Returns 0 on success,
 * <0 on error (file / parse / init). */
int pe_load_hex_file(void* handle, const char* path) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !path) return -1;

    uint32_t cap = h->prof->flash_used;
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

/* Synchronized nav press POST-unlock (press + hold + release, aligned with the
 * firmware's nav_reader). hold!=0 -> long press. Returns 1 if completed. */
int pe_nav(void* handle, int btn, int hold) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return emu_nav_button(h->emu, btn, hold ? true : false) ? 1 : 0;
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

/* Inject one RX edge into the firmware demodulator (PB0). */
void pe_rx_inject(void* handle, int level, uint32_t us) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return;
    emu_rx_inject_edge(h->emu, level ? true : false, us);
}

/* ------------------------------------------------------------ introspection - */

int pe_unlocked(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->emu || !h->inited) return 0;
    return h->emu->unlocked ? 1 : 0;
}

int pe_flash_used(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->prof) return 0;
    return (int)h->prof->flash_used;
}

const char* pe_profile_name(void* handle) {
    PeHandle* h = (PeHandle*)handle;
    if(!h || !h->prof) return "";
    return h->prof->name;
}

/* Framebuffer geometry, so Python never hard-codes the numbers. */
int pe_fb_size(void) { return OLED_FB_SIZE; }      /* 1472 */
int pe_fb_cols(void) { return OLED_COLS; }         /* 92 */
int pe_fb_visible_cols(void) { return OLED_VISIBLE_COLS; } /* 88 */
int pe_fb_pages(void) { return OLED_PAGES; }       /* 16 */
