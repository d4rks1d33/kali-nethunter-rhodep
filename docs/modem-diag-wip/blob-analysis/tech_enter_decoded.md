# FTM RF-test TECH-ENTER — intento de descompresión q6zip de seg26 + hallazgos
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
ELF: `/tmp/modemre/modem_full.elf` (87 MB, reensamblado del .mdt + modem.bNN)
Herramienta: `qualcomm-q6zip` (nlitsme) clonada en `/tmp/q6zip`
Fecha: pass "q6zip / tech-enter".

Leyenda de confianza:
- **FACT** = evidencia estática directa (bytes/desensamblado verificados en esta imagen).
- **INFERENCE** = deducción sobre convención/arquitectura Qualcomm.
- **UNKNOWN** = no determinable con esta imagen sin sondeo en vivo.

================================================================================
## 0. RESUMEN EJECUTIVO — LEER PRIMERO (honesto)
================================================================================
1. **La herramienta q6zip funciona y la entendí** (formato: header `npages,version` +
   dict1/dict2 + tabla de punteros de página + datos bit-packed con 15 opcodes; el
   delta-comp es `2-bit opcode + anchors/delta`). **FACT.**
2. **NO pude descomprimir seg26 con ella**, y determiné la CAUSA con certeza:
   **seg26 (modem.b26 @vaddr 0xcc000000, 38 MB) NO contiene la metadata q6zip/delta
   (ni dict, ni tabla de punteros de página) en NINGUNA forma reconocible dentro del
   ELF.** Lo verifiqué con 6 barridos independientes (sección 2). Es un bloque opaco de
   alta entropía (7.73) sin tabla de punteros monótona en todo su rango. **FACT (límite
   duro).** => `/tmp/modemre/seg26_dec.bin` **NO se pudo generar** (ver sección 2.4).
3. **Descubrí por qué el dispatcher FTM 0xd8150ed8 no está en la imagen**: la región
   virtual del código paginado (0xd8xxxxxx) **no está en ningún program header**, y el
   backing comprimido carece de la tabla de páginas en el ELF. El código real del
   dispatcher/unpacker RF-test **no es recuperable estáticamente de esta imagen**.
   **FACT.** (Confirma y explica el "límite duro" de los passes previos, ahora probado
   a nivel de metadata de dlpager, no solo inferido.)
4. **Lo que SÍ obtuve** (secciones 3-6): la estructura de dlpager (descriptores reales
   desensamblados), el mapa de secciones comprimidas, los nombres de sub-comando y de
   propiedades RF-test desde el segmento **ya descomprimido seg27** (QSHRINK4, que SÍ
   pude leer entero), y la mejor reconstrucción de la secuencia tech-enter con su nivel
   de confianza explícito. **El enum NUMÉRICO exacto de sub_command y el valor NUMÉRICO
   de TECH para LTE/NR5G siguen siendo UNKNOWN estático** — su única vía de obtención es
   EN VIVO vía COMMAND_CAPABILITY (procedimiento en sección 7).

================================================================================
## 1. LA HERRAMIENTA q6zip — cómo funciona (FACT, del código leído)
================================================================================
`/tmp/q6zip/q6unzip.py` (código RX / q6zip) y `deltauncomp.py` (data RW / delta):

- **q6zip (código, sección "rx")**: `Q6zipSegment` espera, EN EL BOUNDARY del segmento:
  `npages:u16, version:u16` → `dict1` (arglen DICT1_BITS, típ. 2^10) → `dict2`
  (2^14) → lista de `npages` punteros (VADDRs a cada página comprimida) → datos.
  `elfbase = ptrs[0] - datastart`. Los datos son bit-packed con 15 opcodes
  (DICT1/DICT2_MATCH, NO_MATCH literal, SEQ, LOOKBACK, MASK/LOOKBACKMASK). El primer
  ~50% de páginas lleva 2 dwords de "chunk meta" extra (`--skipheader`).
- **delta (data, sección "rw")**: `npages:u16, version:u16` → `npages` punteros → datos.
  Opcodes 2-bit: `00`=0x00000000, `01 aa`=anchors[aa], `10 aa d10`=anchor con delta,
  `11 w32`=literal (y avanza anchor). 2 anchor bits, 10 delta bits (estándar).
- **Requisito ineludible**: para invocar cualquiera de las dos herramientas hace falta
  la TABLA DE PUNTEROS DE PÁGINA (y para q6zip además los diccionarios). Sin ella, la
  herramienta no tiene por dónde empezar.

Invocaciones intentadas (todas registradas):
```
python3 q6unzip.py -o 0xcc000000 --dump --dictsize 0x4400 modem_full.elf
   -> npages=17791 ver=0x464c (BASURA: son bytes de datos comprimidos, no un header)
python3 deltauncomp.py --rawfile --dump modem.b26
   -> "ptrs" aleatorios, no monótonos -> no es un header delta válido
```

================================================================================
## 2. LOCALIZACIÓN DEL SEGMENTO — qué es cada cosa (FACT)
================================================================================
Program headers relevantes (del .mdt, verificados):

| PH | vaddr      | filesz    | memsz     | qué es (determinado) |
|----|------------|-----------|-----------|----------------------|
| 21 | 0xc3553000 | 0x0d315f4 | 0x5616000 | **rodata** (strings FTM, tablas dispatch, handler arrays). NO comprimido. |
| 24 | 0xc9138000 | **0**     | 0x2e17000 | **BSS/región descomprimida RW** (48 MB), rellenada en runtime por dlpager. filesz=0. |
| 25 | 0xcbf4f000 | 0x0019900 | 0x001a000 | pequeño (tabla). NO es header de seg26. |
| 26 | 0xcc000000 | 0x244a040 | 0x244b000 | **38 MB opacos, entropía 7.73** = backing comprimido. **Sin metadata en el ELF.** |
| 27 | 0xce480000 | 0x011922f | 0x011a000 | **zlib** → QSHRINK4 DB (ya descomprimido: `seg27_dec.bin`, 6.38 MB). |

**2.1 El dispatcher 0xd8150ed8 vive en 0xd8xxxxxx, que NO está en ningún program
header** (ni filesz ni memsz lo cubren). Es la ventana virtual paginada de dlpager.
**FACT.**

**2.2 Descriptor de dlpager REAL, desensamblado (FACT):** en el código en c0db4194+
hay acceso directo `r2 = memw(##0xc8d0a348)` seguido de aritmética con
`immext(#0xd8000000)`. El descriptor en **0xc8d0a338** es:
```
c8d0a338: cc000000   comp_start  (= seg26)
c8d0a33c: ce13a000   comp_end
c8d0a340: d4400000   remap_start
c8d0a344: d6539000   remap_end
c8d0a348: 00c00000   (usado por memw en c0db419c)
```
Nota crítica: `comp_size (0x213a000) == remap_size (0x2139000)` → es un **remap 1:1**
(físico→virtual), NO una relación de compresión (una descompresión daría remap>comp).
Confirma que ese descriptor es de POOL/REMAP de memoria, y explica por qué no da la
tabla de páginas. **FACT.**

**2.3 Descriptor de seg27 (QSHRINK):** en 0xc906a0a4: `cf800000 / ce480000 / ce59922f`
→ seg27 (zlib). Consistente. **FACT.**

**2.4 Barridos que PRUEBAN la ausencia de metadata q6zip/delta (FACT):**
- (a) Header q6zip en offset 0 de seg26 → basura (npages=17791/ver=0x464c).
- (b) Búsqueda de una lista de punteros monótona (≥300 entradas, delta<0x8000) en
  TODO seg26 (9.5 M words): **0 resultados**.
- (c) Búsqueda de header q6zip auto-consistente (npages+ver+dict1[0]==0, ptrs
  crecientes con ptr0 apuntando justo tras la ptrlist) en TODO el ELF, con 9 tamaños
  de dict: **0 resultados** válidos con ptrlist larga.
- (d) Búsqueda de ptr-list apuntando al rango de seg26 (0xcc–0xce) en todo el ELF:
  **0 resultados**.
- (e) seg25 (previo a seg26) como header/ptrlist de seg26: **sin ptrs a seg26**.
- (f) delta rawfile sobre seg26: "ptrs" aleatorios, no monótonos.
=> **La tabla de páginas y el/los diccionario(s) del código q6zip NO están en esta
imagen reensamblada.** Probablemente residen en una región cargada por separado (PH24
es filesz=0) o el backing está cifrado/scrambled. **FACT.**

CONSECUENCIA HONESTA: **no fue posible producir `seg26_dec.bin`.** No por falta de la
herramienta (que funciona), sino porque el insumo obligatorio (dict + ptrlist) no está
presente para alimentarla. Cualquier "descompresión" a ciegas de seg26 produciría ruido.

================================================================================
## 3. LO QUE SÍ QUEDA PROBADO SOBRE EL CAMINO RF-TEST (FACT, imagen)
================================================================================
De seg21 (rodata, NO comprimido) y seg27 (QSHRINK, descomprimido):

- **Framework**: `ftm_multi_tech_rf_test.c` (@0xc356a44e) + `ftm_rf_test_*.c`.
  Camino LTE: `ftm_lte_rf_test.c` (@0xc3569dc8) y `ftm_lte_common_dispatch.c`
  (@0xc3569e11) — el RF-test SÍ cuelga de **FTM_LTE = ftm_cmd_id 0x27**. **FACT.**
  Camino NR5G: `ftm_nr5g_rf_test.c` (@0xc356a6dd), `ftm_nr5g_main.c` — cmd_id
  **0x8000/0x8001**. **FACT.**
- **Tech-enter/exit**: módulo `ftm_rf_debug_tech_enter_exit.c` (@0xc37bef2d) con el
  format-string de UNPACK en **0xc37beef0**:
  `[FTM.RFDEBUG][TECH_ENTER_EXIT][UNPACK]:[%3d][ %12s ][ %12d ]`.
  Layout del TLV que imprime: **`[field_id:%3d][ name:%12s ][ value:%12d ]`**. **FACT.**
  Tabla de params (pass previo) g16 @0xc906c8e8 = {SUB, TECH, SCENARIO}. **INFERENCE
  fuerte.**  => el tech-enter toma TLVs {SUB, TECH, SCENARIO}.
- **Nombres de sub-comando RF-test** (de los strings [FTM.RFTEST][<CMD>][UNPACK]):
  RADIO_CONFIG, COMMAND_CAPABILITY, RX_MEASURE, WAIT_TRIGGER, MSIM_CFG, TX_CONTROL,
  IQ_CAPTURE, TX_MEASURE, IRAT_CONFIG, TRM_RRA, MPE_CONTROL. **FACT (nombres).**
- **Propiedades (field_ids) confirmadas por assertion strings en seg27** (FACT):
  `FTM_RF_TEST_RADIO_CFG_PROP_RX_CARRIER`, `..._TX_CARRIER`;
  `FTM_MPE_CONTROL_PROP_{MPE_STATE,BEAM_ID_A,BEAM_ID_B,TYPE1_DETECTION,TYPE2_*,...}`.
- **Gating por "tech activa"**: existe el concepto (cadena de pass previo
  "No Tech is active, so skipping RF read"). **FACT.**

================================================================================
## 4. STATUS 0x14 — condición que valida el dispatch antes de ejecutar
================================================================================
(Consolidado con `rftest_entry_0x14.md`, sin cambios; ahora con la CAUSA raíz probada.)
- 0x14 = **DIAG_BAD_PARM_F** (constante fija del protocolo DIAG). El comando SÍ se
  reconoce (0x27 está en la tabla dispatch @0xc37bd1e8, 75 entradas, todas →0xd8150ed8),
  por eso NO es 0x13 (DIAG_BAD_CMD). **FACT (valor) + INFERENCE (que sea éste).**
- **Condición que el dispatch valida antes del 0x14 (mecanismo, FACT por la cadena
  `ftm_lte_rex_dispatch: ... not supported - %d`)**: `ftm_lte_rex_dispatch` decodifica
  el sub_command bajo 0x27 y valida per-sub. Si el sub necesita params/estado
  (tech entrada) y no se cumple → **BAD_PARM 0x14 ANTES del UNPACK** (por eso no hay F3
  [FTM.RFTEST]). El flag concreto que chequea (p.ej. `tech_entered`/`FTM_MODE`) está en
  el código paginado → **UNKNOWN estático** en cuanto al nombre/dirección exacta, pero
  el MECANISMO ("sub reconocido pero estado/param inválido") es FACT.
- Observado en vivo (para cruzar): bajo 0x27, sub 0/5/6 → echo limpio (aceptados, sin
  ejecutar); sub 1-4 → 0x14. Consistente con: 0/5/6 = control/consulta sin TLV
  obligatorio; 1-4 = config/measure que exigen tech-enter previo. **FACT (observado).**

================================================================================
## 5. SECUENCIA TECH-ENTER — mejor reconstrucción (con confianza explícita)
================================================================================
Header DIAG-FTM: `0x4B 0x0B <ftm_cmd_id:u16 LE> <sub_command:u16 LE> <num_tlv:u16 LE> {TLVs}`
TLV = `{field_id:u16, length:u16, value[]}`.

### 5a. Para LTE
- **ftm_cmd_id = 0x0027** (FTM_LTE). **FACT** (cuelga de aquí el RF-test LTE).
- **sub_command (tech-enter) = UNO de {0, 5, 6}** (los que en vivo dan echo limpio,
  = control sin TLV obligatorio; el tech-enter es de control). **INFERENCE alta.**
  Candidato más probable por convención Qualcomm: **sub 0** (o vía RF-DEBUG
  TECH_ENTER_EXIT). El valor numérico exacto: **UNKNOWN estático**.
- **TLVs (del format-string TECH_ENTER_EXIT, FACT del layout)**:
  `{SUB, len=4, val=<sub_id (0 si single-SIM)>}`,
  `{TECH, len=4, val=<TECH_LTE>}`,
  `{SCENARIO, len=4, val=0}`.
  Los `field_id` de SUB/TECH/SCENARIO: de g16 (INFERENCE) — el orden imprime SUB(0),
  TECH(1), SCENARIO(2) según el UNPACK `[%3d]`. **INFERENCE.**
- **VALOR de TECH para LTE = UNKNOWN estático.** El enum `ftm tech`/`rfcom_mode_enum`
  es compilado y su sección de código está en q6zip no recuperable. NO aparece como
  string numerado en seg21 ni seg27. Único dato de enums-tech hallado en strings:
  legado A2 `[0:DEF 1:W 2:L 3:TD]` (DEFAULT/WCDMA/LTE/TDSCDMA) — **NO es el enum FTM**,
  no usarlo como verdad. Orden típico Qualcomm de `rfcom_mode_enum_type` (INFERENCE,
  varía por build): 0=1X, 1=HDR, 2=GSM, 3=WCDMA, 4=LTE, 5=TDSCDMA, ..., alto=NR5G.
  => **barrer TECH = 0..12 en vivo.**

### 5b. Para NR5G
- **ftm_cmd_id = 0x8000 / 0x8001** (FTM_NR5G). **FACT (están en la tabla; INFERENCE que
  son el entry NR5G).** Misma estructura de tech-enter; **TECH_NR5G = UNKNOWN** (el más
  alto del enum). Barrer TECH = 0..15.

### 5c. Advertencia honesta
Ni el `sub_command` numérico ni el `TECH` numérico se pudieron FIJAR estáticamente en
esta imagen. Todo lo marcado UNKNOWN aquí requiere la vía en vivo (sección 7). Lo que
está FIJO (FACT) es: cmd_id 0x27 (LTE) / 0x8000-1 (NR5G), el layout del TLV de
tech-enter {SUB,TECH,SCENARIO}, y que el sub-enter es de control (grupo {0,5,6}).

================================================================================
## 6. MAPA sub_command → nombre (FACT nombres / UNKNOWN números)
================================================================================
Sub-comandos del framework RF-test multi-tech (nombres = FACT; su ÍNDICE numérico bajo
0x27 = UNKNOWN estático, ver nota):

  nombre                 tabla-params (field_id=índice)   #props
  RADIO_CONFIG           g13 @0xc906c630                  60 (46 con handler)
  COMMAND_CAPABILITY     g22 @0xc906ce18                  8  (6 con handler)
  RX_MEASURE             g14 @0xc906c720                  75 (70 con handler)
  IQ_CAPTURE             g17 @0xc906c8f8 / g14            51 con handler
  TX_MEASURE             g18 @0xc906c968                  222 (237 handler)
  TX_CONTROL             g15 @0xc906c850                  37 (19 handler)
  WAIT_TRIGGER           g21 @0xc906cdf0                  10 (17 handler)
  MSIM_CFG               g19 @0xc906cce0                  38 (0 handler propio)
  IRAT_CONFIG            g20 @0xc906cd78                  30
  TECH_ENTER_EXIT        g16 @0xc906c8e8 {SUB,TECH,SCEN}  (RF-DEBUG)
  MPE_CONTROL            (props FTM_MPE_CONTROL_PROP_*)
  TRM_RRA                g23/g24

Nota (FACT): NO existe en rodata la tabla maestra `sub_command→handler_array`; se arma
en el código paginado. Por eso el NÚMERO de cada sub_command es UNKNOWN estático. El
CONTEO/CONTENIDO de propiedades por comando SÍ es FACT (arrays de handlers en seg21).

### COMMAND_CAPABILITY — para leer CMD_MASK
- Sub-comando **COMMAND_CAPABILITY** (número UNKNOWN; candidato entre {0,5,6} de 0x27
  por ser consulta sin enter-mode). Tabla g22 @0xc906ce18: field_id
  **1=QUERY_COMMAND, 2=QUERY_PROPERTY, 3=CMD_MASK, 4..7=PROPERTY_MASK_*.** **FACT.**
- Para leer el CMD_MASK: enviar COMMAND_CAPABILITY con
  `{field_id=1(QUERY_COMMAND), len=4, val=0xFFFFFFFF}`; la respuesta REPACK trae
  `{field_id=3(CMD_MASK), ...}` con un bit por sub_command soportado. **Decodificar ese
  CMD_MASK EN VIVO resuelve el enum numérico entero.** (Es literalmente para lo que el
  comando existe.)

================================================================================
## 7. PROCEDIMIENTO EN VIVO PARA CERRAR LO UNKNOWN (accionable)
================================================================================
P1. **COMMAND_CAPABILITY (da el enum entero sin entrar en modo):**
    - `0x4B 0x0B  27 00  SS 00  00 00`  con SS = 0, luego 5, luego 6 (num_tlv=0).
    - El SS que responda con payload REPACK (no 0x14) = COMMAND_CAPABILITY.
    - Repetir ese SS con num_tlv=1, TLV `{01 00, 04 00, FF FF FF FF}` (QUERY_COMMAND).
    - Leer CMD_MASK (field_id=3): bit N=1 ⇒ sub_command N existe. **Resuelve el enum.**
P2. **Confirmar 0x14 = err_rsp DIAG:** capturar la respuesta completa. Si es
    `14 4B 0B 27 00` (~5 bytes) ⇒ DIAG_BAD_PARM con eco del original.
P3. **Tech-enter LTE:** para cada sub∈{0,5,6}, enviar
    `{SUB=0}{TECH=t}{SCENARIO=0}` barriendo t=0..12; tras cada intento mandar
    RADIO_CONFIG (el sub que P1 marque como config). El (sub_enter, t) que haga que
    RADIO_CONFIG DEJE de dar 0x14 y emita `[FTM.RFTEST][RADIO_CONFIG][UNPACK]` = el
    enter-mode + el valor de **TECH_LTE**. (NR5G idéntico bajo 0x8000/0x8001, t=0..15.)
P4. **RADIO_CONFIG mínimo:** ya entrado, agregar TLVs y quitar de a uno hasta hallar el
    set obligatorio; luego RX_MEASURE/IQ_CAPTURE → captura IQ.

================================================================================
## 8. FACT / INFERENCE / UNKNOWN (cierre honesto)
================================================================================
**FACT (probado en esta imagen):**
- La herramienta q6zip funciona y su formato está entendido (dict+ptrlist+bit-packed).
- seg26 (0xcc000000, 38 MB) es el backing comprimido pero **carece de metadata
  q6zip/delta recuperable en el ELF** (6 barridos ⇒ 0 tablas de páginas). No se pudo
  descomprimir. `seg26_dec.bin` NO producido.
- El dispatcher 0xd8150ed8 está en la ventana virtual paginada 0xd8xxxxxx, ausente de
  todo program header. Descriptor de dlpager real desensamblado en 0xc8d0a338 (remap
  1:1, no compresión). PH24 (0xc9138000) es filesz=0 (región descomprimida en runtime).
- Camino RF-test: LTE=0x27, NR5G=0x8000/0x8001. Tech-enter = `ftm_rf_debug_tech_enter_exit.c`
  con TLV `[field_id][name][value]`, params {SUB,TECH,SCENARIO}.
- Nombres de sub-comando y de propiedades (secciones 3 y 6).
- COMMAND_CAPABILITY: QUERY_COMMAND=1, QUERY_PROPERTY=2, CMD_MASK=3, PROPERTY_MASK=4..7.

**INFERENCE:**
- sub_command de tech-enter ∈ {0,5,6} bajo 0x27 (probablemente 0); COMMAND_CAPABILITY
  también ∈ {0,5,6}. Correlación con el probe en vivo (0/5/6 = control).

**UNKNOWN (solo en vivo o descomprimiendo el q6zip de código, que no está en la imagen):**
- Número exacto de cada sub_command (RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY/
  TECH_ENTER).
- **Valor numérico de TECH para LTE y NR5G.**
- Nombre/dirección exactos del flag "tech_entered" que dispara el 0x14.
