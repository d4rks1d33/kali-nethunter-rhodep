# FTM RF-TEST / IQ-CAPTURE / RX-TUNE — Reverse engineering pass 2
Moto G82 5G (SM6375, Hexagon/QDSP6)
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
ELF: /tmp/modemre/modem_full.elf  (reensamblado, verificado)

Confianza: **FACT** = evidencia estatica directa | **INFERENCE** = deduccion |
**UNKNOWN** = no determinable estaticamente (handler en RFLM runtime).

------------------------------------------------------------------
## TL;DR
------------------------------------------------------------------
1. **Selector / ftm_cmd_id del RF-test:** INFERENCE = **0x03 (FTM_RF)** para el
   RF-test multi-tech generico; alternativamente **0x27 (FTM_LTE=39)** y
   **0x8000/0x8001 (FTM_NR5G)** para el RF-test por-tecnologia. La confirmacion
   exacta requiere prueba en vivo (el dispatcher esta en RFLM runtime). 0x27 ya
   respondio en vivo -> mejor candidato inicial para IQ capture LTE.
2. **Command number (sub-command RF-test):** UNKNOWN numerico. Los sub-comandos
   existen y estan nombrados (RADIO_CONFIG, RX_MEASURE, IQ_CAPTURE, TX_MEASURE,
   WAIT_TRIGGER, TX_CONTROL, MSIM_CFG, COMMAND_CAPABILITY, IRAT_CONFIG, TRM_RRA)
   pero su valor de enum se asigna en codigo runtime, no estatico. FACT: los
   nombres/format-strings. Ver plan de sondeo (COMMAND_CAPABILITY).
3. **Tabla nombre-TLV -> ID numerico:** **FACT, extraida completa.** El ID de cada
   campo = indice en el array char* del comando. Ficheros:
   - rftest_tlv_RX_TUNE_ids.txt     (RADIO_CONFIG/RX_TUNE, 60 campos)
   - rftest_tlv_IQ_CAPTURE_ids.txt  (RX_MEASURE/IQ_CAPTURE, 75 campos)
   - rftest_tlv_groups_raw.txt      (25 grupos completos)
4. **Muestras IQ:** se devuelven por **PUNTERO (DDR/memshare) + tamano**, no inline.
   FACT (format REPACK: [%2d][%3d][ %12s ][ %4d ][ 0x%8x ], el 0x%8x = direccion).
5. Detalle completo del formato del comando en: **rftest_command_format.md**.

------------------------------------------------------------------
## 1. Selector FTM del RF-test  (evidencia)
------------------------------------------------------------------
FACT: tabla de dispatch FTM (subsys 0x0B) @ **0xc37bd1e8** (seg21), 75 entradas
`{u16 cmd_lo; u16 cmd_hi; u32 disp}`, disp=0xd8150ed8 (runtime) para todas.
Los 75 valores cmd_lo son los ftm_cmd_id validos (2do u16 del paquete DIAG-FTM).
Incluye: 0x03(FTM_RF), 0x27(FTM_LTE), 0x28(TDSCDMA), 0x8000/0x8001(NR5G), 0x14(WLAN)...

FACT: existen los modulos (strings en seg21):
  ftm_multi_tech_rf_test.c @0xc356a44e   <- framework RF-test generico
  ftm_rf_test_control.c, _radio_config.c, _rx_measure.c, _wait_trigger.c,
  _iq_capture.c, _tx_measure.c, _command_capability.c, _msim_config.c,
  _tx_control.c, _irat_config.c
  ftm_rf_debug_*.cpp (device_cal, tx_measure_adv, idc_cal, therm_read, rx_measure...)

INFERENCE: el RF-test generico cuelga de ftm_cmd_id **0x03 (FTM_RF)**. Los strings
[FTM.RFTEST][RX_MEASURE][LTE] y [FTM.LTE.RFTEST][TX_CONTROL] muestran que ademas
hay caminos por-tecnologia (LTE=0x27, NR5G=0x8000).

UNKNOWN: no hay xref estatico de la tabla 0xc37bd1e8 ni de los modulos (todo el
codigo de dispatch RF-test esta relocado a 0xd815xxxx/0xd868xxxx/0xd919xxxx que
NO estan en ningun program header). Verificado: is_valid_va(0xd8150ed8)=False.

------------------------------------------------------------------
## 2. Sub-comandos RF-test (FACT los nombres, UNKNOWN los IDs)
------------------------------------------------------------------
Derivado de format strings [FTM.RFTEST][<SUBCMD>][UNPACK|REPACK] en seg21:

  SUBCMD              UNPACK fmt (request)         REPACK fmt (response)                  @vaddr
  RADIO_CONFIG        [%3d][ %12s ][ %12d ]        [%3d][ %12s ][ %lld ][ 0x%8x ]         0xc37c0320
  COMMAND_CAPABILITY  [%3d][ %12s ][ %12d ]        [%3d][ %12s ][ %lld ][ 0x%8x ]         0xc37c03cc
  RX_MEASURE          [%3d][ %12s ][ %12d ]        [%3d][ %12s ][ %4d ][ 0x%8x ]          0xc37c0590
  WAIT_TRIGGER   [%2d][%3d][ %12s ][ %12d ]   [%2d][%3d][ %12s ][ %4d ][ 0x%8x ]          0xc37c0664
  MSIM_CFG            [%3d][ %12s ][ %12d ]        -                                      0xc37c073c
  TX_CONTROL          [%3d][ %12s ][ %12d ]        -                                      0xc37c07d8
  IQ_CAPTURE     [%2d][%3d][ %12s ][ %12d ]   [%2d][%3d][ %12s ][ %4d ][ 0x%8x ]          0xc37c08f4
  TX_MEASURE          [%3d][ %12s ][ %12d ]        [%3d][ %12s ][ 0x%04x%04x%04x%04x ]    0xc37c0dc4
                                              /y    [%3d][ %12s ][ %4d ][ 0x%8x ]
  IRAT_CONFIG         [%3d][ %12s ][ %12d ]        -                                      0xc391a3f8
  (+ variantes LTE:   [FTM.RFTEST][RADIO_CONFIG][LTE], [RX_MEASURE][LTE]...)  0xc37cc190+

Lectura de campos: %3d=field_id (=indice tabla TLV), %12s=name(solo log),
%12d=valor int, %4d=size, 0x%8x=puntero a data (payload grande). El %2d extra de
IQ_CAPTURE/WAIT_TRIGGER = sub-indice de captura (NUM_OF_CAPTURES). FACT.

------------------------------------------------------------------
## 3. Formato del REQUEST (INFERENCE de estructura, FACT de field_ids)
------------------------------------------------------------------
Transporte DIAG:
   0x4B | 0x0B | <ftm_cmd_id:u16 LE> | <RF-test payload>

RF-test payload (INFERENCE, estructura estandar Qualcomm ftm_rf_test):
   struct {
     u16 sub_command;   // RADIO_CONFIG / RX_MEASURE / IQ_CAPTURE / ...  (id UNKNOWN)
     u16 num_tlv;
     struct { u16 field_id; u16 length; u8 value[length]; } tlv[num_tlv];
   }
   field_id LE, value LE. (Confirmar u16 vs u8 en vivo.)

field_id sale de las tablas FACT:
  - RX-tune (RADIO_CONFIG): rftest_tlv_RX_TUNE_ids.txt
  - IQ capture (RX_MEASURE): rftest_tlv_IQ_CAPTURE_ids.txt

------------------------------------------------------------------
## 4. IQ CAPTURE — campos clave (FACT, tabla @0xc906c720)
------------------------------------------------------------------
Comando: RX_MEASURE (modulo ftm_rf_test_rx_measure.c / ftm_rf_test_iq_capture.c)
  id=1   RX_CARRIER         id=4   RX_AGC            id=7   RX_MODE
  id=10  SENSITIVITY        id=13  FETCH_IQ          id=14  NUM_OF_SAMPLES
  id=15  IQ_DATA_FORMAT     id=16  SAMP_FREQ         id=17  OVERRIDE_LNA
  id=18  RX_GAIN_CTL_TYPE   id=39  IQ_CAPTURE_TYPE   id=44  NUM_OF_CAPTURES
  id=2   RFM_DEVICE         id=5   LNA_GS            id=6   SIG_PATH
  id=45/46/47 IQ_SAMPLE_DEBUG_INFO_0/1/2
Respuesta: bloque REPACK con field_id, size (%4d) y puntero 0x%8x a las muestras.
=> **Muestras via puntero DDR/memshare, NO inline.** FETCH_IQ dispara/recupera.

Handshake NR5G (FACT, strings [SHANK]): nrfw_rx_iq_capture enter -> before send ->
after cnf -> exit  (@0xc37a3198..0xc37a323b) => req/cnf asincrono.

------------------------------------------------------------------
## 5. RX TUNE — campos clave (FACT, tabla @0xc906c630)
------------------------------------------------------------------
Comando: RADIO_CONFIG (ftm_rf_test_radio_config.c).
  id=1 RX_CARRIER  id=2 TX_CARRIER  id=3 RFM_DEVICE  id=5 BAND  id=6 CHANNEL
  id=7 BANDWIDTH   id=12 CENTER_FREQ (tambien id=21) id=22 SUB_TECH id=25 TECH_MODE
  id=26 SCS  id=9 SIG_PATH  id=10 ANT_PATH  id=20 INTER_FREQ  id=29 TECHNOLOGY
  id=45 BWP_ID  id=46 BWP_BW  id=48 BWP_CENTER_FREQ  ...
La "tecnologia" se selecciona por TLV (TECH_MODE/SUB_TECH/TECHNOLOGY), no por header.
NOTA: RX_TUNE_CMD @0xc379794c NO es esto — es un msg interno del searcher 1X (msg_id
0x78, tabla @0xc3797bc4). Irrelevante para DIAG. Ver rftest_command_format.md sec 5b.

------------------------------------------------------------------
## 6. Limites estaticos + plan de sondeo en vivo
------------------------------------------------------------------
NO determinable estaticamente (RFLM runtime 0xd8xxxxxx/0xd9xxxxxx):
 - ftm_cmd_id exacto confirmado, valor numerico de cada sub_command, layout binario
   exacto (u8 vs u16 de field_id/length, alineacion).
Plan:
 a) COMMAND_CAPABILITY: mandar FTM_RF(0x03) barriendo sub_command 0..15; el modem
    responde el enum soportado (REPACK). Tambien probar cmd_id 0x27 y 0x8000.
 b) RADIO_CONFIG minimo (TLV TECH_MODE) para validar cmd_id + layout.
 c) IQ: RADIO_CONFIG(RX_TUNE) -> RX_MEASURE {IQ_CAPTURE_TYPE, NUM_OF_SAMPLES,
    SAMP_FREQ, FETCH_IQ=1}; leer puntero 0x%8x devuelto (memshare/DDR).
 d) Confirmar endianness/anchura de field_id y length.
