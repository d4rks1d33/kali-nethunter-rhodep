/*
 * emulator.h - EFM32 Cortex-M3 hardware model for the Pandora D650 keyfob.
 *
 * AGNOSTIC CORE (G82 / mainline Linux port). This is the SAME agnostic hardware
 * model validated on the ESP32 backend, adapted to be driven directly from
 * Python via the ctypes FFI (libpandora.so) instead of a UART link. The core
 * provides ONLY the HARDWARE; the firmware does everything. The SAME core runs
 * ANY .hex for this MCU (Cortex-M3 / EFM32) -- there is NO per-firmware profile.
 *
 * KEY PROPERTIES (vs the old G82 core it replaces):
 *   - ONE hardware profile (PROFILE_PANDORA_HW): only physical pins/geometry, NO
 *     firmware code addresses (no scan_head/spin_heads/unlock_pc/nav_reader/
 *     demod_isr/fb_addr). PROFILE_PANDORA/PANDORAMAX are compat ALIASES of it.
 *   - flash_used is DERIVED from the firmware image length, not hardcoded.
 *   - real NVIC driven by the firmware's own ISER/ICER (nvic_enabled), dynamic
 *     VTOR (ppb_vtor, intercepted on write to 0xE000ED08), generic weak-handler
 *     detection (branch-to-self 0xE7FE), TIMER_IRQS={12,13,14}.
 *   - unlock is the OBSERVABLE framebuffer signal (emu_is_unlocked), not a PC.
 *   - RX injection is REAL: PB0 edge + TIMER1 CNT = us*6 (emu_rx_inject_edge).
 *   - knock-code is FIRMWARE CONFIG set by the host (emu_set_knock), pressed
 *     purely over GPIO at reset.
 *
 * FLASH IS RAM-RESIDENT: the whole firmware is malloc'd into one contiguous
 * block and passed to thumb_set_regions as a DIRECT flash region. The OLED is
 * captured into a double-buffered framebuffer read over the FFI. The Si4432
 * mode + a TX edge ring + an RX inject hook are exposed to the host so the
 * HackRF bridge can follow the firmware's radio.
 */
#ifndef PANDORA_EMULATOR_H
#define PANDORA_EMULATOR_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#include "thumb_core.h"
#include "protocol.h"

/* ------------------------------------------------------------ memory map ---- */
/* Emulated EFM32 (NOT the host). */
#define PA_FLASH_BASE 0x00000000u
#define PA_RAM_BASE 0x20000000u
#define PA_RAM_SIZE 0x00008000u /* 32 KB */

/* OLED geometry (keyfob UC16xx/ST7528): 92 columns x 16 pages, 2bpp. */
#define OLED_COLS 92
#define OLED_VISIBLE_COLS 88 /* physical glass width (cols 88..91 off-glass) */
#define OLED_PAGES 16
#define OLED_FB_SIZE (OLED_COLS * OLED_PAGES) /* 1472 */

#define SPI_RX_QUEUE_LEN 64

/* TX edge ring: edges captured from the firmware bit-banging PB3 (OOK/FSK).
 * Exposed to the host for the real HackRF to replay. */
#define TX_RING_LEN 512u

/* ------------------------------------------------------------ HW profile ----
 *
 * AGNOSTIC HARDWARE PROFILE.
 *
 * This struct describes ONLY the PHYSICAL hardware of the D650 keyfob, which is
 * IDENTICAL for every firmware of this model. It contains NO firmware-internal
 * code address (no scan_head / spin_heads / unlock_pc / nav_reader / demod_isr /
 * fb_addr / RAM offsets). All I/O is done through GPIO pins and the SPI bus, as
 * on the real hardware, so the SAME profile runs ANY .hex for this MCU.
 *
 * What used to be per-firmware (flash_used, knock-code) is handled agnostically:
 *   - flash size is derived from the firmware image length at emu_init
 *     (`top = max(code)+1`), NOT a hardcoded ArmProfile.flash_used.
 *   - the knock-code is FIRMWARE CONFIG, not hardware. It is provided by the host
 *     via emu_set_knock() as an AUTOTEST button sequence (pressed purely over
 *     GPIO). The agnostic core itself does not know nor need it.
 */
/* logical button indices (order matters: mirrored by PROTO_BTN_* bits). */
enum { B_UP = 0, B_DOWN, B_OK, B_BACK, B5, B6, BTN_COUNT };

typedef struct {
    const char* name;
    /* 6 physical buttons -> (port, pin) GPIO DIN (pull-up, active-low).
     * Identical in every firmware of this keyfob (verified by RE).
     * NOTE: BACK=(0,15). The old 'pandora' profile had BACK=(2,7) which was a
     * BUG: PC7=(2,7) is a vestigial OUTPUT, not a button. Fixed for all. */
    uint8_t up_port, up_pin;
    uint8_t down_port, down_pin;
    uint8_t ok_port, ok_pin;
    uint8_t back_port, back_pin;
    uint8_t b5_port, b5_pin;
    uint8_t b6_port, b6_pin;
    /* RF pins (Si4432 direct mode, bit-bang OOK/FSK) */
    uint8_t data_port, data_pin; /* RX-data (PB0 = port1.0) */
    uint8_t tx_data_port, tx_data_pin; /* TX-data (PB3 = port1.3) */
    uint8_t rf_cs_port, rf_cs_pin; /* Si4432 CS (PE9 = port4.9, active-high) */
    uint16_t rf_tx_cell_us; /* default OOK cell width (us), order-of-magnitude */
} ArmProfile;

/* The ONE hardware profile of the D650 keyfob. The core is AGNOSTIC: every
 * firmware .hex uses this single HW model. */
extern const ArmProfile PROFILE_PANDORA_HW;

/* COMPAT: legacy names still referenced by the FFI (pandora_ffi.c). Both alias
 * the SINGLE hardware profile above (there is no per-firmware profile any more). */
#define PROFILE_PANDORA PROFILE_PANDORA_HW
#define PROFILE_PANDORAMAX PROFILE_PANDORA_HW

/* ------------------------------------------------------------ Si4432 -------- */
typedef struct {
    uint8_t regs[0x80];
    uint8_t mode; /* 0 idle, 1 ready, 2 rx, 3 tx (PROTO_RF_*) */
} Si4432;

/* ------------------------------------------------------------ TX edge ------- */
typedef struct {
    bool level;
    uint32_t duration; /* us (approx) */
} RfEdge;

/* ------------------------------------------------------------ emulator ------ */
typedef struct EmuState {
    ThumbCore* cpu;
    const ArmProfile* prof;

    uint8_t* flash; /* firmware RAM-resident (flash_used bytes) */
    uint32_t flash_used;
    uint8_t* ram; /* 32KB EFM32 RAM */

    Si4432 si;
    /* SPI RX queue (Si4432 responses) */
    uint8_t spi_rx[SPI_RX_QUEUE_LEN];
    uint8_t spi_rx_head, spi_rx_tail;
    int spi_phase; /* -1 idle, 0 pending-after-addr */
    uint8_t spi_kind; /* 'w'/'r' */
    uint8_t spi_reg;

    /* GPIO */
    uint16_t gpio_din[16]; /* bit=1 released (pull-up) */
    uint16_t gpio_dout[16];

    /* timers (faithful free-running model) */
    uint64_t timer_t0[4];
    uint32_t timer_top[4];
    uint32_t timer_rate[4];
    uint32_t rx_pulse_ticks;
    bool rx_feeding, rx_primed;
    bool buzzer_event;

    /* minimal NVIC (agnostic: driven by the firmware's REAL ISER/ICER) */
    uint8_t in_irq;
    uint8_t irq_rr;
    /* Pending UI tick request. The reference (Unicorn pandora_tui) raises a
     * _pending_tick after EVERY run_burst from its button/boot drivers, which
     * forces one IRQ (timer) tick at the start of the next burst IN ADDITION to
     * the periodic one. That extra per-burst tick is what gives the firmware's UI
     * TIMER IRQ (IRQ13) its correct cadence; without it the C core fired IRQ13
     * ~30% less often than Unicorn, so post-unlock the firmware's screen state
     * machine stayed in the 3-scanner UI loop (never reading the OK/UP/DOWN
     * button ports) and navigation did nothing. Mirror the oracle exactly. */
    uint8_t pending_tick;
    uint32_t tick_count;
    uint64_t last_tick_insn;
    /* Mask of IRQs the firmware has ENABLED right now (NVIC ISER set / ICER
     * clear). 64 bits so IRQ numbers up to ~63 are covered (this MCU uses up to
     * ~IRQ36). Bit N = IRQ N. */
    uint64_t nvic_enabled;
    /* SCB->VTOR (0xE000ED08): the active vector table base the firmware installs
     * at runtime (0x8100 flash-XIP for pandora, 0x20000000 RAM for pmax). Stored
     * here because the PPB has no backing memory in the core; cur_vtor reads it.
     * Agnostic: we honour whatever the firmware writes. */
    uint32_t ppb_vtor;

    /* WFI/WFE skip set: absolute addresses of WFI(0xBF30)/WFE(0xBF20) opcodes,
     * scanned agnostically from the code (flash + RAM), NOT a per-firmware
     * address. */
    uint32_t* wfi_set;
    uint32_t wfi_count;
    uint32_t wfi_cap;
    uint8_t wfi_ram_scanned; /* RAM rescanned once after boot (app-in-RAM case) */
    uint32_t wfi_pc_hint; /* first WFI addr (compat) */

    /* unlocked: AGNOSTIC observable signal. A locked keyfob shows a BLACK screen;
     * once unlocked it draws the UI. Derived from the captured framebuffer having
     * lit pixels (see emu_is_unlocked), NOT from a firmware code address. */
    uint8_t unlocked;
    int max_step; /* inert, kept for struct compat */

    /* buttons (logical mask) */
    uint8_t btn_pressed[BTN_COUNT];

    /* OLED double buffering */
    uint8_t oled_fb[OLED_FB_SIZE]; /* VISIBLE buffer (read over the FFI) */
    uint8_t oled_work[OLED_FB_SIZE]; /* WORK buffer (written byte-by-byte) */
    uint8_t oled_page;
    uint8_t oled_col;
    uint8_t oled_work_dirty;
    uint32_t oled_frames; /* commit counter (diagnostic) */

    /* RF bridge (Si4432 -> host / HackRF follow) */
    uint8_t rf_mode; /* PROTO_RF_* */
    uint8_t rf_mod; /* PROTO_MOD_* */
    uint32_t rf_freq_hz;

    /* TX capture ring (firmware bit-bangs PB3 -> edges) */
    RfEdge tx_ring[TX_RING_LEN];
    uint32_t tx_head, tx_tail;
    bool tx_capturing;
    bool tx_last_level;
    uint64_t tx_last_insn;

    uint64_t ninsn;
    uint32_t pc;
    uint32_t next_pc;

    /* knock-code (FIRMWARE CONFIG, set by the host via emu_set_knock). Pressed
     * purely over GPIO as an autotest at reset. The agnostic core does not know
     * nor need it; it is only a canned button sequence + a final held gate. */
    uint8_t knock_seq[16];
    uint8_t knock_len;
    int knock_gate; /* B_* index of the final held gate, or -1 for none */

    /* --- polling-aware button injection (robust, phase-independent) ---------
     * The firmware scans the button GPIO DIN ports by polling inside its main
     * loop. A press only registers if the pin is held LOW across the firmware's
     * sampling window. To guarantee that (regardless of phase), emu_press_button
     * runs the core in SHORT bursts while the pin is low and counts how many
     * times the firmware actually READ the DIN register of the button's port.
     * Only once the firmware has sampled the low pin >=K times do we release,
     * then run until it has sampled the released pin >=J times. These counters
     * observe ONLY the DIN MMIO read of the relevant port (generic hardware), no
     * firmware code address. din_watch_port=-1 disables counting. */
    int din_watch_port;        /* port whose DIN reads are counted (-1 = off) */
    uint32_t din_read_count;   /* DIN reads of din_watch_port since last reset */

    /* --- GPIO EXTI (external interrupt) delivery ---------------------------
     * Both firmwares read the nav buttons via the EFM32 GPIO external interrupt
     * (GPIO_EVEN=IRQ1 even pins, GPIO_ODD=IRQ11 odd pins), NOT by polling ports
     * 2/5. The post-unlock UI loop only polls port 0 directly; the OK/UP/DOWN
     * edges reach the firmware through its GPIO ISR. The core must therefore
     * raise the GPIO IRQ on a real button pin edge, or those presses are lost
     * (the erratic navigation). pending_gpio_irq!=0 asks the scheduler to enter
     * the GPIO ISR; gpio_if is the latched EXTI interrupt-flag register the ISR
     * reads at 0x40006114 (IF) to learn WHICH line fired. Agnostic: we only
     * observe button pin edges + the firmware's own IEN/ISER, no code address. */
    uint16_t gpio_if;          /* EXTI IF: bit L set = EXTI line L pending */
    uint8_t pending_gpio_even; /* a GPIO_EVEN (IRQ1) edge is pending */
    uint8_t pending_gpio_odd;  /* a GPIO_ODD (IRQ11) edge is pending */

    /* --- EFM32 GPIO EXTI configuration registers (as the firmware writes them) -
     * The EXTI block lets the firmware route a GPIO pin to an external-interrupt
     * LINE and choose which edge(s) raise it:
     *   EXTIPSELL (0x40006100): lines 0..7,  4 bits/line -> source PORT
     *   EXTIPSELH (0x40006104): lines 8..15, 4 bits/line -> source PORT
     *   EXTIRISE  (0x40006108): bit L = rising-edge  enabled for line L
     *   EXTIFALL  (0x4000610C): bit L = falling-edge enabled for line L
     *   IEN       (0x40006110): bit L = interrupt enabled for line L
     * On this MCU EXTI line L is driven by PIN L of the port selected in
     * EXTIPSEL[L]. We mirror these so GPIO IRQ delivery is FAITHFUL to the
     * firmware's own config (deliver only when the line is selected for the pin's
     * port, the observed edge is enabled, and IEN has the line). If the firmware
     * does NOT configure EXTI for a button pin (e.g. it polls that port instead),
     * we deliver NO GPIO IRQ for it and the firmware's polling path handles it --
     * exactly like real hardware. */
    uint32_t exti_psell;       /* 0x40006100 */
    uint32_t exti_pselh;       /* 0x40006104 */
    uint16_t exti_rise;        /* 0x40006108 */
    uint16_t exti_fall;        /* 0x4000610C */
    uint16_t exti_ien;         /* 0x40006110 */
    /* instrumentation (measurement only): instructions between consecutive DIN
     * reads of din_watch_port, and the last ninsn at which it was read. */
    uint64_t din_last_read_insn;
    uint64_t din_gap_sum;
    uint32_t din_gap_n;
    uint32_t din_gap_min;
    uint32_t din_gap_max;
} EmuState;

/* ------------------------------------------------------------ API ----------- */

/* Allocate+init the core, load the firmware image (flat) into RAM-resident
 * flash, allocate the 32KB RAM, wire the core regions/callbacks. flash_used is
 * DERIVED from fw_len. Returns 0 on success, <0 on allocation failure. */
int emu_init(EmuState* emu, const ArmProfile* prof, const uint8_t* fw_data, uint32_t fw_len);

/* Free the heap owned by the emulator (core, flash, ram, wfi set). */
void emu_deinit(EmuState* emu);

/* Reset the core and run the boot. If a knock-code was set with emu_set_knock()
 * it is then pressed PURELY over GPIO as an autotest (knock = firmware config,
 * not hardware). After this returns, emu->unlocked reflects the OBSERVABLE
 * display state. Blocking (runs millions of instructions). */
void emu_reset_and_unlock(EmuState* emu);

/* Run one burst of instructions with WFI/tick + EXC_RETURN handling. */
void emu_run_burst(EmuState* emu, uint32_t count);

/* Press+release ONE button PURELY over GPIO with realistic timing. Drive the
 * DIN pin low (pressed), run the firmware a while so its polling scan sees it,
 * then drive it high (released). hold=true -> long press. AGNOSTIC: no PC/code-
 * address sync. Returns true (the injection always happens). */
bool emu_press_button(EmuState* emu, int btn, bool hold);

/* COMPAT alias: a navigation button. Now identical to emu_press_button (pure
 * GPIO, no nav_reader PC-sync). hold=true long press. */
bool emu_nav_button(EmuState* emu, int btn, bool hold);

/* Set a button level directly (0=pressed). Used for continuous/raw input when a
 * full press cycle is not wanted. */
void emu_set_button(EmuState* emu, int btn, bool pressed);

/* Set the knock-code (unlock button sequence + final gate button) pressed by
 * emu_reset_and_unlock. This is FIRMWARE CONFIG, provided by the host/frontend,
 * NOT baked into the agnostic core. seq = array of B_* indices, gate = B_* index
 * of the held final button (<0 = none). Pass len=0 to disable auto-unlock. */
void emu_set_knock(EmuState* emu, const uint8_t* seq, uint8_t len, int gate);

/* True if the display currently has content (observable unlock signal). */
bool emu_is_unlocked(EmuState* emu);

/* Inject one RX edge into the firmware demodulator over the real RX-data pin
 * (PB0) + advance TIMER1 CNT (the demod's pulse-width stopwatch). AGNOSTIC: no
 * demod_isr call-by-address and no profile RAM writes. */
void emu_rx_inject_edge(EmuState* emu, bool level, uint32_t duration_us);

/* Pop one captured TX edge (firmware bit-bang on PB3). Returns false if empty. */
bool emu_tx_pop_edge(EmuState* emu, RfEdge* out);

/* Copy the current VISIBLE OLED framebuffer (OLED_FB_SIZE bytes) into dst. */
void emu_get_fb(EmuState* emu, uint8_t* dst);

/* Render the OLED framebuffer as ASCII art into a caller buffer (debug). Writes
 * up to cap bytes including the trailing NUL. Returns the length written. */
size_t emu_fb_ascii(EmuState* emu, char* out, size_t cap);

#endif /* PANDORA_EMULATOR_H */
