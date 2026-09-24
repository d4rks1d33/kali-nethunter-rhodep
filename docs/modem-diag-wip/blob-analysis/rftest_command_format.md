# FTM RF-TEST COMMAND FORMAT — IQ CAPTURE / RX TUNE
Build: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 (SM6375 / Moto G82 5G)
Analyst pass 2 — static extraction over reassembled ELF /tmp/modemre/modem_full.elf

Marcado de confianza: **FACT** = extraido con evidencia estatica directa.
**INFERENCE** = deduccion sobre convencion/estructura. **UNKNOWN** = no determinable
estaticamente (handler relocado a 0xd8xxxxxx / 0xd9xxxxxx en runtime RFLM).

====================================================================
0. RESUMEN EJECUTIVO
====================================================================
- El framework RF-test es "multi-tech" (modulo ftm_multi_tech_rf_test.c @0xc356a44e)
  y usa un **protocolo TLV** para todos sus sub-comandos (RADIO_CONFIG, RX_MEASURE,
  IQ_CAPTURE, TX_MEASURE, WAIT_TRIGGER, TX_CONTROL, MSIM_CFG, COMMAND_CAPABILITY,
  IRAT_CONFIG, TRM_RRA). **FACT** (format strings [FTM.RFTEST][<CMD>][UNPACK/REPACK]).
- Las TABLAS nombre-TLV -> ID numerico ESTAN presentes en el binario (seg23, data).
  El **ID de cada campo TLV = indice dentro de su tabla de comando**. **FACT**.
- El **codigo** que ejecuta el dispatch FTM y el (un)packing TLV NO esta en la imagen
  estatica: todos los handlers apuntan a 0xd8150ed8 / 0xd815xxxx / 0xd868xxxx /
  0xd919xxxx que NO estan mapeados en ningun program header. **FACT** (verificado:
  is_valid_va(0xd8150ed8)=False). => selector numerico y command-id exactos requieren
  prueba en vivo o el blob RFLM secundario. Ver seccion 6.

====================================================================
1. HEADER DIAG-FTM  (nivel de transporte)
====================================================================
Paquete DIAG:  0x4B (DIAG_SUBSYS_CMD_F) | 0x0B (DIAG_SUBSYS_FTM) | <ftm_cmd_id:u16 LE> | ...

FACT: La tabla de dispatch FTM esta en 0xc37bd1e8 (seg21 rodata), 75 entradas de
8 bytes con formato REAL:
        struct { u16 cmd_lo; u16 cmd_hi; u32 dispatch_ptr; }
Es un dispatch por RANGO de ftm_cmd_id [cmd_lo..cmd_hi] (aqui lo==hi siempre).
dispatch_ptr = 0xd8150ed8 para las 75 (dispatcher comun runtime-resuelto).

Los 75 ftm_cmd_id (== el 2do u16 del paquete DIAG-FTM):
  0x00(FTM_COMMON) 0x01(1X) 0x02(HDR/EVDO) 0x03(FTM_RF) 0x07(GSM) 0x08(WCDMA)
  0x09(UMTS) 0x0a(GPS) 0x0b 0x0d 0x10 0x11 0x12 0x14(WLAN) 0x15 0x1b 0x1d 0x1e
  0x1f 0x20 0x21 0x22 0x23 0x24 0x25 0x27(LTE=39) 0x28(TDSCDMA=40) 0x2f 0x30 0x31
  0x32 0x33 0x38..0x3f 0x41..0x47 0x4a..0x57 0x65..0x69 0x79 0x7a 0x7b 0x7c 0x7e
  0x80 0x8000(NR5G) 0x8001(NR5G)

NOTA IMPORTANTE sobre "selector" vs "ftm_cmd_id":
En este firmware el 2do u16 del paquete DIAG-FTM ES el ftm_cmd_id que indexa la
tabla 0xc37bd1e8. No hay un tercer nivel "selector" separado en el transporte:
el "selector de tecnologia" del RF-test moderno se pasa como CAMPO TLV
(TECH_MODE / SUB_TECH / TECHNOLOGY) DENTRO del payload, no en el header.  **FACT**
(TECH_MODE, SUB_TECH, TECHNOLOGY existen como campos TLV en la tabla RADIO_CONFIG).

====================================================================
2. QUE ftm_cmd_id DISPARA EL RF-TEST
====================================================================
**INFERENCE (alta confianza):** ftm_cmd_id = **0x03 (FTM_RF)**.
Evidencia:
 - 0x03 esta en la tabla de dispatch (idx 1). En la arquitectura FTM moderna de
   Qualcomm el comando FTM_RF=3 es el dispatcher RF generico/comun que aloja el
   framework multi-tech RF-test (ftm_multi_tech_rf_test.c). Los modulos
   ftm_rf_test_*.c y ftm_rf_debug_*.c cuelgan de este arbol.
 - Los sub-comandos LTE-especificos aparecen tambien bajo FTM_LTE=0x27 y NR5G bajo
   0x8000/0x8001 (strings [FTM.RFTEST][RX_MEASURE][LTE], [FTM.LTE.RFTEST][TX_CONTROL]).
   => Hay DOS caminos: (a) RF-test generico via FTM_RF=3 con TECH_MODE en TLV;
   (b) RF-test por-tecnologia via el cmd_id de esa tecnologia (0x27 LTE, 0x8000 NR5G).

**UNKNOWN (requiere prueba en vivo):** confirmar cual de 0x03 / 0x27 / 0x8000 acepta
el sub-comando IQ_CAPTURE en este build concreto. El (un)packer que lee el
sub-command byte esta en RFLM runtime (no estatico). Ver seccion 6 para plan de
sondeo.

Cross-check con probes en vivo previos (7,13,16,20,27 respondieron): 0x1b(27) y
0x27(39) estan vivos. 0x27=FTM_LTE es fuerte candidato para IQ capture LTE.
0x03 no fue sondeado aun -> PROBAR.

====================================================================
3. ESTRUCTURA GENERICA DEL PAYLOAD RF-TEST (TLV)
====================================================================
FACT (derivado de los format strings de (un)pack):

REQUEST (host->modem), "UNPACK" en el modem:
  Formatos observados:
    [FTM.RFTEST][RADIO_CONFIG][UNPACK]:      [%3d][ %12s ][ %12d ]
    [FTM.RFTEST][RX_MEASURE][UNPACK]:        [%3d][ %12s ][ %12d ]
    [FTM.RFTEST][IQ_CAPTURE][UNPACK]:  [%2d][%3d][ %12s ][ %12d ]   <- 2 indices
    [FTM.RFTEST][WAIT_TRIGGER][UNPACK]:[%2d][%3d][ %12s ][ %12d ]   <- 2 indices
    [FTM.RFTEST][TX_MEASURE][UNPACK]:        [%3d][ %12s ][ %12d ]
  Interpretacion de campos por TLV:
    %3d  = field_id  (== indice en la tabla de nombres del comando, seccion 4)
    %12s = field_name (solo log; NO va en el wire)
    %12d = valor entero del campo
    En IQ_CAPTURE / WAIT_TRIGGER el %2d extra delante = sub-indice de captura /
      instancia (NUM_OF_CAPTURES>1 -> se repite el bloque). **INFERENCE**.

  => Cada TLV en el wire es (INFERENCE de estructura estandar Qualcomm ftm_rf_test):
        struct ftm_rf_test_tlv {
            uint16_t field_id;     // = indice tabla (seccion 4)
            uint16_t length;       // bytes del value
            uint8_t  value[length];// little-endian
        };
     El header RF-test antes de los TLVs (INFERENCE):
        struct ftm_rf_test_req_hdr {
            uint16_t sub_command;  // RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE / ...
            uint16_t num_tlv;      // cantidad de TLVs que siguen
            // ftm_rf_test_tlv tlv[num_tlv];
        };
     (El valor exacto de 'sub_command' por cada modulo es UNKNOWN estaticamente,
      ver seccion 6; el ORDEN de aparicion de los format strings sugiere un enum:
      0=RADIO_CONFIG? 1=COMMAND_CAPABILITY 2=RX_MEASURE 3=WAIT_TRIGGER 4=MSIM_CFG
      5=TX_CONTROL 6=IQ_CAPTURE 7=TX_MEASURE ... pero es INFERENCE debil.)

RESPONSE (modem->host), "REPACK" en el modem:
    [FTM.RFTEST][RADIO_CFG][REPACK]:   [%3d][ %12s ][ %lld ][ 0x%8x ]
    [FTM.RFTEST][RX_MEASURE][REPACK]:  [%3d][ %12s ][ %4d ][ 0x%8x ]
    [FTM.RFTEST][IQ_CAPTURE][REPACK]:  [%2d][%3d][ %12s ][ %4d ][ 0x%8x ] <-
    [FTM.RFTEST][TX_MEASURE][REPACK]:  [%3d][ %12s ][ 0x%04x%04x%04x%04x ] (inline 64b)
                                 y     [%3d][ %12s ][ %4d ][ 0x%8x ]
  Interpretacion:
    %3d   = field_id
    %4d   = size (bytes de ese campo en la respuesta)
    0x%8x = **PUNTERO / offset a la data** del campo (DDR / memshare)
    %lld  = valor 64-bit inline (RADIO_CFG)
  => La respuesta empaqueta cada campo como TLV; los campos grandes (muestras IQ)
     se referencian por PUNTERO 0x%8x, no inline. Ver seccion 5.
  "NOT PACKED" y "Swapped location of elements %d and %d" => hay una fase de
  reordenamiento/optimizacion del buffer de respuesta. **FACT** (strings presentes).

====================================================================
4. TABLA NOMBRE-TLV -> ID NUMERICO  (FACT, extraida de seg23)
====================================================================
Ubicacion: arrays de char* consecutivos en seg23 (RW data), region 0xc906c268+.
Formato: char* param_names[]; el INDICE del array = field_id del TLV.
Grupo delimitado por entrada "UNASSIGNED" (id 0) y terminado por 0x00000000.
NOTA: no hay un puntero-maestro estatico a cada grupo (se resuelve en runtime),
pero las tablas de datos estan completas y verificadas.

Ver archivos:
  - rftest_tlv_RX_TUNE_ids.txt      (Grupo 13 = RADIO_CONFIG / RX_TUNE, 60 fields)
  - rftest_tlv_IQ_CAPTURE_ids.txt   (Grupo 14 = RX_MEASURE/IQ_CAPTURE, 75 fields)
  - rftest_tlv_groups_raw.txt       (los 25 grupos completos)

====================================================================
5. COMO SE DEVUELVEN LAS MUESTRAS IQ
====================================================================
FACT + INFERENCE:
 - El REPACK de IQ_CAPTURE es: [%2d][%3d][ %12s ][ %4d ][ 0x%8x ]
   El ultimo campo 0x%8x = **DIRECCION (puntero DDR / memshare buffer)**, y %4d el
   tamano. => las muestras IQ NO viajan inline en el paquete DIAG de respuesta;
   se devuelve un puntero + longitud. El host las lee via memshare/DDR o via un
   comando FETCH_IQ posterior. **FACT** (el patron 0x%8x aparece SOLO donde hay
   payload grande: IQ_CAPTURE, RX_MEASURE, RADIO_CFG; TX_MEASURE tiene ademas una
   variante inline 0x%04x%04x%04x%04x para valores de 64 bits).
 - Campo TLV **FETCH_IQ** (RX_MEASURE id=13) = flag/accion para recuperar el buffer
   IQ; **NUM_OF_SAMPLES** (id=14), **IQ_DATA_FORMAT** (id=15), **SAMP_FREQ** (id=16),
   **IQ_CAPTURE_TYPE** (id=39), **NUM_OF_CAPTURES** (id=44). **FACT**.
 - Flujo INFERIDO de IQ capture:
     1) RADIO_CONFIG (RX_TUNE): sintoniza RX (BAND, CHANNEL, BANDWIDTH, CENTER_FREQ,
        RX_CARRIER, TECH_MODE...).
     2) RX_MEASURE con IQ_CAPTURE_TYPE + NUM_OF_SAMPLES + SAMP_FREQ + FETCH_IQ:
        dispara la captura; el modem coloca las muestras en un buffer DDR y
        responde con puntero (0x%8x) + size.
     3) (Opcional) FETCH_IQ para paginar/recuperar el buffer si es grande.
   Los strings [SHANK] nrfw_rx_iq_capture enter/before send/after cnf/exit
   (0xc37a3198..) confirman un handshake req/cnf async para NR5G. **FACT**.

====================================================================
5b. ACLARACION SOBRE "RX_TUNE_CMD" @0xc379794c  (IMPORTANTE)
====================================================================
FACT: RX_TUNE_CMD @0xc379794c NO es el comando de RX-tune del RF-test DIAG.
Es una entrada de la tabla {name, msg_id} del MSGR interno del SEARCHER 1X/CDMA
(array @0xc3797bc4: RX_TUNE_CMD->msg_id 0x78, SRCH_PEAK_CMD->0x79,
REQUEST_SYSTEM_RESTART_CMD->0x7a, ...). Es un mensaje L1 interno, no accesible
directamente por DIAG-FTM.
=> El "RX tune" del RF-test por DIAG es el sub-comando **RADIO_CONFIG (Grupo 13)**,
   que sintoniza RX/TX con los TLV BAND/CHANNEL/BANDWIDTH/CENTER_FREQ/RX_CARRIER/
   TECH_MODE. Ver rftest_tlv_RX_TUNE_ids.txt.

====================================================================
6. LO QUE NO SE PUEDE DETERMINAR ESTATICAMENTE + PLAN EN VIVO
====================================================================
UNKNOWN estaticos (handlers en 0xd8xxxxxx/0xd9xxxxxx, no mapeados):
 - El valor numerico exacto del sub_command de cada modulo RF-test (RADIO_CONFIG=?,
   RX_MEASURE=?, IQ_CAPTURE=?). Solo hay el ORDEN de los strings.
 - El ftm_cmd_id de nivel-1 confirmado (0x03 vs 0x27 vs 0x8000).
 - El layout binario exacto del header RF-test (endianness de field_id/length,
   si length esta en bytes o words, alineacion).

PLAN DE SONDEO EN VIVO (via DIAG):
 a) Enviar COMMAND_CAPABILITY: es un sub-comando que el modem responde listando
    los sub-comandos y campos soportados (format string
    [FTM.RFTEST][COMMAND_CAPABILITY][REPACK]). Mandar FTM_RF (cmd_id=0x03) con
    sub_command variando 0..15 y payload minimo; el que responda sin error revela
    el enum. Capturar la respuesta REPACK.
 b) Barrer ftm_cmd_id en {0x03,0x27,0x8000} con un RADIO_CONFIG minimo
    (1 TLV: TECH_MODE) y ver cual acepta.
 c) Para IQ capture: RADIO_CONFIG(RX_TUNE) -> RX_MEASURE con
    IQ_CAPTURE_TYPE, NUM_OF_SAMPLES, SAMP_FREQ, FETCH_IQ=1; leer el puntero 0x%8x
    devuelto. Los field_id a usar estan en rftest_tlv_IQ_CAPTURE_ids.txt (FACT).
 d) Confirmar layout TLV probando field_id como u16 vs u8 y length en bytes.
