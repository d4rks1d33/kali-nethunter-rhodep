/*
 * protocol.h - Enum/constant definitions shared by the agnostic EFM32 emulator
 *              core (emulator.c/.h) and the FFI wrapper.
 *
 * ORIGIN: on the ESP32 backend this file was the UART wire-format spec shared
 * with the Flipper frontend. ON THE G82 (mainline Linux) THE CORE IS DRIVEN VIA
 * A DIRECT ctypes FFI (libpandora.so) -- there is no UART link -- so the frame
 * layout, CRC-8 and FW-upload machinery are NOT needed here. They have been
 * removed to keep the build lean. Only the semantic enums the emulator core
 * actually references survive:
 *
 *   - PROTO_RF_*   Si4432 operating mode (idle/ready/rx/tx) -> EmuState.rf_mode
 *   - PROTO_MOD_*  radio modulation (unmod/OOK/FSK/GFSK)     -> EmuState.rf_mod
 *   - PROTO_BTN_*  logical keyfob button bits (B_* enum order)
 *
 * These are the only PROTO_* symbols emulator.c/.h use. Keeping them here (rather
 * than inlining) preserves the include and the enum names across the codebase.
 */
#ifndef PANDORA_PROTOCOL_H
#define PANDORA_PROTOCOL_H

#include <stdint.h>

/* ------------------------------------------------------------ button bits --- */
/* One bit per keyfob logical button. Matches the emulator's B_* enum order
 * (B_UP=0, B_DOWN=1, B_OK=2, B_BACK=3, B5=4, B6=5). */
#define PROTO_BTN_UP (1u << 0)
#define PROTO_BTN_DOWN (1u << 1)
#define PROTO_BTN_OK (1u << 2)
#define PROTO_BTN_BACK (1u << 3)
#define PROTO_BTN_B5 (1u << 4)
#define PROTO_BTN_B6 (1u << 5)

/* ------------------------------------------------------------ RF state ------ */
/* Mirrors the Si4432 operating mode derived in the emulator. */
#define PROTO_RF_IDLE 0u
#define PROTO_RF_READY 1u
#define PROTO_RF_RX 2u
#define PROTO_RF_TX 3u

/* Radio modulation (for display/diagnostics / HackRF bridge follow). */
#define PROTO_MOD_UNMOD 0u
#define PROTO_MOD_OOK 1u
#define PROTO_MOD_FSK 2u
#define PROTO_MOD_GFSK 3u

#endif /* PANDORA_PROTOCOL_H */
