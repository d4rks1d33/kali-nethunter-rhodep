# FTM RF-TEST — SUB-COMMAND ENUM, ENTER-MODE, COMMAND_CAPABILITY
Build: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 (SM6375 / Moto G82 5G)
Pass 3 — foco: enum numérico de sub_command + enter-mode + COMMAND_CAPABILITY
ELF: /tmp/modemre/modem_full.elf

Confianza: **FACT** = evidencia estática directa. **INFERENCE** = deducción.
**UNKNOWN** = no determinable estáticamente (handler/unpacker en región RFLM
relocada 0xd815xxxx/0xd868xxxx/0xd919xxxx, NO mapeada en ningún program header).

====================================================================
0. RESULTADO CORTO (leer esto primero)
====================================================================
- El ENUM NUMÉRICO EXACTO de sub_command **NO es extraíble estáticamente**. Verificado
  por 5 vías independientes (abajo, sección 5). El dispatcher/unpacker RF-test está
  100% en la región RFLM relocada que no está en la imagen. **FACT (límite duro).**
- LO QUE SÍ ES FACT: los NOMBRES de sub-comando, sus TABLAS de parámetros TLV
  (índice = field_id), el mecanismo de COMMAND_CAPABILITY (por MÁSCARAS numéricas),
  y el orden de emisión de strings/tablas.
- EL CAMINO CORRECTO para obtener el enum numérico REAL es EN VIVO vía
  COMMAND_CAPABILITY (sección 4) — está diseñado exactamente para eso. Doy el
  procedimiento binario listo para usar.
- Doy además el mejor ESTIMADO del enum (INFERENCE) reconciliado con tu probe en
  vivo, con la advertencia explícita de que NO cuadra limpio (sección 3), lo que
  aporta información sobre qué es realmente el sub_command bajo 0x27.

====================================================================
1. NOMBRES DE SUB-COMANDO RF-TEST  (FACT)
====================================================================
De los format strings [FTM.RFTEST][<CMD>][UNPACK/REPACK] en seg21 (todos verificados):

  #  UNPACK fmt @vaddr    nombre
  -  0xc37c0320           RADIO_CONFIG
  -  0xc37c03cc           COMMAND_CAPABILITY
  -  0xc37c0590           RX_MEASURE
  -  0xc37c0664           WAIT_TRIGGER
  -  0xc37c073c           MSIM_CFG
  -  0xc37c07d8           TX_CONTROL
  -  0xc37c08f4           IQ_CAPTURE
  -  0xc37c0dc4           TX_MEASURE
  -  0xc391a3f8           IRAT_CONFIG
  -  0xc4168270           TRM_RRA
Framework: ftm_multi_tech_rf_test.c @0xc356a44e ; módulos ftm_rf_test_*.c.
Camino LTE: ftm_lte_rf_test.c @0xc3569dc8, ftm_lte_common_dispatch.c @0xc3569e11
(=> el RF-test SÍ es alcanzable por FTM_LTE=0x27, consistente con tu probe). **FACT.**

IMPORTANTE: los nombres NO existen como strings independientes (solo embebidos en los
format strings). Por eso COMMAND_CAPABILITY NO reporta por nombre sino por MÁSCARA
numérica (ver sección 4). Esto **confirma que existe un enum numérico de comandos**,
pero su valor se asigna en el header .h compilado dentro del código relocado. **FACT.**

====================================================================
2. TABLAS DE PARÁMETROS POR COMANDO (FACT) — el índice ya es el field_id
====================================================================
25 grupos de char*[] consecutivos en seg23 desde 0xc906c268 (pool único, delimitado
por NULL). Cada grupo = tabla de parámetros de un (sub)comando; field_id = índice.
Mapeo grupo->comando por SIGNATURA de campos (los dos primeros = FACT del pass previo;
el resto = INFERENCE fuerte por semántica de campos):

  g13 @0xc906c630 (60)  = RADIO_CONFIG           [FACT, pass previo]
  g14 @0xc906c720 (75)  = RX_MEASURE / IQ_CAPTURE[FACT, pass previo]
  g15 @0xc906c850 (37)  = TX_CONTROL   (TX_ACTION,TX_POWER,TX_WAVEFORM)  [INFERENCE]
  g16 @0xc906c8e8 (4)   = SUB/TECH/SCENARIO  (RADIO_SETUP mini / ENTER)  [INFERENCE]
  g17 @0xc906c8f8 (28)  = IQ_CAPTURE viejo (ACTION_ACQUIRE/FETCH,IQ_SOURCE,SAMP_SIZE)
  g18 @0xc906c968 (222) = TX_MEASURE   (ACTION_TX_POWER,EVM,ACLR,PVT,SEM,OBW)[INFERENCE]
  g19 @0xc906cce0 (38)  = MSIM_CFG     (SUB_IDX,MSIM_TUNE_AWAY,REGROUP)    [INFERENCE]
  g20 @0xc906cd78 (30)  = IRAT_CONFIG  (ACTION,SRC_*,BEAM_CYCLE)           [INFERENCE]
  g21 @0xc906cdf0 (10)  = WAIT_TRIGGER (SUB,TECH,THRESHOLD,DIRECTION,TIMEOUT)[INFERENCE]
  g22 @0xc906ce18 (8)   = COMMAND_CAPABILITY (QUERY_COMMAND,CMD_MASK,PROPERTY_MASK)[INFERENCE fuerte]
  g0..g12  = tablas de RF-DEBUG/CAL (device_cal, dpd, ept, iip2, idc) — no RF-test DIAG.
  g1  @0xc906c290 (37)  = RADIO_SETUP (IS_TEARDOWN,RADIO_SETUP_TYPE,BAND,CHANNEL,WAVEFORM)
  g23/g24 = ASDIV/TRM_RRA.
(Detalle completo de todos los campos: rftest_tlv_groups_raw.txt)

Nota: g13..g22 son las tablas de los 10 sub-comandos RF-test, emitidas de forma
CONSECUTIVA (declaration order del compilador). Ese orden es la pista más fuerte del
enum, pero NO cuadra con el probe en vivo (sección 3) => el sub_command bajo 0x27 NO
usa ese orden, o 0x27 usa un enum LTE legado distinto.

====================================================================
3. ENUM NUMÉRICO — ESTIMADO Y RECONCILIACIÓN CON EL PROBE EN VIVO
====================================================================
### 3a. Lo que dice tu probe en vivo (ftm_cmd_id=0x27, num_tlv=0):  (dato = ground truth)
  sub 0, 5, 6  -> ECHO VACÍO aceptado (sin datos)  => comando SIN TLV obligatorio
  sub 1, 2, 3, 4 -> status 0x14 (reconocido, pide TLVs) => comando con TLV obligatorio
  sub 7..15    -> sin respuesta                     => el enum sólo abarca 0..6 aquí

### 3b. Hipótesis A: enum = orden de tablas de parámetros (g13->0 .. g22->9)
  0 RADIO_CONFIG 1 RX_MEASURE 2 TX_CONTROL 3 (SUB/TECH/SCENARIO) 4 IQ_CAPTURE(old)
  5 TX_MEASURE 6 MSIM_CFG 7 IRAT_CONFIG 8 WAIT_TRIGGER 9 COMMAND_CAPABILITY
  => cmd0=RADIO_CONFIG debería PEDIR TLV, pero live sub0=echo-vacío. **NO CUADRA.**

### 3c. Hipótesis B: enum = orden de emisión de format strings
  0 RADIO_CONFIG 1 COMMAND_CAPABILITY 2 RX_MEASURE 3 WAIT_TRIGGER 4 MSIM_CFG
  5 TX_CONTROL 6 IQ_CAPTURE 7 TX_MEASURE 8 IRAT_CONFIG ...
  => cmd0=RADIO_CONFIG debería pedir TLV; sub5=TX_CONTROL y sub6=IQ_CAPTURE también.
     Live los da como echo-vacío. **NO CUADRA.**

### 3d. Hipótesis C (la que mejor explica el probe): CONCLUSIÓN
  El patrón live (0/5/6 = no-arg; 1-4 = piden config) NO corresponde al enum
  multi-tech RF-test. Corresponde a un enum donde los índices bajos sin-arg son
  comandos de ESTADO/CONTROL (enter/exit/get-state/reset/trigger) y 1-4 son los de
  configuración/medida. Esto es típico del enum LTE legado (ftm_lte_common_dispatch)
  bajo FTM_LTE=0x27, NO del ftm_common_rf_test genérico.
  => **INFERENCE (alta):** bajo 0x27, sub_command NO es el enum RADIO_CONFIG/RX_MEASURE
     multi-tech; es el enum de comandos FTM_LTE. Los echos-vacíos (0/5/6) son
     candidatos ENTER_MODE / EXIT_MODE / GET_STATE; los 1-4 son SET/CONFIG/MEASURE.
  => El framework multi-tech RF-test (RADIO_CONFIG etc.) probablemente cuelga de
     **FTM_RF=0x03** (aún NO sondeado) — hay que probarlo. Ver sección 6.

### 3e. Mejor estimado del enum multi-tech RF-test (para usar con FTM_RF=0x03)
  **INFERENCE — NO CONFIRMADO. Verificar con COMMAND_CAPABILITY (sección 4).**
  Estimado por convención Qualcomm ftm_common_rf_test_command_id (varía por build):
    0 = RADIO_CONFIG
    1 = RX_MEASURE
    2 = TX_CONTROL
    3 = TX_MEASURE
    4 = WAIT_TRIGGER
    5 = COMMAND_CAPABILITY
    6 = IQ_CAPTURE
    7 = MSIM_CONFIG
    8 = IRAT_CONFIG
  Los valores reales de ESTE build son UNKNOWN estáticamente. No los tomes como
  verdad: úsalos sólo como orden de barrido inicial.

====================================================================
4. COMMAND_CAPABILITY — cómo invocarlo y qué devuelve  (FACT del formato)
====================================================================
Módulo: ftm_rf_test_command_capability.c @0xc37c040b.
Tabla de parámetros = grupo 22 @0xc906ce18 (8 campos, FACT):
    field_id 1 = QUERY_COMMAND         (u32) = número de comando a consultar
    field_id 2 = QUERY_PROPERTY        (u32) = número de propiedad a consultar
    field_id 3 = CMD_MASK              (respuesta) bitmask de comandos soportados
    field_id 4 = PROPERTY_MASK_0_63    (respuesta) bits 0..63
    field_id 5 = PROPERTY_MASK_64_127  (respuesta)
    field_id 6 = PROPERTY_MASK_128_191 (respuesta)
    field_id 7 = PROPERTY_MASK_192_255 (respuesta)
UNPACK fmt: [FTM.RFTEST][COMMAND_CAPABILITY][UNPACK]:[%3d][ %12s ][ %12d ]  (FACT)
REPACK fmt: [FTM.RFTEST][COMMAND_CAPABILITY][REPACK]:[%3d][ %12s ][ %lld ][ 0x%8x ] (FACT)

INTERPRETACIÓN (FACT del layout de máscaras):
 - COMMAND_CAPABILITY devuelve un **CMD_MASK** = bitmap donde el bit N = 1 si el
   comando de enum N está soportado. Es decir: **el enum numérico está codificado en
   los bits de CMD_MASK**. Leyendo CMD_MASK obtenés directamente qué números de
   comando existen (y cuántos). Igual PROPERTY_MASK_0..255 para 256 propiedades.
 - QUERY_COMMAND/QUERY_PROPERTY son opcionales: sin ellos puede enumerar todo (por
   eso puede responder con num_tlv=0, i.e. tu "echo vacío" es compatible con que
   COMMAND_CAPABILITY sea uno de los sub 0/5/6).

USO EN VIVO (RECOMENDADO — resuelve el enum de forma definitiva):
  1) Enviar COMMAND_CAPABILITY con num_tlv=0 (o con QUERY_COMMAND=0xFFFFFFFF si num_tlv>0).
  2) Leer CMD_MASK en la respuesta: cada bit en 1 = un comando de enum válido.
  3) Para el nombre de cada bit, mandar COMMAND_CAPABILITY con QUERY_COMMAND=N y ver
     PROPERTY_MASK (propiedades: soporta_IQ, soporta_TX, necesita_enter, etc).
  sub_command de COMMAND_CAPABILITY: **UNKNOWN numérico** (candidato: uno de los que
  dieron echo-vacío en vivo = sub 0, 5 o 6). Probar los tres.

====================================================================
5. POR QUÉ EL ENUM NUMÉRICO NO ES EXTRAÍBLE ESTÁTICAMENTE  (FACT, 5 vías)
====================================================================
 (1) Los format strings [FTM.RFTEST][*] NO tienen NINGÚN xref inmediato en los
     segmentos de código mapeados (b10,b13,b12,b02,b30). Verificado con grep de sus
     vaddr en los disasm completos: 0 hits para las 11 cadenas. **FACT.**
 (2) Las tablas de parámetros (g13..g22 @0xc906cXXX) NO tienen NINGÚN puntero que las
     referencie en código ni en datos. Verificado escaneando las 35 secciones por el
     valor de cada base: 0 hits. Se indexan por cálculo en runtime. **FACT.**
 (3) Los nombres de archivo .c de los handlers (ftm_lte_rf_test.c,
     ftm_rf_test_control.c, ftm_multi_tech_rf_test.c) tampoco tienen xref: 0 hits.
     Todo el árbol FTM está en la región relocada. **FACT.**
 (4) La tabla de dispatch FTM subsys 0x0B @0xc37bd1e8 apunta uniformemente a
     0xd8150ed8 (runtime, NO mapeado; is_valid_va=False). **FACT (pass previo).**
 (5) Los nombres de sub-comando NO existen como strings sueltos => no hay tabla
     {char* name; uint id; funcptr} estática que enumere; el mapeo vive en un .h
     compilado dentro del código relocado. **FACT.**
 => El enum numérico se resuelve SÓLO en vivo (COMMAND_CAPABILITY) o con el blob RFLM
    secundario (no presente en este dump).

====================================================================
6. ENTER-MODE DE TECNOLOGÍA  (lo que se sabe)
====================================================================
FACT:
 - Existe ftm_rf_debug_tech_enter_exit.c @0xc37bef2d con log tag
   [FTM.RFDEBUG][TECH_ENTER_EXIT][UNPACK]:[%3d][ %12s ][ %12d ] @0xc37beef0.
   => Es un sub-comando de RF-DEBUG (no RF-TEST). UNPACK simple [field_id][name][val].
 - Tabla de params candidata = grupo 16 @0xc906c8e8: {SUB, TECH, SCENARIO}. Layout
   coherente con "entrar en (SUB, TECH)": SUB=subscription, TECH=tecnología,
   SCENARIO=escenario RF. **INFERENCE fuerte.**
 - Existen strings de estado: "ENTER IN PROGRESS" @0xc3920540, "EXIT IN PROGRESS"
   @0xc392055c => el enter/exit es una máquina de estados con confirmación. **FACT.**
 - No hay un enum de tecnología expuesto como strings. Los valores de TECH se pasan
   como entero. Referencias de tecnología en el árbol: TECH_LTE, TECH_5G/NR5G,
   TECH_WCDMA, TECH_GSM (@0xc41066f5.. como strings de otro subsistema; el valor
   numérico que espera el TLV TECH es UNKNOWN). **FACT (strings) / UNKNOWN (valor).**

CÓMO SE SELECCIONA LA TECNOLOGÍA EN EL RF-TEST (FACT):
 - En el framework multi-tech, la tecnología NO va en el header: va como TLV dentro
   de RADIO_CONFIG (g13): field_id 25=TECH_MODE, 22=SUB_TECH, 29=TECHNOLOGY. **FACT.**
 - Por lo tanto hay DOS mecanismos:
   (a) Enter explícito vía RF-DEBUG TECH_ENTER_EXIT {SUB,TECH,SCENARIO} (g16). INFERENCE.
   (b) Selección implícita por TLV TECH_MODE/SUB_TECH/TECHNOLOGY en RADIO_CONFIG. FACT.

SECUENCIA enter-mode (INFERENCE, basado en (a)+(b) y en los estados ENTER/EXIT):
   1) (opcional/necesario) TECH_ENTER: RF-DEBUG sub_command TECH_ENTER_EXIT con
      TLVs {SUB=<sub>, TECH=<tech_lte>, SCENARIO=<0>}  -> esperar "ENTER IN PROGRESS"->done
   2) RADIO_CONFIG con TLVs incl. TECHNOLOGY/TECH_MODE + BAND + CHANNEL/CENTER_FREQ
   3) RX_MEASURE / IQ_CAPTURE
   4) TECH_EXIT (TECH_ENTER_EXIT con IS_TEARDOWN/EXIT) al terminar.
 - sub_command numérico de TECH_ENTER_EXIT: UNKNOWN (está en el enum de RF-DEBUG,
   distinto del enum RF-TEST). El valor de TECH para LTE: UNKNOWN estáticamente.
 - RELACIÓN CON TU PROBE: los sub 0/5/6 que dan echo-vacío bajo 0x27 son los
   candidatos naturales a ENTER / EXIT / GET-STATE (comandos sin TLV obligatorio).
   Sondear cuál dispara "ENTER IN PROGRESS" identifica el enter-mode. **plan sección 8.**

====================================================================
7. CAMPOS OBLIGATORIOS  (RADIO_CONFIG / RX_MEASURE-IQ_CAPTURE)
====================================================================
UNKNOWN estático: NO hay flags de "required" por campo en las tablas. Las tablas g13/g14
son sólo char*[] (nombre). La validación "faltan TLVs" (status 0x14) la hace el unpacker
relocado. **FACT (no hay bitmap de required en la imagen).**

INFERENCE (por semántica RF, mínimos para que el comando ejecute):
  RADIO_CONFIG (g13) mínimo probable:
    id=29 TECHNOLOGY  (o id=25 TECH_MODE / id=22 SUB_TECH)
    id=5  BAND
    id=6  CHANNEL     (o id=12/21 CENTER_FREQ)
    id=7  BANDWIDTH
    id=1  RX_CARRIER  (y/o id=2 TX_CARRIER)
    id=3  RFM_DEVICE
  RX_MEASURE / IQ_CAPTURE (g14) mínimo probable para captura IQ:
    id=1  RX_CARRIER
    id=2  RFM_DEVICE
    id=39 IQ_CAPTURE_TYPE
    id=14 NUM_OF_SAMPLES
    id=16 SAMP_FREQ
    id=15 IQ_DATA_FORMAT
    id=13 FETCH_IQ = 1   (dispara/recupera)
  (field_ids = FACT; el subconjunto "obligatorio" = INFERENCE, confirmar en vivo
   agregando TLVs hasta que el status pase de 0x14 a OK.)

====================================================================
8. PLAN EN VIVO PARA CERRAR TODO (definitivo)
====================================================================
Header: 0x4B 0x0B <ftm_cmd_id:u16 LE> <sub_command:u16 LE> <num_tlv:u16 LE> {TLVs}

A) RESOLVER EL ENUM (COMMAND_CAPABILITY):
   - Bajo 0x27: mandar sub 0, luego 5, luego 6 con num_tlv=0. Uno responderá con un
     CMD_MASK no vacío en el REPACK => ese es COMMAND_CAPABILITY. Decodificar CMD_MASK:
     bit N=1 => comando enum N existe. Eso te da el ENUM NUMÉRICO real y su tamaño.
   - Repetir bajo ftm_cmd_id=0x03 (FTM_RF) — probablemente ahí vive el multi-tech
     RF-test con RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE. Comparar CMD_MASK 0x27 vs 0x03.
B) IDENTIFICAR ENTER-MODE:
   - De los sub que dan echo-vacío (0/5/6), mandar cada uno con TLVs {SUB=0, TECH=<n>,
     SCENARIO=0} barriendo TECH=0..15 y observar si aparece transición de estado /
     "ENTER IN PROGRESS" o si RADIO_CONFIG posterior deja de dar 0x14. El que habilita
     RADIO_CONFIG = enter-mode; el TECH que funciona para LTE = valor de tecnología.
C) CONFIRMAR REQUIRED de RADIO_CONFIG:
   - Empezar con los mínimos de sección 7 e ir quitando/agregando TLVs; el status
     0x14->OK marca el conjunto obligatorio real.
D) LAYOUT TLV: confirmar field_id u16 vs u8 y length en bytes (el probe previo mostró
   que el modem ecoa el TLV -> comparar el eco byte a byte para deducir anchura).

====================================================================
9. RESUMEN FACT/INFERENCE/UNKNOWN
====================================================================
FACT:
 - Nombres de los 10 sub-comandos RF-test y sus tablas TLV (field_id=índice).
 - COMMAND_CAPABILITY usa máscaras numéricas (CMD_MASK + PROPERTY_MASK_0..255);
   grupo 22 @0xc906ce18 con QUERY_COMMAND/QUERY_PROPERTY.
 - TECH_ENTER_EXIT existe (RF-DEBUG), con estados ENTER/EXIT IN PROGRESS; params
   candidatos {SUB,TECH,SCENARIO} (g16).
 - La tecnología en RF-test se pasa por TLV (TECH_MODE/SUB_TECH/TECHNOLOGY en g13).
 - El enum numérico NO está en la imagen (5 vías verificadas).
INFERENCE:
 - Bajo 0x27 el sub_command es el enum LTE legado (0/5/6 = enter/exit/get-state sin
   arg; 1-4 = config/measure). El multi-tech RF-test genérico cuelga de 0x03.
 - Mapeo grupo->comando g15..g22 (por semántica de campos).
 - Estimado de enum multi-tech y campos obligatorios (sección 3e/7).
UNKNOWN (sólo en vivo o con blob RFLM):
 - VALOR numérico exacto de cada sub_command (RADIO_CONFIG=?, RX_MEASURE=?, IQ_CAPTURE=?).
 - sub_command numérico de COMMAND_CAPABILITY y de TECH_ENTER_EXIT.
 - Valor numérico de la tecnología LTE para el TLV TECH / TECHNOLOGY.
 - Conjunto exacto de campos obligatorios (bitmap de required está en runtime).
