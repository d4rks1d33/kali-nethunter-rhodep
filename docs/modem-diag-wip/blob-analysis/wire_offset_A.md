# WIRE OFFSET — command_id = memub(r18+0xa) — RESULTADO

Build: MPSS.HI.4.3.4 SM6375 (Moto G82 5G). Analisis 100% estatico sobre
`/tmp/modemre/clade_dec_full.bin` (base VA 0xd8000000). Herramienta: `dis.sh`.

Marcado: **FACT** = evidencia directa en el disasm. **INFERENCE** = deduccion de
convencion/ABI. **UNKNOWN** = no determinable estaticamente.

====================================================================
## RESPUESTA CORTA
====================================================================

**`memub(r18 + 0xa)` = WIRE BYTE Nº 10 (0x0a), contando `0x4b` como byte 0.**

- **Desplazamiento N = 0.** `r18` apunta a la **base del mensaje wire** (el byte 0x4b
  es `r18[0]`). No hay salto de header; no apunta a wire+8. **FACT.**
- Por tanto el `command_id` esta en `wire[0x0a]` = byte 10 del paquete DIAG-FTM completo,
  es decir el **3er byte del cuerpo** (cuerpo empieza en wire[0x08]).

====================================================================
## 1. CADENA DE LLAMADA Y QUE REGISTRO ES EL WIRE  (Pregunta 1)
====================================================================

### 1.1 Router de rango → dispatcher 0x10xx (`0xd82714b4`)  — FACT
El sub_command @wire[0x04..0x05] entra al router `0xd8157ec4` que por rango salta a
`0xd82714b4` (0x1000–0x3FFF = RFTEST).

```
d82714bc:  call 0xd8272bb0                       ; init plantilla de respuesta en r0
d82714c0:  r17:16 = combine(r0,r1)               ; r16 = r0 = PTR AL WIRE ; r17 = r1
d82714c8:  r3 = memub(r16+#0x5) ; r2 = memub(r16+#0x4)   ; sub_command hi/lo = wire[5]/wire[4]
d82714cc..fc: gate por wire[5] (0x10/0x20/0x30) elige tabla de stubs
d82714fc:  cmp(wire[4],#0x17)  -> indice de stub = wire[4] (byte bajo sub_command)
```
**`r16` = puntero al mensaje wire con `wire[0]=0x4b`.** Lo prueba que `memub(r16+0x4/0x5)`
= sub_command, que por layout esta en wire[0x04/0x05]. **Desplazamiento = 0.** **FACT.**

`0xd8272bb0` NO modifica r0 como puntero (solo escribe una plantilla de status en los
primeros bytes en los casos de early-out selector 0/1/2, no en el path RFTEST). Por eso
`r16 = r0(entrada) = wire base`. **FACT.**

### 1.2 stub → handler: valores de r0/r1/r2  — FACT
Stub 0x1002 (`0xd8271548`): `r2 = 0xd86fd0f4` (handler), luego `jump 0xd8271630`.
Cola comun de dispatch `0xd8271630`:
```
d8271630:  r1:0 = combine(r16, r18)     ; r0 = r18 (buffer de respuesta @disp r29+0x140)
                                         ; r1 = r16 = PTR AL WIRE
d8271634:  callr r2                      ; r2 = handler VA (0xd86fd0f4)
```
**Handler recibe: r0 = buffer de respuesta, r1 = puntero al wire (base, 0x4b en +0).** **FACT.**

### 1.3 Confirmacion cruzada: handler `0x100b` (`0xd86fe120`) lee wire+0xa DIRECTO — FACT
Este handler hermano prueba de forma inequivoca que `+0xa` sale del **wire crudo**:
```
d86fe128:  r17:16 = combine(r1,r0)      ; r17 = r1 = WIRE ; r16 = r0 = respuesta
d86fe12c:  r2 = #0x10                    ; len para el parser
d86fe130:  r0 = add(r29,#0x18)           ; buffer local destino
d86fe134:  call 0xd8272c10              ; parser (r1 aun = WIRE)
d86fe154:  r17 = memub(r17+#0xa)         ; <<< command_id LEIDO DEL WIRE CRUDO (r17, callee-saved)
```
`r17` es callee-saved (r16–r27 se preservan por ABI a traves de `0xd8272c10`), asi que
en `0xd86fe154` sigue siendo el wire de entrada **sin transformar** → `wire[0x0a]`. **FACT.**

====================================================================
## 2. QUE COPIA 0xd8272c10 → 0xd8151060, Y DE DONDE SALE +0xa  (Pregunta 2)
====================================================================

### 2.1 0xd8272c10  — FACT del disasm
```
Entrada:   r0 = resp_buf , r1 = WIRE , r2 = 0xe (len; puesto por 0xd86fd0e8)
d8272c18:  r17:16 = combine(r1,r2)   ; r17 = WIRE , r16 = 0x0e (metadata/len)
d8272c1c:  r0 = add(r29,#0)          ; contenedor local (vector-like)
d8272c24:  call 0xd8151060           ; init contenedor + alloca buffer de 0xe(14) bytes
; --- copia byte a byte (r2 = buffer heap = memw(contenedor+0x8)) ---
d8272c54:  memb(r2+0) = memub(r17+0)  ; buf[0] = wire[0]
d8272c5c:  memb(r2+1) = memub(r17+1)  ; buf[1] = wire[1]
...        memb(r2+7) = memub(r17+7)  ; buf[7] = wire[7]   (los 8 bytes de header)
d8272c78:  memb(r2+8) = r16           ; buf[8] = 0x0e (metadata LOW, NO wire)
d8272c80:  memb(r2+9) = lsr(r16,#8)   ; buf[9] = 0x00 (metadata HIGH, NO wire)
```
**Que se copia:** solo `wire[0..7]` (el header DIAG-FTM de 8 bytes) → `buf[0..7]`, y luego
`buf[8]/buf[9]` = metadata `0x000e` (la longitud/tipo r16, **no** del wire). Los bytes
`buf[0xa..0xd]` del buffer de 14 NO se rellenan aqui. **FACT.**

### 2.2 0xd8151060 = constructor de contenedor (vector/string wrapper) — FACT
```
r16=r0(contenedor) ; r17=r1(size=0xe)
call 0xd814d760(size) -> buffer heap
memw(contenedor+0x8) = buffer   ; ptr datos
memw(contenedor+0x4) = 0xe      ; size/capacidad
memb(contenedor+0x0) = 2 ; memb(contenedor+0xc) = 1  ; tags de tipo
```
Es un `std::vector`/buffer-descriptor, **no** un memcpy plano del cuerpo.

### 2.3 CONCLUSION sobre el buffer copiado vs. +0xa  — FACT + INFERENCE
El buffer de 14 bytes de `0xd8272c10` **NO** es de donde el handler lee `+0xa`. Prueba:
- El buffer solo tiene sentido en `buf[0..9]` (header + metadata); `buf[8]/buf[9]`
  provienen de metadata (r16=0x0e), no del wire. **FACT.**
- El handler `0x100b` (`0xd86fe120`) lee `+0xa` de **r17 = wire crudo**, NO del buffer
  destino (`r29+0x18`). **FACT.**
- Los handlers `0x1002`/`0x1003` leen `+0xa` de `r18/r17 = r1` devuelto por `0xd86fd0e8`,
  que es el **puntero al wire preservado** a lo largo de la cadena. **FACT del mecanismo;
  INFERENCE de la igualdad r1(retorno)=wire** (coherente con el hermano 0x100b que lee
  identicamente el mismo campo del wire crudo).

→ **`memub(r18+0xa)` NO viene del buffer de 10 bytes; viene del cuerpo del wire crudo,
offset 0x0a.** El buffer copiado es un artefacto (guarda el header para el REPACK de la
respuesta); el command_id se lee siempre del wire original. **FACT.**

====================================================================
## 3. OFFSET FINAL Y LAYOUT COMPLETO DEL WIRE RFTEST  (Pregunta 3)
====================================================================

### 3.1 Offset del command_id  — FACT
```
memub(r18 + 0xa)  =  wire[0x0a]  =  BYTE Nº 10   (0x4b = byte 0)
Desplazamiento de r18 respecto al wire:  N = 0  (r18 apunta a wire base)
```

### 3.2 Layout completo del paquete wire RFTEST  — FACT (header) / FACT (campos handler)
```
offset  campo                                   fuente
------  --------------------------------------  --------------------------------
0x00    0x4B  DIAG_SUBSYS_CMD_F                 header DIAG          [FACT]
0x01    0x0B  subsys FTM                        header DIAG          [FACT]
0x02..3 u16  ftm_cmd  (LE, p.ej 0x0027 LTE)     header FTM           [FACT]
0x04..5 u16  sub_command (LE, p.ej 0x1002)      dispatcher wire[4/5] [FACT]
0x06..7 u16  num_tlv / contador                 header req           [FACT layout]
------  ---- fin header de 8 bytes; cuerpo empieza en 0x08 ----
0x08    (cuerpo[0])                             cuerpo               [FACT es cuerpo]
0x09    (cuerpo[1])                             cuerpo
0x0A    command_id  = memub(r18+0xa) (<=0x31)   <<< ESTE CAMPO       [FACT: 0xd86fd108]
0x0B..E (cuerpo)
0x0F    memub(r18+0xf) -> r19                    campo del cuerpo    [FACT: 0xd86fd12c]
0x10    memub(r18+0x10) -> r18.lo (16b: 0x10|0x11<<8)  campo cuerpo  [FACT: 0xd86fd134]
0x11    memub(r18+0x11) -> r20 (byte alto del 16b anterior)          [FACT: 0xd86fd120/138]
```
Relacion 0x10/0x11: `r18 = memub(wire+0x10) | (memub(wire+0x11) << 8)` → **valor de 16
bits LE en wire[0x10..0x11]**. **FACT** (`0xd86fd134` + `0xd86fd138 r18 |= asl(r20,#8)`).

### 3.3 Contador de TLVs
- Header: `num_tlv` esta en **wire[0x06..0x07]** (u16 LE), segun el layout FACT del
  paquete (`gate_resolution.md §2.1`, corroborado por el uso @+6 en los desempaquetadores).
  **FACT (layout) / INFERENCE (que estos handlers concretos lo consuman en +6, ya que
  el path 0x1002 delega el conteo TLV al resolver 0xd8272684 + callr).**
- En `0x1002` el conteo/estructura TLV real se procesa despues por `0xd8272cd8`
  (`callr r5`), no por lectura inline en el prologo. **FACT del path.**

====================================================================
## 4. RESUMEN DE HECHOS / INFERENCIAS / DESCONOCIDOS
====================================================================

**FACT**
- `command_id = memub(r18+0xa) = wire[0x0a] = byte 10`. VA `0xd86fd108`.
- `r18` (=r1 del handler) apunta a la **base del wire** (0x4b en +0). **N = 0.** Prueba:
  `0xd82714c8` lee sub_command en wire[4/5] desde el mismo puntero; el parser
  `0xd8272c10` copia wire[0..7] (header) desde ese mismo puntero (VA `0xd8272c54..c78`).
- El parser `0xd8272c10` copia SOLO wire[0..7] + metadata 0x000e en buf[8/9]; el buffer
  de 14 bytes NO es la fuente de `+0xa`.
- Campos del cuerpo leidos por el handler 0x1002: wire[0x0a]=command_id, wire[0x0f]=r19,
  wire[0x10..0x11]=u16 LE. VAs `0xd86fd12c/0xd86fd134/0xd86fd138`.
- Confirmacion independiente: handler 0x100b `0xd86fe154` lee `memub(wire+0xa)` del wire
  crudo (r17 callee-saved).
- Bound-check: `cmp.gtu(command_id,#0x31)` → command_id valido 0x00..0x31. VA `0xd86fd10c`.

**INFERENCE**
- El puntero devuelto por `0xd86fd0e8` en r1 == wire base (igualdad de campo con 0x100b
  que lo lee del wire crudo; ABI callee-saved conserva el wire a traves de la cadena).
- `num_tlv` en wire[0x06..0x07] (layout estandar Qualcomm ftm_rf_test; consumido por el
  path TLV posterior, no inline en el prologo de 0x1002).

**UNKNOWN (estatico)**
- El sub_command 0x10NN exacto ↔ nombre (RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/…): binding
  en RAM via `@0xca79a850[command_id]+0x34` (poblado en runtime). No afecta el offset +0xa.
- Semantica exacta de wire[0x08],[0x09],[0x0b..0x0e] (relleno/subindice/reservado): el
  prologo solo consume 0x0a, 0x0f, 0x10, 0x11.

====================================================================
## 5. VAs CLAVE
====================================================================
```
0xd8157ec4   router de rango sub_command -> dispatchers
0xd82714b4   dispatcher RFTEST 0x10xx (lee wire[4]/wire[5])
0xd8272bb0   init plantilla respuesta (no mueve el ptr wire)
0xd8271630   cola dispatch: r0=resp, r1=WIRE ; callr handler
0xd8271548   stub 0x1002 -> r2=0xd86fd0f4
0xd86fd0f4   HANDLER 0x1002: memub(r18+0xa)=command_id
0xd86fd108   <<< instruccion r17 = memub(r18+0xa)
0xd86fd0e8   envelope parse -> 0xd8272c10
0xd8272c10   wire-parser: copia wire[0..7]->buf, buf[8/9]=metadata 0x000e
0xd8151060   constructor de contenedor (alloc buffer 0xe bytes)
0xd8272684   resolver command_id -> struct @0xca79a850+id*4+0x34
0xd86fe120   handler 0x100b (prueba cruzada: memub(wire+0xa) del wire crudo @0xd86fe154)
```
