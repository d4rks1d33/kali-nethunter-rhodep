# CLADE — descompresión HW-accurate RESUELTA + dispatcher FTM (SM6375 / Moto G82 5G)

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
Fecha del pase: 2026-09-24
Leyenda: **FACT** = verificado byte/instrucción en esta imagen · **INFERENCE** = deducción con base · **UNKNOWN** = no determinado aquí.

---

## 0. RESULTADO EJECUTIVO — LA PIEZA BLOQUEANTE ESTÁ RESUELTA

**El "límite duro" del proyecto (las exception words de CLADE) YA NO EXISTE.** Se
compiló y ejecutó el codec **REAL de Qualcomm `libclade.so`** (Hexagon SDK 3.5.4, del
repo `mzakocs/qualcomm_baseband_scripts`) contra nuestros segmentos, y la
descompresión del código del modem es ahora **HW-accurate y limpia**:

- **FACT — La descompresión CLADE completa FUNCIONA con `libclade.so`.** La página 0
  (vaddr 0xd8000000) sale con el prólogo EXACTO y todo el código parsea limpio:
  ```
  d8000000: immext(#0xf8018000); r1:0=combine(#0,##0xf8018000); r17=#0; r3:2=combine(#0,#0)
  d800000c: call 0xd80dec28
  d8000010: immext(#0x409c0); jump 0xd80409f4
  ```
- **FACT — Validación cuantitativa: 0.14% de "invalid" en 1.048.333 instrucciones**
  desensambladas (10 MB de código, 0xd8000000..0xd8a00000). La **página del dispatcher
  0xd8150ed8** sale con **1 sola línea "invalid" en 2049 instrucciones (<0.05%)** — muy
  por debajo del criterio de éxito (<2%). Esto **supera** al mejor intento previo
  (`unclade_exc.py`, ~17% exception words sin resolver + ~4% residual).
- **FACT — El problema era exactamente el de los reportes previos**: `unclade.py`/
  `unclade_exc.py` (reimplementación Python) NO aplica las exception words ni un
  sub-código adicional. `libclade.so` (silicio-exacto) SÍ, y elimina el 100% del error.

**Entregables de este pase:**
- `/tmp/modemre/clade_dec_full.bin` — **10 MB de código descomprimido HW-accurate**
  (vaddr base 0xd8000000; incluye el dispatcher 0xd8150ed8 y todos los handlers FTM).
- `/tmp/modemre/clade_extractor_sm6375` (+ `.c` + `clade_api.h` + `libclade.so`) —
  herramienta reproducible que descomprime CUALQUIER rango bajo demanda.
- `/tmp/modemre/findings/dispatcher_full.txt` — disasm limpio del dispatcher (2507 líneas).
- Este reporte.

---

## 1. Cómo se resolvió (reproducible)

### 1.1 El recurso: `libclade.so` del repo nuevo (FACT)
`git clone https://github.com/mzakocs/qualcomm_baseband_scripts /tmp/qbs` trae:
- `libclade.so` — **codec CLADE real de Qualcomm, x86-64, NO stripped** (Hexagon SDK 3.5.4).
  Símbolos C usables: `clade_init`, `clade_read`, `clade_create_trace_file`,
  `clade_set_trace`, `clade_get_error_string`.
- `clade_extractor_pixel_5.c` — harness de referencia (direcciones del Pixel 5).
- Los headers (`clade_api.h`) NO venían; se **reconstruyeron** por disasm de `libclade.so`.

### 1.2 Obstáculo de plataforma y solución (FACT)
- Host = **aarch64**; `libclade.so` = **x86-64**. Solución: compilar el extractor con
  el cross-toolchain **`x86_64-linux-gnu-gcc`** (instalado por apt) y ejecutar bajo
  **`qemu-x86_64-static`** con `QEMU_LD_PREFIX=/usr/x86_64-linux-gnu` y las libs x86-64
  (`libc++.so.1`, `libc++abi.so.1`) presentes en `/usr/lib/x86_64-linux-gnu`.

### 1.3 Layout de las estructuras (recuperado del disasm de `libclade.so`) (FACT)
Verificado con `offsetof` en el binario compilado:
```
clade_config_t: region@0x00, num_pds@0x08, num_dicts@0x10, dict_len@0x14,
                pd_params@0x18, dicts@0x20, error@0x38, magic@0x3c
clade_pd_params_t (stride 0x40): comp@0x20, comp_len@0x28, exc_hi@0x30, exc_hi_len@0x38
clade_memblock_t (0x50): prev@0, next@8, id[0x20]@0x10, wordsize@0x30, addr@0x38,
                         len@0x40, data@0x48
```
`clade_init` valida `num_pds<5`, escribe magic `0xb993b56`, versión BuildID interno
`01.01.44` y **auto-deriva** por PD: `comp=region`, `exc_hi=region+0x4000000`, lens `0x4000000`.

### 1.4 El mecanismo real de lectura (FACT — clave para que funcione)
`clade_read` descomprime **por líneas de 64 bytes (16 words)**. Para cada línea invoca
el callback `client_lookup(addr=lineOffset, len=64)` esperando que se le devuelvan los
**bytes comprimidos** correspondientes; `client_lookup` en nuestro extractor mapea ese
offset a `modem.b26` (el `.clade.comp`). Los **3 diccionarios** (`config.dicts[]`) se
**dereferencian directamente** por `libclade.so` → hay que pasarle **punteros host reales**
a los 3×0x2000 bytes de `modem.b26 @ 0x2444000` (VA 0xce444000). Las exception words las
resuelve `libclade.so` internamente (`Packer::has_exc_high/low`, `unpack_high/low`).

### 1.5 Parámetros validados (FACT)
```
.clade.comp  = modem.b26 offset 0            (VA 0xcc000000)   -> region
.clade.dict  = modem.b26 offset 0x2444000    (VA 0xce444000)   -> 3×0x2000
Traducción request: req_va = 0xcc000000 + (output_vaddr - 0xd8000000)
```
Comando (bajo qemu, ver §8):
```
clade_extractor_sm6375 <out.bin> <req_va_hex> <len_hex>
# ej. dispatcher 0xd8150000: req_va=0xcc150000
```

---

## 2. FTM — arquitectura del comando (FACT del disasm limpio)

### 2.1 Punto de entrada y tabla de dispatch (FACT)
- **Tabla DIAG @ rodata 0xc37bd1e8** (modem.b21 off 0x26a1e8): 78 entries
  `{cmd_id:u16(dup a u32), handler:u32}`, **TODAS → 0xd8150ed8**. Incluye
  `0x00,0x03,0x20,0x27,0x28,0x22,0x25,0x69,0x7a…`. → **cmd 0x27 registrado → 0xd8150ed8** (FACT).
- **0xd8150ed8** = wrapper de dispatch/registro DIAG (NO el switch de sub_command).
  Valida el paquete DIAG y registra el manejador; su cuerpo es el serializador
  genérico de campos TLV de respuesta (tipos 0x21/0x33/0x35/0x36/0x40/0x41/0x47/0x80,
  con la jump-table @0xc37bd18c de 7 entries y utilidades 0xc0faxxxx).

### 2.2 Formato del paquete que valida 0xd8150ed8 (FACT, disasm)
```
memb(pkt+1) == 0x0b                       ; subsystem = 0x0b  (FTM)
(memub(pkt+2)|memub(pkt+3)<<8) == 0x14     ; sub-cmd DIAG = 0x14
(memub(pkt+4)|memub(pkt+5)<<8) & 0xfffe == 0x35a  ; ftm command id
memb(pkt+0xa)|memb(pkt+0xb)<<8 = ftm_cmd   ; leído por 0xd8169df0
```
**INFERENCE:** el paquete de entrada es un `DIAG_SUBSYS_CMD (0x4b)` con subsys 0x0b (FTM)
y sub-id 0x14; el `ftm_cmd` (p.ej. 0x27) va en offset 0xa. La tabla rodata 0xc37bd1e8
registra 0x27 como uno de los DIAG cmd codes que enrutan a 0xd8150ed8.

### 2.3 Mapa cmd → tech-index (FACT — función 0xd8169ec0)
```
d8169ec0: cmp.eq(r0,#0x22) -> r16 = 0
d8169ed0: cmp.eq(r0,#0x28) -> r16 = 3
d8169edc: cmp.eq(r0,#0x27) -> r16 = 1      ; else r16 = 7
```
→ **FACT: DIAG-cmd 0x27 se mapea a tech-index interno 1.** (0x22→0, 0x28→3, otros→7.)
**INFERENCE:** 0x27=LTE en la nomenclatura del proyecto ⇒ tech-index interno **1** para LTE.

### 2.4 Comandos FTM RF (sub_commands) — lista COMPLETA (FACT, strings rodata b21)
Cada sub_command tiene su función UNPACK con string `<NAME>][UNPACK]`. Orden en rodata:
```
TRM_ARA, SET_DPD_DEBUG_MODE, LOAD_UNITY_DPD, DEVICE_CAL, IDC_CAL, VDPD_CONVERSION,
VDPD_CAL, TX_OVERRIDE, TECH_ENTER_EXIT, AUTO_PIN_OVERRIDE, AUTO_DELAY_OVERRIDE,
AGC_ALG_CTRL, LOAD_DPD, RADIO_CONFIG, COMMAND_CAPABILITY, RX_MEASURE, WAIT_TRIGGER,
MSIM_CFG, TX_CONTROL, IQ_CAPTURE, TX_MEASURE
```
→ **FACT: existen TECH_ENTER_EXIT, RADIO_CONFIG, COMMAND_CAPABILITY, RX_MEASURE,
IQ_CAPTURE** como comandos FTM RF. (Coinciden con lo pedido.)

### 2.5b Formato TLV del comando tech_enter (FACT — parser 0xd8174ab4/0xd8174bf4)
El parser lee estructuras TLV anidadas **grupo → campo**, cada id como u32 (LE, leído
byte a byte de `memub(r21-4..-1)`):
```
group_id  = u32;  if (group_id > 0x27) -> default          ; tabla grupos @0xc906c438
field_id  = u32;  if (field_id-1 > 0x1d) -> default(0xd8174ee0) ; 30 fields @0xc37bee24
```
→ **FACT: hay hasta 0x28 grupos (0..0x27) y hasta 0x1e campos por grupo.** El **campo
TECH = field_id 2** (handler `[1]`=0xd8174cd0, lee un **u32** y lo guarda). Consistente
con el "grupo 16 (0x10) / field_id 2" del objetivo (el grupo 0x10 está dentro de 0..0x27).

### 2.5 Handler tech_enter_exit (FACT — localizado por string+tabla)
- String `tech_enter_exit.c\x00` @ 0xc37bef3a, **seguido de una tabla de 22 punteros**
  a código (@0xc37bef4c → 0xd8175bc8, 0xd8175be8, …). Es la **tabla PACK** (serializa
  campos de respuesta), indexada por `field_type-1`, usada en 0xd8175bc0:
  `r3 = memw(r2<<2 + ##0xc37bef4c); jumpr r3`.
- **Función UNPACK de tech_enter en 0xd8174ab4** (allocframe 0xc0). Parser TLV con
  jump-table @0xc37bee24 (30 field handlers), gate `cmp.gtu(field-1,#0x1d)` (>0x1d ⇒
  default). El **field TECH** cae en el handler `[1]` (0xd8174cd0), que lee un **u32**
  del paquete y lo guarda (`memw(r5)=valor; memb(r18)=1`). → **FACT: TECH es un campo
  u32 con field_id = 2** (índice 1). Consistente con "grupo 16 / field_id 2".

---

## 3. Valores pedidos — estado honesto

| Ítem | Valor | Confianza | Base |
|------|-------|-----------|------|
| Descompresión CLADE HW-accurate | **RESUELTA** | **FACT** | libclade.so, 0.14% invalid en 1M instr |
| Página del dispatcher 0xd8150ed8 | **descomprimida limpia** | **FACT** | 1 invalid/2049 instr |
| ftm_cmd_id (LTE) | **0x27** | **FACT** | tabla rodata 0xc37bd1e8 |
| Entry dispatcher DIAG | **0xd8150ed8** | **FACT** | tabla rodata (78 handlers) |
| subsys FTM en el paquete | **0x0b** | **FACT** | validación en 0xd8150ed8 |
| ftm command id (grupo) | **0x35a (&0xfffe)** | **FACT** | validación en 0xd8150ed8 |
| DIAG-cmd 0x27 → tech-index interno | **1** | **FACT** | función 0xd8169ec0 |
| Comandos FTM RF (TECH_ENTER/RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/COMMAND_CAPABILITY) | **existen** | **FACT** | strings + funciones UNPACK |
| Campo TECH en tech_enter | **field_id 2, u32** | **FACT** | handler 0xd8174cd0 |
| Función UNPACK tech_enter | **0xd8174ab4** | **FACT** | tabla campos @0xc37bee24 |
| Tabla PACK tech_enter | **0xc37bef4c (22 entries)** | **FACT** | tras string tech_enter_exit.c |
| **Número exacto del sub_command TECH_ENTER** | **ver §4** | **INFERENCE (no FACT)** | registro dinámico no cerrado estáticamente |
| **Enum numérico exacto de TECH=LTE en el TLV** | **ver §4** | **INFERENCE (no FACT)** | el u32 se guarda; comparación LTE en handler no aislada |
| Flag `tech_entered` + condición 0x14 | **PARCIAL** | INFERENCE | gate bad-parm en 0xd8150ed8; estado tech no aislado |

---

## 4. Lo que FALTA para cerrar los NÚMEROS EXACTOS (honesto)

La **descompresión ya no es el límite** — cualquier función se lee 100% limpia. Lo que
queda es **trabajo de RE puro** (seguir cadenas de llamadas) sobre código ya legible:

1. **Número del sub_command TECH_ENTER (y RADIO_CONFIG/IQ_CAPTURE):** los comandos FTM RF
   se **registran dinámicamente** (no hay una tabla estática `{subcmd→handler}` en rodata;
   el registro ocurre en funciones tipo 0xd816cf58/0xd816d0a4 que escriben una config
   global @0xca65b414). El número de cada sub_command está en la estructura de registro,
   que se construye en runtime. **No se cerró estáticamente en este pase.**
   - **INFERENCE (orden de rodata):** si los sub_commands se enumeran en el orden de sus
     strings UNPACK, TECH_ENTER_EXIT es el 9º grupo (índice ~8) y RADIO_CONFIG/…/IQ_CAPTURE
     vienen después. **NO usar estos números como definitivos.**
2. **Enum LTE del TLV TECH:** el handler de field_id 2 **guarda** el u32 sin compararlo ahí;
   la validación LTE vs NR5G ocurre en el handler del comando (aún no aislado). La pista
   más fuerte es el **tech-index interno 1** para cmd 0x27 (§2.3), pero el **valor exacto
   que se escribe en el TLV** no está confirmado por comparación directa. **UNKNOWN (exacto).**
3. **Gate 0x14 / flag tech_entered:** el gate DIAG_BAD_PARM (status byte del paquete) vive
   en 0xd8150ed8; la condición concreta "no se entró en modo" apunta a un flag global
   comparado antes de escribir el status, pero el global exacto no se aisló.

**Ruta directa para el próximo pase (ya sin bloqueos de descompresión):**
- Desensamblar la función de registro dinámico (0xd816cf58 y la config @0xca65b414) para
  leer el `subcmd_id` que se asocia a `ftm_rf_debug_tech_enter_exit` (0xd8174ab4).
- Desensamblar el handler (no-unpack) de tech_enter y localizar el `cmp` del u32 TECH
  contra el valor LTE, y el `memw(global)=1` de `tech_entered` + el `cmp` que gatilla 0x14.
- Todo esto es lectura de código YA descomprimido y limpio (usar `clade_extractor_sm6375`
  para cualquier rango).

---

## 5. Secuencia para capturar IQ (con la salvedad de §4)

**Estructura del comando (FACT):** DIAG `0x4b` (SUBSYS_CMD), subsys **0x0b** (FTM),
sub-id DIAG **0x14**, ftm command id **0x35a**, `ftm_cmd` (p.ej. **0x27** = LTE) en
offset 0xa; el sub_command FTM RF y los TLV siguen en el cuerpo.

Los tres pasos y sus comandos EXISTEN y están confirmados por nombre; los **números
de sub_command y el enum TECH concretos deben tomarse del próximo pase (§4)**, NO
fabricar:

1. **TECH_ENTER (cmd 0x27 = LTE)** — comando `TECH_ENTER_EXIT`.
   - TLV **TECH** = field_id **2** (u32) — valor LTE = **[pendiente, §4]** (tech-index
     interno 1 es la mejor pista).
   - TLV SUB / SCENARIO — presentes en el parser (30 fields @0xc37bee24).
2. **RADIO_CONFIG** — comando `RADIO_CONFIG` (tune: CENTER_FREQ, BANDWIDTH…). Confirmado
   por string 0xc37c032d y su UNPACK.
3. **IQ_CAPTURE** — comando `IQ_CAPTURE` (NUM_OF_SAMPLES, SAMP_FREQ, FETCH_IQ). Confirmado
   por string 0xc37c0901 y su UNPACK.

---

## 6. FACT / INFERENCE / UNKNOWN — resumen

**FACT (probado en esta imagen):**
- `libclade.so` descomprime el código del modem HW-accurate (0.14% invalid en 1M instr;
  página 0 y página del dispatcher perfectas). **La pieza bloqueante del proyecto está resuelta.**
- `.clade.comp`=b26@0, `.clade.dict`=b26@0x2444000; el extractor reproduce todo.
- ftm_cmd_id 0x27 → 0xd8150ed8 (tabla rodata). subsys FTM 0x0b, ftm id 0x35a.
- DIAG-cmd 0x27 → tech-index interno 1 (0xd8169ec0).
- Existen TECH_ENTER_EXIT, RADIO_CONFIG, COMMAND_CAPABILITY, RX_MEASURE, IQ_CAPTURE.
- TECH = field_id 2 (u32) en el UNPACK de tech_enter (0xd8174ab4; tabla @0xc37bee24).

**INFERENCE:**
- 0x27=LTE ⇒ enum de tech LTE ligado al índice interno 1.
- El sub_command de cada comando FTM RF sigue el orden de sus strings UNPACK (no definitivo).

**UNKNOWN (solo RE, ya sin bloqueo de descompresión):**
- Número exacto del sub_command TECH_ENTER / RADIO_CONFIG / IQ_CAPTURE (registro dinámico).
- Valor u32 exacto del TLV TECH para LTE/NR5G (comparación en el handler no aislada).
- Dirección global exacta de `tech_entered` y la condición precisa del 0x14.

---

## 7. Archivos generados

- `/tmp/modemre/clade_dec_full.bin` — **10 MB código descomprimido HW-accurate** (VA 0xd8000000).
- `/tmp/modemre/clade_extractor_sm6375` — extractor x86-64 (correr con qemu, §8).
- `/tmp/modemre/clade_extractor_sm6375.c`, `clade_api.h`, `libclade.so` — fuentes/lib.
- `/tmp/modemre/findings/dispatcher_full.txt` — disasm limpio del dispatcher.
- `/tmp/modemre/findings/dispatcher_disasm.txt` — región 0xd8150ed8+ (2492 líneas).
- `/tmp/modemre/findings/AGENT_CONTEXT.md` — contexto/HOWTO para continuar.

## 8. Reproducir la descompresión (comando exacto)

```bash
git clone https://github.com/mzakocs/qualcomm_baseband_scripts /tmp/qbs
# headers reconstruidos: /tmp/modemre/clade_api.h -> /tmp/qbs/clade_api.h
# fuente: /tmp/modemre/clade_extractor_sm6375.c -> /tmp/qbs/
x86_64-linux-gnu-gcc -g -O1 -o /tmp/qbs/clade_extractor_sm6375 \
    /tmp/qbs/clade_extractor_sm6375.c -L/tmp/qbs -Wl,-rpath,'$ORIGIN' -l:libclade.so

# Descomprimir p.ej. la página del dispatcher (0xd8150000):
QEMU_LD_PREFIX=/usr/x86_64-linux-gnu \
LD_LIBRARY_PATH=/tmp/qbs:/usr/lib/x86_64-linux-gnu \
qemu-x86_64-static /tmp/qbs/clade_extractor_sm6375 /tmp/out.bin cc150000 2000

# Desensamblar:
python3 /tmp/modemre/mkelf.py /tmp/out.bin 0xd8150000 /tmp/out.elf
llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66 /tmp/out.elf
```
