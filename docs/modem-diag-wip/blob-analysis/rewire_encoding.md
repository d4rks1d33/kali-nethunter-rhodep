# rewire_encoding.md — Encoding byte-exacto del disparo de re-cableo del endpoint COMMAND (SM6375, MPSS.HI.4.3.4)

**Build:** MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK · SM6375 · Hexagon v66
**Imágenes:** `_dis_b13.txt` (diag core, VA 0xc0d36000+), `_dis_b08.txt` (0xc0a0420c @ 0xc09fb000+), rodata seg21 (`modem_full.elf`, VA 0xc3553000).
**Verificación:** cada VA con `grep -n "^<VA>:" _dis_bNN.txt`; tablas con `vmap.py` sobre `modem_full.elf`.
**Leyenda:** **FACT** = leído del disasm/rodata (VA citada) · **INFERENCE** = deducción consistente · **UNKNOWN** = sólo en vivo.

> **CONCLUSIÓN PRINCIPAL (corrige `force_rewire.md` §3.3/§3.4 y `diag_transport_full.md`):**
> `0xc0d7fcdc` **NO es un parser de ctrl-msg DIAG_CTRL sobre QRTR, y `0xc0a0420c` NO es un scanf ni un sprintf de texto.**
> - `0xc0a0420c` es el **emisor de mensajes F3 QShrink 2.0 (`msg_v2`)** — 284 llamadas en el diag core, con lookup de tabla en `pc+0xfffe6354` (VA 0xc09ea564) vía `bitsplit(r1,#0xc)`. Es logging, no parsing. **FACT** (`_dis_b08.txt:9319-9349`).
> - `0xc0d7fcd4/0xc0d7fcdc` es el **dispatcher del driver QDI (`qurt_qdi`) del subsistema `diag_qdi`** (inter-PD, root-PD ↔ user-PD/kernel). `r6`/`r7`/`r20`/... son **argumentos de método QDI en registros**, NO campos de un datagrama QRTR. Strings del cluster: `diag_qdi_release`, `diag_qdi_init_diagbuffer`, `diag_mpd_cleanup`, `_qdi_diagbuffer_add_to_drain_queue`, `i_get_diagID`. **FACT** (rodata 0xc35ce080/0c0/0de/1c0/200/240/400).
>
> **Por lo tanto NO existe un "ctrl-msg byte-exacto para mandar por CNTL que re-cablee COMMAND".** El re-cableo se dispara por una **invocación QDI local (trap `qurt_qdi_handle_invoke`)** hecha por un cliente diag que corre **dentro del Hexagon** (kernel diag driver / user-PD), no por un socket QRTR desde el AP. Ver §6 para lo que esto implica operativamente.

---

## 0. TL;DR

| Pregunta | Respuesta corta |
|---|---|
| ¿El ctrl-msg r6=4/r20=17 es texto o binario? | **Ninguno.** No es un mensaje de wire. Son **args de una llamada QDI** en registros (r2 empaqueta r6+r7; r3=r20). **FACT.** |
| Format string de 0xc0a0420c | **No hay format string de parseo.** 0xc0a0420c es el emisor F3 QShrink; sus "strings" son logs (`"... channel_type = %d, io_type = %d, port_num = %d"`, etc.). **FACT.** |
| Cómo se obtienen r6/r20 | `r2 & 0xFF = r6` (comando), `(r2>>8)&0xFF = r7` (modo, debe ==1), `r3 = r20` (sub-op). Todos **argumentos de la función QDI 0xc0d7fcd4**. **FACT.** |
| Ctrl-msg byte-exacto para CNTL | **No aplica** (no llega por CNTL). El grafo de re-cableo es correcto pero se alcanza por **QDI trap intra-modem**, inaccesible desde un socket QRTR del AP. **FACT + INFERENCE.** |
| De dónde saca el re-cableo el node/port del nuevo peer | `0xc0d81af8` **NO lee node/port de ningún mensaje ni de 0xc8c2d870/74**; camina la lista de descriptores `0xc9508978` y **re-publica el servicio QRTR de cada canal** (`0xc0d5da84`), seteando marcadores globales `0xc950897c/7d`. El destino del `sendto` sigue siendo el global `0xc8c2d870/74` (NEW_SERVER). **FACT.** |

---

## 1. La ABI real de `0xc0d7fcd4` (entry verdadero) — FACT

El allocframe está en `0xc0d7fcdc`, pero el **entry real es `0xc0d7fcd4`**: hay un paquete de 2 instrucciones justo antes del allocframe, después del `dealloc_return` de la función previa (`0xc0d7fcd0`), que preparan los args y **caen por fall-through** al allocframe:

```
c0d7fcd4: r7:6 = bitsplit(r2,#0x8)     ; r6 = r2 & 0xFF ; r7 = (r2 >> 8) & 0xFF
c0d7fcd8: r16 = r0                       ; r0 = arg0 (buffer de reply/salida)
c0d7fcdc: allocframe(#0xc0) ; memd(sp-0x10)=r17:16   ; <-- prólogo
```
**FACT** (`_dis_b13.txt:75491-75493`).

Mapeo argumento→registro (del prólogo `0xc0d7fce0-0xc0d7fd10`):

| Arg | Reg destino | Uso | VA |
|-----|-------------|-----|----|
| r0 | r16 | buffer de salida/reply (arg0 a los F3 y a helpers) | c0d7fcd8 |
| r1 | r24 | **"conn"/contexto** (r24+0x10=name, r24+0x20, r24+0x24=diagbuffer); r6-dispatch exige `r24!=0` | c0d7fd00 |
| r2 | r6=`r2&0xFF`, r7=`(r2>>8)&0xFF` | **r6 = COMANDO**, **r7 = MODO/dirección** | c0d7fcd4 |
| r3 | r20 | **SUB-OP** | c0d7fcf0 |
| r4 | r18 | longitud / count | c0d7fcfc |
| r5 | r22 | parámetro (en r20=4 se compara `==0x20`) | c0d7fce0 |
| stack +0xc8 | r21 | parámetro (buffer/ptr) | c0d7fd0c |
| stack +0xcc | r23 | parámetro (len/id) | c0d7fce8 |
| stack +0xd0 | r19 | parámetro | c0d7fd04 |

**Gate de nivel 0 (elección de familia por r7):** **FACT** (`_dis_b13.txt:75507-75513`)
```
c0d7fce4: p0 = cmp.eq(r7,#0x1)
c0d7fd14: if (p0) jump 0xc0d7fd3c        ; r7==1  -> dispatch por r6 (tabla 0xc35cdfec)  <== camino al re-cableo
c0d7fd1c: (r7==0) if (r6==2) call 0xc0d7fc50 ; sólo r6==2 válido cuando r7==0
c0d7fd20/2c: else -> 0xc0d804f8 (reject/default)
```
⇒ **Para llegar a r6=4 hace falta r7==1.** Es decir el word `r2` debe ser `0x0104` (r6=0x04, r7=0x01), o más en general `r2 = (r7<<8)|r6` con r7=1, r6=4 ⇒ **`r2 = 0x00000104`** (bits 16-31 no usados en el split). **FACT.**

**Dispatch r6 (nivel 1):** **FACT** (`_dis_b13.txt:75517-75525`)
```
c0d7fd3c: r3 = add(r6,#-0x1)
c0d7fd44: if (cmp.gtu(r3,#0x15)) jump 0xc0d804f8   ; r6 válido 1..22
c0d7fd50: r3 = memw(r3<<2 + ##0xc35cdfec) ; jumpr r3
```
Tabla `0xc35cdfec` (índice = r6-1), verificada de seg21: **r6=4 → 0xc0d8005c**. **FACT.**

**Dispatch r20 (nivel 2, sólo r6=4, rama 0xc0d8005c):** **FACT** (`_dis_b13.txt:75717-75727`)
```
c0d8005c: r17=0; if (r24==0) jump 0xc0d8052c   ; exige conn (arg1) != 0
c0d80070: r2 = add(r20,#-0x1)
c0d80078: if (cmp.gtu(r2,#0x10)) jump 0xc0d802ac ; r20 válido 1..17
c0d80080: r2 = memw(r2<<2 + ##0xc35ce044) ; jumpr r2
```
Tabla `0xc35ce044` (índice = r20-1), verificada: **r20=17 → 0xc0d80234**. **FACT.**

**Rama r20=17 → re-cableo:** **FACT** (`_dis_b13.txt:75835-75864`)
```
c0d80234: (log "get_diagID"/"failed for diagID"; usa r22 como param, r18/r21 como flags)
c0d8028c: r17 = add(r29,#0x38)      ; struct local
c0d80290: call 0xc104f4b8           ; r1=r17 -> invoca el handler del canal (callr) que llena/valida params
c0d80298: call 0xc0d80ba4           ; args: r0=r16, r1=r17(struct), r2=r21, r3=r23, r4=(r29+0x20)
```
`0xc0d80ba4`: `r16=r4`, `r17=r0`, `r0=r1(=struct r29+0x38)`, `call 0xc0d81af8` (`c0d80bb8`). **FACT** (`_dis_b13.txt:76439-76444`).

**Grafo (idéntico a force_rewire.md, confirmado byte a byte):**
```
QDI invoke(r2=0x0104, r3=17, ...)  →  0xc0d7fcd4  →(r7==1)→ 0xc0d7fd3c(dispatch r6)
   →  r6=4: 0xc0d8005c  →(r24!=0)→ 0xc0d80078(dispatch r20)  →  r20=17: 0xc0d80234
   →  0xc0d80290(call 0xc104f4b8) / 0xc0d80298(call 0xc0d80ba4)  →  0xc0d80bb8: call 0xc0d81af8  (RE-CABLEO)
```

---

## 2. `0xc0a0420c` es el emisor F3 QShrink 2.0, NO scanf/sprintf de texto — FACT

```
c0a0420c: immext(#0xfffe6340)
c0a04210: r14 = add(pc,##0xfffe6354)     ; r14 = 0xc09ea564 (tabla de ruteo de msg)
c0a04214: r13:12 = bitsplit(r1,#0xc)     ; descompone el selector de msg empaquetado en r1
c0a04220: r28 = memw(r14+#0x10) ; ... trap1(#0x2) si fuera de rango
c0a04244/50/5c: cadena de lookups anidados en la tabla -> jumpr r14 (tail-call al backend de msg)
```
**FACT** (`_dis_b08.txt:9319-9349`). Es el `__qsr_msg` / `msg_v2` (QShrink): recibe un **descriptor de constante de mensaje empaquetado** (r1 = ssid/línea codificados), NO un format string de texto que parsee bytes de payload. **284 call-sites** en el diag core (`_seg13_raw.bin`), típico del macro de log F3. **FACT.**

Los args de las llamadas dentro de `0xc0d7fcd4` (`r3:2 = combine(rX, #0x52/0x53/0x54/0x55)`, `r1:0 = combine(r16,#0)`) son **(ssid, line#, args…)** de logs F3 (líneas 0x52=82, 0x53=83, 0x54=84, 0x55=85 del archivo `diag_qdi`). **No hay ningún campo r6/r20 "extraído de un %d".** **FACT.**

⇒ **La premisa "el ctrl-msg puede ser TEXTO 'diag: … %d …'" es incorrecta.** No existe tal format string ni tal parseo. **FACT.**

---

## 3. Qué es `0xc0d7fcd4`: el dispatcher QDI de `diag_qdi` — FACT + INFERENCE

Cluster de funciones en el mismo archivo fuente (comparten el file-const `0xc35ce40b`="diag_mpd_cleanup Entered" y el func-name-ptr en `0xc0d7fac0`+): `0xc0d7fc50`, `0xc0d80ba4`, `0xc0d80ec0`, `0xc0d80fc8`, `0xc0d81cc8`, `0xc0d81af8`. Strings del archivo (rodata, **FACT**):

| VA | String |
|----|--------|
| 0xc35ce080 | `diag_qdi_release calling mpd cleanup START.` |
| 0xc35ce0c0 | `...release calling mpd cleanup DONE` |
| 0xc35ce0de | `diag_qdi_init_diagbuffer` |
| 0xc35ce0d2 | `malloc err for %d bytes` |
| 0xc35ce1c0 | `_qdi_diagbuffer_add_to_drain_queue: malloc failed.` |
| 0xc35ce200 | `..._qdi..i_get_diagID` |
| 0xc35ce240 | `...failed for diagID` |
| 0xc35ce40b | `diag_mpd_cleanup Entered` |
| 0xc35ce500 | `...channel_type = %d, io_type = %d, port_num = %d` |

El subsistema completo usa **`qurt_qdi`** (strings `qurt_qdi_devname_register`, `qurt_qdi_copy_from_user`, `qurt_qdi_handle_create_from_obj_t`, `QDI layer Misbehaved` — `_seg27_strings.txt:458-496`). **FACT.**

**INFERENCE (fuerte):** `0xc0d7fcd4` es el **método `invoke` del driver-object QDI de diag** (registrado con `qurt_qdi_devname_register`; el fn-ptr no aparece estático porque se instala en runtime en el objeto QDI — por eso **0 callers estáticos y 0 punteros a 0xc0d7fcd4/dc en toda la imagen**, verificado). Se ejecuta cuando un cliente **dentro del Hexagon** (kernel diag driver de la HLOS a través del canal glink/smd-diag, o un **user-PD/guest**) hace `qurt_qdi_handle_invoke(handle, method=r2, a1..a9)`. El QDI framework copia los args del llamador a r0..r5 + stack y salta al `invoke`. `r6`/`r7`/`r20` = los args del método. **INFERENCE.**

---

## 4. Layout "wire" de r6=4 / r20=17 (respuesta directa) — FACT

**No hay wire.** Es una llamada de función QDI en registros. La "codificación" para producir (r6=4, r20=17) en el dispatcher es:

```
método QDI (registro r2)  = 0x00000104     ; byte0=r6=0x04 (comando), byte1=r7=0x01 (modo)
arg (registro r3)         = 0x00000011      ; r20 = 17 (sub-op)
arg (registro r1)         = <ptr conn>      ; r24, DEBE ser != 0  (contexto de canal QDI)
arg (registro r0)         = <ptr reply buf> ; r16
arg (registro r4)         = <len/count>     ; r18
arg (registro r5)         = <param, p.ej 0x20 en algunos sub-ops> ; r22
stack args                = r21/r23/r19     ; params adicionales del canal
```
**FACT** (mapeo §1). El "conn" (r1→r24) es un **handle de canal QDI ya abierto** (creado por un `open`/`create` QDI previo del mismo cliente); sin él, la rama r6=4 retorna sin re-cablear (`c0d80060: if(r24==0) return`). **FACT.**

**No existe struct de payload con offsets porque no hay payload de datagrama.** La "struct" que consume el re-cableo (`r29+0x38`) la **rellena el propio handler del canal** invocado en `0xc104f4b8` (`callr r6` sobre `conn`), no viene de un buffer del AP. **FACT** (`_dis_b13.txt:76704-76756`, `811239-811270`).

---

## 5. Qué lee `0xc0d80ba4`/`0xc0d81af8` para el re-cableo (pregunta 6) — FACT

`0xc0d81af8` (el walker/rewire) **NO toma node/port de ningún mensaje ni de 0xc8c2d870/74.** Su cuerpo real (`0xc0d81b40`):

```
c0d81b58: r20 = memw(##0xc9508978)     ; head de la lista de descriptores de canal
c0d81bb4..c0d81ca8: itera cada descriptor 'r20':
    lee memb(r20+0x4/0x5) (marcadores node), memb(r20+0x7/0x8/0x9) (type/diag_id),
    r20+0x14 / r20+0x32 (nombres de servicio),
    call 0xc0d5da84  (=diag re-registro/publicación del servicio QRTR del canal)
c0d81cb0: memb(##0xc950897c) = r21     ; marcador global "rewire done" (node-side)
c0d81cb8: memb(##0xc950897d) = r22     ; marcador global "rewire done" (port-side)
```
**FACT** (`_dis_b13.txt:77420-77535`).

⇒ **El re-cableo re-publica el servicio de cada canal (incl. COMMAND) por QRTR**, dejando el descriptor listo para que QRTR re-descubra el peer. **El destino del `sendto` sigue saliendo del global `0xc8c2d870`(node)/`0xc8c2d874`(port)** que se puebla **exclusivamente** en NEW_SERVER (`0xc0d83018/20`, incondicional, sin path a 0). **FACT** (confirmado en `force_rewire.md` §4.1).

**Esto confirma la hipótesis operativa:** **node/port del nuevo peer COMMAND = tu NEW_SERVER** (global 0xc8c2d870/74). El QDI-rewire **NO** trae node/port propios; sólo re-abre/re-publica el endpoint para que vuelva a atender al peer del global vivo. Por eso "una vez funcionó tras parar MM": el kernel diag re-invocó QDI (re-registro de canal) → `0xc0d81af8` re-publicó COMMAND → QRTR re-resolvió al server vivo (tu NEW_SERVER). **FACT (mecanismo) + INFERENCE (correlación).**

---

## 6. Consecuencia operativa (honesta) — qué se puede y qué no

- **No se puede disparar el Camino B "mandando bytes por el socket CNTL"**, porque `0xc0d7fcd4` **no cuelga del dispatcher de ctrl-msg QRTR** (tabla `0xc35c9b78`, `diagpkt_process_ctrl_msg` @ 0xc0d66264). Ese dispatcher cubre cmd_type 3..0x65 y **no tiene entrada que salte a 0xc0d7fcdc** (verificado: sus targets son 0xc0d663xx..0xc0d675xx + default 0xc0d67794; `r6=4` del *ctrl-msg* va a **default 0xc0d67794**, no a nuestro parser). **FACT.**
- El re-cableo se dispara por una **invocación QDI local** hecha por:
  1. el **kernel diag driver de la HLOS** cuando (re)abre su canal diag hacia el root-PD (glink `DIAG_CTRL`/smd), o
  2. un **user-PD / guest** que registra su canal.
  Ambos corren *dentro* del Hexagon (o vía el transporte glink que el kernel usa), **no** replicables por un datagrama QRTR desde userspace del AP. **FACT (arquitectura QDI) + INFERENCE (quién invoca).**
- **Camino accionable desde el AP (sin reboot):** provocar que el **kernel diag driver** re-ejecute su registro QDI de canal — típicamente **cerrando y reabriendo `/dev/diag`** (o descargando/recargando el módulo diag-char), lo que hace que el kernel re-invoque el `open`/`register` QDI hacia el modem → `0xc0d80ba4→0xc0d81af8`. Combinado con **tu NEW_SERVER (global 0xc8c2d870/74)** ganado *justo antes* de que QRTR re-resuelva, el endpoint COMMAND queda apuntando a tu socket. **INFERENCE (consistente con el "una vez funcionó").**
- **Verificación en vivo:** trazar el `sendto` del modem tras reabrir `/dev/diag` + tu NEW_SERVER; el port destino debe ser el tuyo. Si tenés acceso a un user-PD con handle QDI de diag, `qurt_qdi_handle_invoke(h, 0x104, /*a1=conn*/, /*a2=reply*/, 17, ...)` reproduce el re-cableo directamente (sólo desde dentro del Hexagon). **UNKNOWN (requiere el handle QDI vivo).**

---

## 7. FACT / INFERENCE / UNKNOWN — cierre con VAs

### FACT
- Entry real del dispatcher = **0xc0d7fcd4** (fall-through al allocframe 0xc0d7fcdc). `r7:6=bitsplit(r2,#8)` ⇒ r6=r2&0xFF, r7=(r2>>8)&0xFF; r16=r0. (`_dis_b13.txt:75491-75493`).
- Gate por r7: r7==1 → dispatch r6; r7==0 → sólo r6==2. (`75507-75513`).
- r6 tabla `0xc35cdfec` (idx r6-1): **r6=4 → 0xc0d8005c**. r20 tabla `0xc35ce044` (idx r20-1): **r20=17 → 0xc0d80234**. (verificado con vmap sobre modem_full.elf).
- r6=4 exige `r24(arg1)!=0` (conn). (`75717-75719`). r20=17 → 0xc0d80290(call 0xc104f4b8)/0xc0d80298(call 0xc0d80ba4). (`75857-75860`).
- 0xc0d80ba4 → 0xc0d80bb8: call 0xc0d81af8. (`76439-76444`).
- 0xc0d81af8 camina lista `0xc9508978`, re-registra servicios (`0xc0d5da84`), setea `0xc950897c/7d`; **NO lee 0xc8c2d870/74**. (`77420-77535`).
- 0xc0a0420c = emisor F3 QShrink (`add(pc,##0xfffe6354)`→0xc09ea564; `bitsplit(r1,#0xc)`; 284 call-sites). **NO** es scanf/sprintf de texto. (`_dis_b08.txt:9319-9349`).
- Cluster = `diag_qdi` / `qurt_qdi` (strings 0xc35ce080/0c0/0de/1c0/200/240/40b/500; `_seg27_strings.txt:458-496`).
- `diagpkt_process_ctrl_msg` @ 0xc0d66264 usa tabla `0xc35c9b78` (idx=cmd_type-3, cmd_type 3..0x65); **0xc0d7fcdc NO está en esa tabla**; ctrl-msg cmd_type=4 → default 0xc0d67794. (dump completo de la tabla).
- Global peer COMMAND `0xc8c2d870/74`: sólo escrito en NEW_SERVER 0xc0d83018/20; único origen del node/port del sendto. (confirmado en force_rewire.md §4.1).
- 0xc0d81af8 con 2 callers: 0xc0d375ec (Camino A, gateado por connected) y 0xc0d80bb8 (Camino B, QDI). (decodificado de _seg13_raw.bin).

### INFERENCE
- `0xc0d7fcd4` es el método `invoke` del driver-object QDI de diag; r6=comando QDI, r7=modo, r20=sub-op; r6=4/r20=17 = (re)apertura/asociación de canal (equivale al re-cableo de boot pero invocable por QDI, sin el gate `connected`).
- El disparo real lo hace el kernel diag driver / user-PD al (re)registrar su canal; por eso "una vez funcionó" tras parar MM (el kernel re-invocó QDI). Reabrir `/dev/diag` + ganar el NEW_SERVER es el análogo accionable desde el AP.
- El re-cableo no aporta node/port propios: usa el global vivo (tu NEW_SERVER). Basta NEW_SERVER + provocar el re-registro QDI.

### UNKNOWN (sólo en vivo / requiere ejecución dentro del Hexagon)
- El **handle QDI** y el **objeto `conn`** concretos (r1/r24) — existen sólo en runtime; sin ellos no se puede invocar el método desde fuera.
- Si reabrir `/dev/diag` (o recargar diag-char) del kernel HLOS re-dispara efectivamente 0xc0d80ba4 en este build (probar y trazar el sendto).
- El valor numérico del port QRTR de tu socket (lo asigna el bind) que quedará en 0xc8c2d870/74.

---

## 8. Resumen para el cliente diag userspace

1. **No mandes un "ctrl-msg r6=4/r20=17" por CNTL**: no existe tal mensaje; ese dispatcher es QDI intra-modem, no QRTR. El intento de bruteforce de "register diag channel/PD textual" propuesto en force_rewire.md §7.3 **no puede funcionar** (0xc0a0420c no parsea texto). **FACT.**
2. **Para re-cablear COMMAND sin reboot desde el AP:** (a) publicá tu servicio DATA/COMMAND por QRTR (NEW_SERVER → global 0xc8c2d870/74), y (b) **forzá que el kernel diag re-registre su canal QDI** (cerrar/reabrir `/dev/diag`, o reload del diag-char driver), que es lo que ejecuta 0xc0d80ba4→0xc0d81af8. **INFERENCE accionable.**
3. Si controlás un **user-PD** con handle QDI de diag, `qurt_qdi_handle_invoke(h, method=0x0104, conn, reply, subop=17, …)` dispara el re-cableo directo (sólo intra-Hexagon). **INFERENCE.**
