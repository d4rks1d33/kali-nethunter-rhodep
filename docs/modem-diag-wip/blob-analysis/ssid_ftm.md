# SSID del subsistema FTM / RF-test — DIAG msg mask

Firmware: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (Moto G82 5G, SM6375, Hexagon/QDSP6)
ELF: `/tmp/modemre/modem_full.elf` (reensamblado). Análisis estático.
Confianza: **FACT** = evidencia estática directa · **INFERENCE** = deducción · **UNKNOWN** = no determinable estáticamente.

--------------------------------------------------------------------
## TL;DR
--------------------------------------------------------------------
- **SSID del RF-test / FTM = `23` (0x17)** → `MSG_SSID_FTM`. **FACT.**
- TODA la familia `[FTM.RFTEST]`, `[FTM.RFDEBUG]`, `[FTM.CMN]`, `IQ_CAPTURE`,
  `RX_MEASURE`, `RADIO_CONFIG`, `TX_MEASURE`, cal-V3, etc. emite F3 con **ss_id=23**.
- Para que lleguen al AP: habilitar en el msg mask el rango que cubre 23 con todos
  los niveles de severidad en 1 (runtime_mask=0xFFFFFFFF).
- "Enable all": mandar un `SET_ALL_MSG_MASKS` (mask_len=0), o iterar
  todos los rangos 0..7389 con runtime_mask=0xFFFFFFFF.

--------------------------------------------------------------------
## 1. Evidencia — cómo se determinó el SSID (FACT)
--------------------------------------------------------------------
Los format strings `[FTM.RFTEST]...` están en seg21 (rodata) como texto plano
(NO fueron eliminados por QShrink → se emiten con `MSG_SPRINTF`/`msg_const_type`
clásico, con SSID inline, NO QSR-hash).

Cada mensaje F3 tiene una estructura `msg_const_type` de 16 bytes en rodata (seg21):

    struct msg_const_type {          // 16 bytes, little-endian
        const char* fmt_ptr;         // -> "[FTM.RFTEST][RADIO_CONFIG][UNPACK]..."
        const char* fname_ptr;       // -> "ftm_rf_test_radio_config.c"
        uint32      packed;          // (ss_id << 16) | line
        uint32      arg_count;       // nº de args del printf
    };

Ejemplos verificados directamente (holder = dirección de la struct en seg21):

    holder 0xc3555b78  ss_id=23 line=1436 args=2  ftm_rf_test_radio_config.c  "[FTM.RFTEST][RADIO_CONFIG][UNPACK]..."
    holder 0xc3555bb8  ss_id=23 line=983  args=2  ftm_rf_test_rx_measure.c    "[FTM.RFTEST][RX_MEASURE][UNPACK]..."
    holder 0xc3555c28  ss_id=23 line=638  args=2  ftm_rf_test_iq_capture.c    "[FTM.RFTEST][IQ_CAPTURE][UNPACK]..."

En todas las entradas de mensajes RF-test, la mitad ALTA del `packed` u32 = **0x17 = 23**
(constante), y la mitad baja = nº de línea del `.c` (varía). FACT.

Escaneo global de todas las `msg_const_type` del blob → la mitad-alta (SSID) sigue
exactamente la convención Qualcomm `msgcfg.h`:
    0x2f=47  (CORE/MEMHEAP)   0xfa9=4009 (L1)   0x1388=5000 (DS)   0x1580=5504 (SEC) ...

De **128 mensajes con ss_id=23**, prácticamente todos son ficheros `ftm_*`:
    ftm_calibration_v3_data.c, ftm_calv3_*_seq_class.cpp, ftm_lte_rf_test.c,
    ftm_rf_debug_vdpd_conversion.cpp, ftm_rf_debug_vdpd_cal.cpp,
    ftm_rf_test_iq_capture.c, ftm_rf_test_radio_config.c, ftm_rf_test_rx_measure.c,
    ftm_rf_test_command_capability.c, ftm_rf_test_wait_trigger.c,
    ftm_rf_test_tx_measure.c, ftm_rf_test_tx_control.c, ftm_rf_test_msim_config.c,
    ftm_rf_debug_*.cpp (device_cal, therm_read, rx_measure, tx_measure_adv, idc_cal,
    tx_override, tech_enter_exit, load_dpd, set_dpd_debug_mode),
    ftm_trm_ara.c, ftm_trm_rra.c, ftm_unified_query_aca.c, ftm_common_selftest.c,
    ftm_common_dispatch.c, ftm_qlnk_*.c, ftm_v2x_mdsp.c, ftm_rfc_cmw.c, ...
(1 solo falso positivo del scanner: srchacq_sm.c, 1/128 → ruido de alineación.)

→ **CONCLUSIÓN FACT: MSG_SSID_FTM = 23 (0x17). Los mensajes IQ_CAPTURE /
   RADIO_CONFIG / RX_MEASURE / REPACK / UNPACK del RF-test salen con ss_id=23.**

Nota: coincide con la convención histórica Qualcomm `MSG_SSID_FTM = 23`.

--------------------------------------------------------------------
## 2. SSIDs vecinos / relacionados con RF (por si algún log específico usa otro)
--------------------------------------------------------------------
- `14` (0x0E) — algunos módulos NR5G/IRAT específicos:
      ftm_nr5g_rf_debug_tx_override.c, ftm_rf_test_irat_config.c, ftm_wcdma_hs.c
  → si un log `[FTM.NR5G...]` no aparece con 23, probar habilitar también 14.
- `6004` (0x1774) — ftm_calv3_xpt_seq_class.cpp (xPT linearizer, cal).
- `6056` (0x17A8) — rf_nr5g_pwr_mgr.c (RF NR5G power mgr, no-FTM).
- `47` (0x2F) — CORE/memheap (aparece incidentalmente en algún ftm helper).

Recomendación: para RF-test basta **23**; para cobertura RF/NR5G completa habilitar
también **14** y **6004** (o simplemente "enable all", ver §4).

--------------------------------------------------------------------
## 3. Rango completo de SSIDs soportados (los que el modem reporta en build mask)
--------------------------------------------------------------------
SSIDs observados estáticamente (mitad-alta de las msg_const), min=0, max=7389:

    0, 7, 9, 13, 14, 17, 18, 19, 20, 21, 23, 35, 46, 47, 49, 50, 64, 80, 81, 85,
    86, 89, 91, 99, 100, 102, 103, 110, 112, 113,
    1006, 1007, 2002, 3010, 4001, 4009, 4600, 4601, 4609, 4612, 4613,
    5000, 5001, 5004, 5005, 5006, 5012, 5018, 5019, 5022, 5025, 5026, 5029, 5030,
    5031, 5032, 5034, 5501, 5502, 5503, 5504,
    6004, 6039, 6056, 7389

Rangos Qualcomm (convención msgcfg.h, para "enable all"):
    Legacy core   : 0   .. ~199
    1x/CDMA       : 1000.. ~1099
    GSM/WCDMA     : 2000.. ~3999
    MCS/L1        : 4000.. ~4099
    LTE           : 4009, 4600..4699
    Data services : 5000.. ~5099
    Security      : 5500.. ~5599
    RF/CAL/FTM-ext: 6000.. ~6099
    NR5G          : 7000.. ~7389+

→ El RF-test (23) cae en el bloque legacy bajo. Un solo rango `ssid_first=23
   ssid_last=23` ya basta para el RF-test.
→ Para "log everything": cubrir **ssid_first=0 .. ssid_last=8192** (redondeo seguro
   por encima de 7389). El modem ignora los ssid sin tabla registrada.

--------------------------------------------------------------------
## 4. Cómo habilitar el SSID en el msg mask DIAG
--------------------------------------------------------------------
Command code DIAG_EXT_MSG_CONFIG_F = **0x7D** (125). Subcomandos relevantes:
[INFERENCE — protocolo DIAG estándar Qualcomm, NO extraído del blob (los subcodes
no dejan strings). El blob SÍ confirma la terminología: strings del handler
`ssid_range_ctrl_pkt: ... num_ssid_ranges = %d` @0xc35cd172 y
`msg_mask: update msg mask - ... ssid_first=%d ssid_last=%d stream_id=%d` @0xc35ccfcf,
lo que valida que el modem parsea rangos {ssid_first,ssid_last}+stream_id.]

    0x7D 0x01  GET_SSID_RANGES        -> devuelve los rangos {first,last} que el modem soporta
    0x7D 0x02  GET_MSG_MASK           -> lee el runtime mask de UN rango
    0x7D 0x03  SET_MSG_MASK <-------- SET del runtime mask de UN rango (ss_id_first..ss_id_last)
    0x7D 0x04  SET_ALL_MSG_MASKS      -> pone el MISMO mask en TODOS los rangos (enable all)

### 4a. Habilitar SOLO el RF-test/FTM (SSID 23) — recomendado
Paquete DIAG_EXT_MSG_CONFIG / SET_MSG_MASK (0x7D 0x03). Estructura (little-endian):

    struct diag_ext_msg_config_set {
        uint8   cmd_code;      // 0x7D
        uint8   sub_cmd;       // 0x03  (SET_MSG_MASK)
        uint16  ss_id_first;   // 23    (0x0017)
        uint16  ss_id_last;    // 23    (0x0017)   -> rango de 1 ssid
        uint16  reserved;      // 0x0000 (padding/status, 0 en request)
        uint32  rt_mask[N];    // N = (ss_id_last - ss_id_first + 1) = 1
                               // rt_mask[0] = 0xFFFFFFFF  (todos los niveles ON)
    };

Bytes concretos (rango 23..23, mask=0xFFFFFFFF):

    7D 03  17 00  17 00  00 00  FF FF FF FF

(El `rt_mask` es el bitmap de niveles de severidad: LOW/MED/HIGH/ERROR/FATAL +
sprintf. 0xFFFFFFFF = todos los niveles habilitados = QXDM "verbose".)

Para cubrir también 14 (NR5G/IRAT): un rango 14..14, otro paquete, o un rango
14..23 con N=10 entradas rt_mask, todas 0xFFFFFFFF:

    7D 03  0E 00  17 00  00 00  [FF FF FF FF] x10

### 4b. "Enable ALL" (log everything, como QXDM full)
Opción A — SET_ALL_MSG_MASKS (0x7D 0x04): pone el mismo mask a TODOS los rangos:

    struct { uint8 cmd=0x7D; uint8 sub=0x04; uint16 rsvd=0; uint32 rt_mask; };
    Bytes:  7D 04 00 00 FF FF FF FF

Opción B — si el modem no respeta 0x04, hacer GET_SSID_RANGES (0x7D 0x01) primero
para enumerar los rangos exactos que el modem reporta, y luego un SET_MSG_MASK
(0x7D 0x03) por cada rango con rt_mask=0xFFFFFFFF. Esto replica lo que hace QXDM.

Opción C — un único SET con rango amplio 0..8192:
    ss_id_first=0x0000, ss_id_last=0x2000, N=8193 uint32 todos 0xFFFFFFFF.
    (Paquete grande; puede exceder MTU DIAG → preferir A o B.)

--------------------------------------------------------------------
## 5. Notas de integración (QRTR / canal DATA)
--------------------------------------------------------------------
- El handshake ya habilita build masks; falta el runtime msg mask de ss_id=23.
- Tras el SET_MSG_MASK, el modem responde con eco del paquete (mismo header +
  status). Verificar que status/reserved vuelva OK.
- Si los F3 siguen sin llegar tras habilitar 23: revisar que el DIAG event/log
  mask no filtre por stream_id, y que el canal DATA (no CMD) esté suscrito a F3
  (DIAG_EXT_MSG_F=0x79 / DIAG_QSR4_EXT_MSG_TERSE). Los strings del handler local:
      "msg_mask: update msg mask - msg_mask_size = %d, ssid_first = %d,
       ssid_last = %d, stream_id = %d, status = %d"   @0xc35ccfcf
  confirman que el modem parsea ssid_first/ssid_last/stream_id → el formato de §4
  es correcto para este build.

--------------------------------------------------------------------
## Resumen accionable
--------------------------------------------------------------------
1. SSID RF-test/FTM = **23 (0x17)** [FACT].
2. Enable RF-test:   `7D 03 17 00 17 00 00 00 FF FF FF FF`
3. Enable RF+NR5G:   añadir rango 14 (`... 0E 00 ...`).
4. Enable ALL:       `7D 04 00 00 FF FF FF FF` (SET_ALL_MSG_MASKS).
5. Rango máx SSID observado = 7389 → usar ss_id_last=0x2000 (8192) para cubrir todo.
