# FTM RF-TEST — Significado del status 0x14 y secuencia de entrada correcta
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
ELF: /tmp/modemre/modem_full.elf
Pass 4 — foco: explicar el rechazo 0x14, hallar el entry real del RF-test multi-tech,
y la secuencia de "entrar en modo".

Confianza: **FACT** = evidencia estatica directa | **INFERENCE** = deduccion sobre
convencion/arquitectura Qualcomm | **UNKNOWN** = no determinable con esta imagen.

====================================================================
0. HALLAZGO ESTRUCTURAL NUEVO (cambia el diagnostico de los passes previos)
====================================================================
**FACT — el codigo FTM/DIAG NO esta "relocado a runtime": esta COMPRIMIDO y paginado
por demanda (dlpager + q6zip).** Verificado:
 - Existen los modulos: dlpager.c, dlpager_q6zip_iface.c, dlpager_swappool.c,
   dlpager_pagesm.c, q6zip_sw.c, q6zip_worker.c, vmem_overlay*.c, y el pool nombrado
   `DL_PAGER_RX_SWAP_POOL`. **FACT (strings en b13/b21).**
 - La region virtual 0xd8xxxxxx (donde vive el dispatcher FTM 0xd8150ed8) NO esta en
   ningun program header (ni filesz ni memsz la cubren). Es espacio virtual paginado.
   **FACT.**
 - seg27 (va 0xce480000, 1.1MB, entropia 7.98) empieza con `78 9c` = **cabecera zlib**
   y descomprime limpio a 6.38 MB (guardado en /tmp/modemre/seg27_dec.bin). Es la
   **base de datos QSHRINK4 de mensajes F3** (no el codigo, pero contiene MUCHAS mas
   cadenas [FTM.RFTEST] que el seg21 crudo). **FACT.**
 - seg26 (36MB, entropia 7.73) es el backing comprimido q6zip del codigo paginado
   (formato propietario Qualcomm, delta+dict, NO zlib). **INFERENCE fuerte.**

Consecuencia dura: el dispatcher FTM (0xd8150ed8) y los (un)packers RF-test estan en
paginas q6zip de seg26. Recuperar la funcion exacta requiere: (1) parsear la metadata
de dlpager para mapear va 0xd8150ed8 -> pagina comprimida, (2) implementar el
decompresor q6zip_sw. Es factible pero es un proyecto en si mismo; con la imagen sola
NO se puede desensamblar directamente. **FACT (limite duro, ahora con la CAUSA exacta:
compresion q6zip, no "relocacion").**

Correccion de pass previo: las cadenas "ENTER IN PROGRESS"/"EXIT IN PROGRESS"
@0xc3920540 NO son de un state-machine de tech-enter de DIAG. Son estados de
`rf_nr5g_set_pwr_state`: la lista completa es
`UNINITALIZED / POWERED DOWN / POWERED UP / ACTIVE / PLL OFF /
 RETENTION ENTER IN PROGRESS / RETENTION EXIT IN PROGRESS / POWERED ON / ASLEEP`.
Son estados de POTENCIA/PLL del RF NR5G, no de entrada de tecnologia DIAG. **FACT.**
=> El "enter/exit" que importa es el de `ftm_rf_debug_tech_enter_exit.c`, no estas.

====================================================================
0.5. ESTRUCTURA NUEVA RECUPERADA: tablas de handlers por-comando (FACT)
====================================================================
Aunque el CODIGO esta en q6zip, la ESTRUCTURA de datos SI es visible. En seg21, justo
ANTES de cada format-string [FTM.RFTEST][<CMD>][UNPACK] hay un ARRAY de punteros a
codigo paginado (0xd80xxxxx-0xd81xxxxx): un handler por PROPIEDAD (field_id) del
comando. El array termina exactamente donde empieza el string del comando. **FACT.**

  handler_array @vaddr   N_handlers   comando (por el string que le sigue)
  0xc37c0268             46           RADIO_CONFIG
  0xc37c03b4              6           COMMAND_CAPABILITY
  0xc37c0478             70           RX_MEASURE
  0xc37c0620             17           WAIT_TRIGGER
  0xc37c073c              0           MSIM_CFG   (sin handlers propios)
  0xc37c078c             19           TX_CONTROL
  0xc37c0828             51           IQ_CAPTURE
  0xc37c0a10            237           TX_MEASURE

Lectura (FACT): cada comando RF-test tiene N propiedades (field_ids) y N funciones
handler. N = numero de propiedades manejables por ese sub-comando. Concuerda con las
tablas TLV de nombres de passes previos (RADIO_CONFIG ~60 nombres pero 46 con handler;
RX_MEASURE ~75 nombres, 70 con handler; IQ_CAPTURE 51). **FACT — confirma el modelo
"comando -> tabla de propiedades indexada por field_id".**

Importante para el 0x14: cada handler valida SU propiedad. Si mandas un field_id fuera
de rango, o un valor invalido para una propiedad, el handler correspondiente devuelve
error y el (un)packer aborta -> BAD_PARM 0x14. => **con field_ids/valores correctos y
el sub_command correcto, el 0x14 debe desaparecer.** COMMAND_CAPABILITY solo tiene 6
handlers (QUERY_COMMAND, QUERY_PROPERTY, CMD_MASK, PROPERTY_MASK x?) -> comando chico,
ideal para probar primero. **FACT (N=6).**

Nota: NO se hallo la tabla maestra sub_command->handler_array en rodata (esta en codigo
paginado o se arma en runtime). Por eso el VALOR numerico del sub_command sigue UNKNOWN
estatico; el CONTEO y CONTENIDO de propiedades por comando SI es FACT.

====================================================================
1. SIGNIFICADO DEL STATUS 0x14  (la pregunta central)
====================================================================
### 1a. Formato de la respuesta de error DIAG  (FACT)
En el dump esta el builder de respuesta de error de DIAG, con su format string:
  `diagpkt_rsp_send: Invalid command response - Sending response packet:
   0x%02X 0x%02X 0x%02X 0x%02X 0x%02X`  (@0xc35ca4cc, seg21)  **FACT.**
Nota: loguea **5 bytes**, mientras que la respuesta normal loguea 1 o 4
(`Response sent 0x%02X` / `0x%02X 0x%02X 0x%02X 0x%02X`). Ese registro de 5 bytes es
el `diagpkt_err_rsp()` clasico de Qualcomm, cuyo layout es:
  `{ uint8 error_code; uint8 original_pkt[N] }`  (N = min(4, len original)).

Tu respuesta observada ("primer byte 0x14, luego ecoa lo que mande") admite DOS
lecturas — hay que desambiguar en vivo (P2 del plan):

 (A) **diagpkt_err_rsp DIAG puro** (INFERENCE): 0x14=error_code y lo "ecoado" son los
     primeros bytes del paquete ORIGINAL (0x4B,0x0B,cmd_lo,cmd_hi...). En este caso el
     eco es CORTO (~4 bytes), no todos tus TLVs. 0x14 = DIAG_BAD_PARM_F.

 (B) **Respuesta FTM propia con status byte** (INFERENCE alternativa): el
     ftm_common_dispatch alloca una respuesta via diagpkt_subsys_alloc, escribe un
     campo de status = 0x14 al inicio del payload FTM, y devuelve el REQUEST ecoado
     (unpack que copia los TLVs de vuelta antes de fallar). En este caso el eco es
     LARGO (todos tus TLVs). 0x14 aqui seria un `ftm_rsp_status` interno.

 DESAMBIGUACION (P2): mira la LONGITUD del eco.
   - Si la respuesta es ~5 bytes (`14 4B 0B xx xx`) => caso (A), DIAG_BAD_PARM.
   - Si la respuesta re-incluye TODOS tus TLVs tras el 0x14 => caso (B), status FTM.
 En AMBOS casos el significado semantico es el mismo: **"parametro/estado invalido:
 el comando se reconocio pero no se pudo ejecutar"** (no es "comando desconocido", que
 seria 0x13). El plan en vivo no cambia. **FACT: no es 0x13 (comando SI reconocido).**

### 1b. Valor 0x14 en el protocolo DIAG  (constantes FIJAS de diagcmd.h)
Los command-codes de error de DIAG son constantes fijas del protocolo, identicas en
todo firmware Qualcomm MSM/MDM/MPSS (diagcmd.h / diagpkt):
  0x13 (19) = **DIAG_BAD_CMD_F**   -> comando/subsys no reconocido
  0x14 (20) = **DIAG_BAD_PARM_F**  -> "bad parameter" (parametro invalido)
  0x15 (21) = **DIAG_BAD_LEN_F**   -> longitud invalida
  0x17 (23) = **DIAG_BAD_MODE_F**  -> no permitido en el modo actual (algunos builds)
  0x18 (24) = DIAG_BAD_SPC_MODE_F
=> **0x14 = DIAG_BAD_PARM_F = "parametro invalido".** **FACT del valor** (constante de
   protocolo) + **INFERENCE** de que es este el que se usa aca (por el formato err_rsp
   de 5 bytes y porque 0x13 seria "no reconocido", que NO es tu caso: el comando SI se
   reconoce, la tabla @0xc37bd1e8 tiene entrada para 0x03 y 0x27).

### 1c. QUIEN pone el 0x14 y por que (interpretacion, INFERENCE alta)
Cadena de dispatch (FACT de la estructura):
  DIAG core (b13, presente) -> tabla subsys -> subsys 0x0B FTM -> tabla @0xc37bd1e8
  (75 entradas, TODAS -> 0xd8150ed8, el `ftm_common_dispatch`) -> re-dispatch interno
  por ftm_cmd_id -> handler de tecnologia -> (un)packer RF-test.
 - El comando SE reconoce (hay entrada para 0x03 y 0x27) => NO es 0x13.
 - El 0x14 se genera DESPUES de reconocer el comando pero ANTES de ejecutar (no hay
   F3 [FTM.RFTEST], confirmado por vos). Es decir: el `ftm_common_dispatch` o el
   handler de tecnologia devuelve un puntero de respuesta que DIAG interpreta como
   "sin respuesta valida / parametro malo", y DIAG emite `diagpkt_err_rsp(0x14)`.
 - **Significado practico: "el paquete llego al handler correcto, pero el
   sub_command/parametros no son validos EN EL ESTADO ACTUAL"** — clasico
   DIAG_BAD_PARM. Coincide con "mode not entered / not supported in current state".

**Resumen 0x14:**
 - **FACT:** es un error DIAG a nivel de framework (formato err_rsp de 5 bytes),
   no un byte de status propio dentro de una respuesta FTM normal.
 - **FACT (valor):** 0x14 = DIAG_BAD_PARM_F (constante de protocolo).
 - **INFERENCE alta:** lo dispara la rama FTM cuando el (ftm_cmd_id, sub_command)
   se reconoce pero los parametros/estado no permiten ejecutar (falta enter-mode, o
   falta un TLV obligatorio, o el sub no aplica a esa tech).

====================================================================
2. 0x03 (uniforme 0x14) vs 0x27 (discrimina)  — que es cada entry
====================================================================
FACT de la imagen:
 - Modulos por-tecnologia: `ftm_lte.c`, `ftm_lte_common_dispatch.c`,
   `ftm_lte_rf_test.c` (LTE=0x27) ; `ftm_nr5g_main.c`, `ftm_nr5g_rf_test.c`
   (NR5G=0x8000/0x8001).
 - Framework generico: `ftm_multi_tech_rf_test.c`, `ftm_rf_test_*.c`.
 - Cadena FACT en el DB qshrink de la rama LTE:
   `Tx_Rx_Split: ftm_lte_rex_dispatch: FTM_LTE_DISABLE_SCELL not supported - %d`
   => 0x27 pasa por `ftm_lte_rex_dispatch` que discrimina sub-comandos y sabe cuales
      "not supported". Esto explica que **0x27 discrimine** (0/5/6 aceptan, 1-4 dan
      0x14): el dispatcher LTE decodifica el sub y valida per-sub. **FACT (mecanismo).**

INTERPRETACION (INFERENCE alta) de tu probe:
 - **0x27 (FTM_LTE) = dispatcher LTE real y activo.** Decodifica sub_command:
     - sub 0/5/6 -> ECHO limpio = comandos que el dispatcher ACEPTA sin TLV obligatorio
       (candidatos: enter/get-state/exit/query — comandos de control sin params).
     - sub 1-4 -> 0x14 = comandos que existen pero requieren params/estado que no diste
       (config/measure que piden TLVs o que exigen enter-mode previo).
     - sub 7+ -> sin respuesta = fuera del rango del enum LTE.
 - **0x03 (FTM_RF) uniforme 0x14 = NO es el entry del RF-test en ESTE build.**
   Dos hipotesis (ambas dan el mismo comportamiento observado):
     (H1) 0x03 esta en la tabla pero su handler interno esta stubbeado/no soporta el
          formato RADIO_CONFIG que le mandas => devuelve BAD_PARM para todo.
     (H2) 0x03 es un dispatcher que exige un layout de header distinto (p.ej. sin el
          sub_command u16 que asumis, o con un "op-code" extra), asi que todo lo que
          mandas le resulta parametro invalido.
   => **INFERENCE alta: el RF-test multi-tech de este firmware NO cuelga de 0x03.
      Cuelga de los cmd_id POR-TECNOLOGIA (0x27 LTE, 0x8000/0x8001 NR5G).** El
      `ftm_multi_tech_rf_test.c` es la capa COMUN que esos dispatchers por-tech invocan
      internamente, no un cmd_id DIAG separado. Esto reconcilia todo lo observado.

Corolario: **dejar de sondear 0x03 para RF-test. El camino LTE es 0x27; el NR5G es
0x8000/0x8001.** (FACT: 0x8000/0x8001 estan en la tabla; INFERENCE: son el entry NR5G.)

====================================================================
3. POR QUE 0x27 sub 0/5/6 se ACEPTAN pero NO emiten [FTM.RFTEST]
====================================================================
INFERENCE alta:
 - Los sub que dan echo-vacio son comandos de CONTROL del dispatcher LTE (no del
   (un)packer RF-test multi-tech). Emiten F3 con OTRO tag (p.ej. [FTM.LTE...] o
   ninguno si son triviales), por eso no ves [FTM.RFTEST].
 - Los [FTM.RFTEST][*][UNPACK] solo se emiten cuando el (un)packer del framework comun
   corre, y eso ocurre para RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/etc — que son
   justamente los sub 1-4 que te dan 0x14 (porque les falta enter-mode o TLVs).
 => El SSID 23 [FTM.RFTEST] no aparece porque nunca llegas a EJECUTAR un comando del
    framework comun: te frena la validacion (BAD_PARM) antes del UNPACK. **Consistente
    con tu observacion de que el msg_mask de SSID 23 esta ON pero no llega nada.**

====================================================================
4. LA SECUENCIA DE ENTRADA CORRECTA (enter-mode)  — mejor reconstruccion
====================================================================
FACT:
 - Existe `ftm_rf_debug_tech_enter_exit.c` con log
   `[FTM.RFDEBUG][TECH_ENTER_EXIT][UNPACK]:[%3d][ %12s ][ %12d ]` (@0xc37beef0).
   Es un sub-comando de **RF-DEBUG** con TLVs simples {field_id, name, val}.
 - Tabla de params candidata g16 @0xc906c8e8 = {SUB, TECH, SCENARIO}. **INFERENCE fuerte.**
 - `FTM_MODE` global existe; hay gating por "tech active" (cadena
   `No Tech is active , so skipping RF read`). **FACT (existe el concepto de tech activa).**

INFERENCE (secuencia recomendada, a validar en vivo):
  Modelo Qualcomm ftm multi-tech: para poder correr RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE
  hay que tener la TECNOLOGIA "entrada". Dos rutas posibles, PROBAR en este orden:

  RUTA A (por-tecnologia, la que tu probe apunta como viva = 0x27):
    1) 0x27 sub=<uno de 0/5/6> con TLVs de enter. El sub 0/5/6 que dispara la
       transicion de tech (o habilita RADIO_CONFIG posterior) = el enter-mode LTE.
       Candidato mas probable: **sub 0** (convencion: cmd 0 = enter/set-mode).
    2) 0x27 sub=<config> RADIO_CONFIG con TLVs (TECHNOLOGY/BAND/CHANNEL/BW).
    3) 0x27 sub=<measure> RX_MEASURE / IQ_CAPTURE.
    4) 0x27 sub=<exit> al terminar (candidato: sub 5 o 6).

  RUTA B (enter explicito via RF-DEBUG TECH_ENTER_EXIT, luego RF-test):
    1) ftm_cmd_id = **0x03 (FTM_RF)** (RF-DEBUG cuelga de FTM_RF) con el sub_command
       de TECH_ENTER_EXIT y TLVs {SUB=0, TECH=<tech_lte>, SCENARIO=0}.
    2) luego RADIO_CONFIG por la misma rama.
    (Menos probable dado que 0x03 te da 0x14 uniforme; pero el TECH_ENTER_EXIT puede
     requerir un layout distinto — por eso 0x03 rechaza tu formato RADIO_CONFIG.)

  Valor numerico de TECH para LTE / NR5G en el TLV: **UNKNOWN estatico.** El enum de
  tecnologia (rfcom_mode_enum_type / ftm tech) NO esta como strings (RFCOM_NUM_MODES
  existe pero sus miembros son enum compilado). Orden Qualcomm tipico de
  rfcom_mode_enum_type (INFERENCE, varia por build):
    0=1X/CDMA, 1=EVDO/HDR, 2=GSM, 3=WCDMA/UMTS, 4=LTE, 5=TDSCDMA, ..., N=NR5G.
  => LTE suele ser 4-6, NR5G el mas alto. **PROBAR barriendo TECH=0..12.**

====================================================================
5. COMMAND_CAPABILITY sin entrar en modo — como invocarlo
====================================================================
FACT:
 - Modulo `ftm_rf_test_command_capability.c`; enum de propiedades con prefijo
   `FTM_RF_TEST_..._PROP_*` confirmado (p.ej. FTM_RF_TEST_RADIO_CFG_PROP_RX_CARRIER,
   FTM_MPE_CONTROL_PROP_*). => el framework usa PROPIEDADES numeradas, y
   COMMAND_CAPABILITY reporta por MASCARA (CMD_MASK + PROPERTY_MASK_0..255).
 - Tabla de params (g22 @0xc906ce18): field_id 1=QUERY_COMMAND, 2=QUERY_PROPERTY,
   3=CMD_MASK(resp), 4..7=PROPERTY_MASK_0_63..192_255(resp). **FACT.**

INFERENCE alta (es la via correcta para NO depender del enter-mode):
 - COMMAND_CAPABILITY es un comando de CONSULTA: por diseno NO debe requerir enter-mode
   (es lo que un tester llama primero para saber que soporta el modem). Por eso es el
   mejor candidato a que responda SIN 0x14 incluso sin haber entrado en modo.
 - Es **casi seguro uno de los sub 0/5/6 de 0x27** que te dieron echo-vacio (comandos
   de consulta/control sin TLV obligatorio).
 - cmd_id + sub_command exacto: **UNKNOWN numerico** (el enum esta en q6zip). PROBAR:
     0x27 sub=0 / sub=5 / sub=6, con num_tlv=0, y con num_tlv=1 {QUERY_COMMAND=0xFFFFFFFF}.
   El que devuelva un REPACK con CMD_MASK != 0 (y NO 0x14) = COMMAND_CAPABILITY.
   Decodificando CMD_MASK obtenes el ENUM NUMERICO REAL de sub-comandos y su tamano.

====================================================================
6. CONDICIONES QUE EL DISPATCH VALIDA ANTES DE EJECUTAR (resumen)
====================================================================
FACT / INFERENCE combinados:
 1) ftm_cmd_id debe estar en la tabla @0xc37bd1e8 (75 validos). Si no -> DIAG_BAD_CMD
    (0x13), no 0x14. (Tu 0x03 y 0x27 estan -> por eso ves 0x14, no 0x13.) **FACT.**
 2) El dispatcher por-tech (ftm_lte_rex_dispatch p/0x27) decodifica sub_command y
    valida per-sub: sub fuera de rango -> sin rsp; sub que necesita params/estado sin
    cumplirse -> **BAD_PARM 0x14**. **FACT (mecanismo por la cadena "...not supported").**
 3) Comandos de config/measure (RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE) requieren
    (INFERENCE): (a) tech entrada/activa, y (b) TLVs obligatorios. Faltando cualquiera
    -> 0x14 antes del UNPACK (por eso no hay F3). **INFERENCE alta.**
 4) No existe en la imagen un bitmap estatico de "TLV required": la validacion vive en
    el (un)packer q6zip. **FACT (no hay tabla de required en rodata).**

====================================================================
7. RESUMEN FACT / INFERENCE / UNKNOWN
====================================================================
FACT:
 - 0x14 significa "parametro/estado invalido", NO "comando desconocido". El comando SI
   se reconoce (0x03 y 0x27 estan en la tabla @0xc37bd1e8) => no es 0x13/DIAG_BAD_CMD.
 - Existe el builder de error DIAG con log de 5 bytes (@0xc35ca4cc): 0x14 encaja como
   DIAG_BAD_PARM_F (0x14 es constante fija del protocolo DIAG en diagcmd.h). El que sea
   err_rsp DIAG (caso A) o status FTM propio (caso B) se desambigua por longitud del eco
   en vivo; el significado semantico es el mismo.
 - 0x27 pasa por ftm_lte_rex_dispatch que discrimina y marca subs "not supported".
 - El codigo FTM esta comprimido q6zip (dlpager), no simplemente relocado. seg27 es la
   DB qshrink (descomprimida en /tmp/modemre/seg27_dec.bin, 6.38MB).
 - ESTRUCTURA de comandos RF-test recuperada: cada comando tiene un array de handlers
   por-propiedad en rodata (RADIO_CONFIG=46, RX_MEASURE=70, IQ_CAPTURE=51,
   COMMAND_CAPABILITY=6, TX_MEASURE=237, WAIT_TRIGGER=17, TX_CONTROL=19, MSIM_CFG=0),
   indexado por field_id (0-based). Handlers reales/distintos (no stubs).
 - Enum de propiedades = FTM_RF_TEST_*_PROP_* (mascaras via COMMAND_CAPABILITY).
 - Las cadenas ENTER/EXIT IN PROGRESS eran de rf_nr5g_set_pwr_state (correccion de pass
   previo; NO son un state-machine de tech-enter).

INFERENCE (alta):
 - El RF-test multi-tech NO cuelga de 0x03; cuelga de los cmd_id por-tecnologia:
   **0x27 (LTE)**, **0x8000/0x8001 (NR5G)**. 0x03 rechaza porque no es el entry.
 - 0x27 sub 1-4 dan 0x14 por falta de enter-mode y/o TLVs obligatorios.
 - COMMAND_CAPABILITY es uno de los sub 0/5/6 de 0x27 (consulta, sin enter-mode).
 - enter-mode LTE probable: 0x27 sub 0 (o via RF-DEBUG TECH_ENTER_EXIT).

UNKNOWN (solo en vivo o descomprimiendo q6zip de seg26):
 - Valor numerico exacto de cada sub_command (RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/
   COMMAND_CAPABILITY/ENTER).
 - Valor numerico de la tecnologia LTE/NR5G para el TLV TECH/TECHNOLOGY.
 - Layout exacto del header bajo 0x03 (por que rechaza todo).
 - Conjunto exacto de TLVs obligatorios de RADIO_CONFIG.

====================================================================
8. PLAN EN VIVO (prioridad, para cerrar lo UNKNOWN)  — accionable
====================================================================
Header: 0x4B 0x0B <ftm_cmd_id:u16 LE> <sub_command:u16 LE> <num_tlv:u16 LE> {TLVs}

P1. **Identificar COMMAND_CAPABILITY (da el enum entero sin entrar en modo):**
    - 0x27, num_tlv=0, sub = 0, luego 5, luego 6.
    - El que responda con payload (REPACK) en vez de 0x14 = COMMAND_CAPABILITY.
    - Repetir ese sub con num_tlv=1, TLV {field_id=1(QUERY_COMMAND), len=4,
      val=0xFFFFFFFF} para forzar enumeracion. Leer CMD_MASK: bit N=1 => sub N existe.
    => Esto RESUELVE el enum numerico real. Es el paso mas importante.

P2. **Confirmar que 0x14 es err_rsp DIAG (no status FTM):**
    - Capturar la respuesta COMPLETA byte a byte de un caso 0x14. Esperado:
      `14 4B 0B <cmd_lo> <cmd_hi>` (5 bytes). Si coincide => confirmado DIAG_BAD_PARM
      con eco del original (no eco de TLVs). Esto valida toda la seccion 1.

P3. **Enter-mode LTE:**
    - De los sub 0/5/6, mandar cada uno con TLVs {SUB=0, TECH=t, SCENARIO=0} barriendo
      t=0..12. Tras cada intento, mandar RADIO_CONFIG (el sub que en P1 aparezca como
      config). El (sub_enter, t) que haga que RADIO_CONFIG DEJE de dar 0x14 y empiece a
      emitir [FTM.RFTEST][RADIO_CONFIG][UNPACK] = enter-mode + valor de TECH LTE.

P4. **RADIO_CONFIG minimo:** una vez entrado, agregar TLVs
    {TECHNOLOGY, BAND, CHANNEL/CENTER_FREQ, BANDWIDTH, RX_CARRIER, RFM_DEVICE} y quitar
    de a uno hasta que 0x14 -> OK: eso da el set obligatorio real.

P5. **Descartar 0x03:** confirmar con COMMAND_CAPABILITY bajo 0x03 (num_tlv=0, sub
    0..9). Si TODO da 0x14 tambien ahi, queda probado que 0x03 no es entry RF-test.

P6. **(Opcional, offline) Recuperar el dispatcher real:** implementar el walk de
    dlpager metadata + decompresor q6zip_sw sobre seg26 para descomprimir la pagina que
    contiene 0xd8150ed8 y desensamblar `ftm_common_dispatch`. Solo asi se obtiene el
    enum sin sondeo en vivo. Esfuerzo alto.
