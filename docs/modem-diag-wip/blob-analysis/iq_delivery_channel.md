# IQ DELIVERY CHANNEL — Cómo llegan las muestras IQ del modem al AP (SM6375, MPSS.HI.4.3.4)

**Target:** Qualcomm SM6375 / Moto G82 5G · MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133
**Binarios:** `/tmp/modemre/clade_dec_36m.bin` (VA base 0xd8000000) · `seg27_dec.bin` (RFLTE, VA base
0xce480000) · `modem.b21` rodata (0xc3553000) · `modem.b23` (0xc8b6a000).
**Herramientas:** `dis36.sh <va> <len>`, `rd.py <va> <n>`, scans de strings/punteros propios.

**Leyenda:** **FACT** = byte/instrucción/string verificado con VA en esta imagen · **INFERENCE** =
deducción con base dura · **UNKNOWN** = cuerpo en pool dlpager (no desensamblable) o valor runtime.

---

## 0. VEREDICTO EJECUTIVO — EL CANAL REAL

**Opción (a): las muestras IQ se devuelven al AP INLINE, dentro de la RESPUESTA DIAG del comando
FETCH (get_samples), en chunks paginados. NO hay región física compartida; NO es memshare.**

Evidencia dura, convergente:

| Evidencia | VA / string | Qué prueba |
|---|---|---|
| memshare refutado | reachability desde 0xd81893a4/0xd8189964 | IQ_CAPTURE **no alcanza** el cliente QMI 52 (memshare_request_flow.md). El buffer de captura sale de heap interno/DSM. **FACT** |
| **Paquete de respuesta que porta las muestras** | `ftm_lte_iq_capture_get_samples_rsp_pkt_ptr allocation failure` @**0xce6deca0** | Existe un **response packet** dedicado a get_samples: se **aloca** para meter las muestras y devolverlas por DIAG. **FACT** |
| **Response-length variable, controlado por el AP** | 0xd8150f90–0xd8150fc0 | Cuando DIAG-subcmd@pkt[0x02]==**0x24**, el modem lee `u16 @ pkt[0x08..0x09]` como **longitud de la respuesta** y aloca el diagpkt de ese tamaño (min 0xfd0=4048, max 0xffff=65535). **FACT literal** |
| **Bound de payload inline** | `Assertion (payload_size<MAX_DIAG_CMD_PAYLOAD_BYTE) failed` @**0xce593d5b** | Las muestras se copian a un payload acotado por el máximo de un comando DIAG ⇒ hay que **paginar** capturas grandes. **FACT** |
| **REPACK = serializador de propiedades inline** | `num props %d, total size %d` @0xce6d3bce; `Could not allocate %d bytes for payload_size array` @0xce6d3ebd | El REPACK arma un **array de propiedades empaquetadas** ({name,size,data}) y las **copia contiguas** al payload de respuesta. **FACT** |
| **La copia real es memcpy a un buffer de staging** | prop-pack calls 0xd818aaf4→**0xd8051b58**→**0xc0989068** (memscpy), offsets 0x9000/0x12000/0x1b000/0x36000/0x3f000 en el buffer `r18` | El framework FTM copia cada propiedad **inline** en un buffer contiguo que se devuelve por DIAG. **FACT** (path RX_MEASURE, framework compartido con IQ_CAPTURE) |

**El `0x%8x` del REPACK NO es una dirección física para el AP** (§3): es el **puntero FUENTE interno**
(VA del heap del modem, `p_sample_capture_buffer`) desde donde el serializador lee `size` bytes para
copiarlos **dentro** del payload DIAG. El AP nunca lo mapea; recibe los bytes ya copiados.

---

## 1. EL PATH FETCH — desensamblado y sub-acciones

### 1.1 Los handlers de campo sólo guardan valores en el contexto [FACT]
El handler FETCH_IQ (field 13) **0xd8189964** (y todos los 51 handlers de la jump-table @0xc37c0828)
NO devuelven muestras: leen el TLV del wire y lo escriben en el descriptor/contexto. FETCH_IQ:
```
d8189964: r5:r2 = TLV value (u32 LE de pkt)
d8189970: r3 = memw(r25+#0x0)        ; config-struct base
d818997c: r3 = memw(r3+#0x64)        ; cfg_default de FETCH_IQ (offset 0x64)
d8189984: r2 = memw(r5 + field<<2)   ; slot del descriptor
d818998c: r2 += mpyi(r16,#0xc)       ; stride 0xc por campo
d8189990: jump 0xd81898a0            ; store común del valor
```
⇒ FETCH_IQ=1 **marca la acción ACTION_FETCH** en el contexto; la ejecución real es posterior. **FACT.**

### 1.2 Las tres acciones internas [FACT — tabla @0xc906c8f8]
Vista interna normalizada (map_iq_capture.md §4), tres flags de acción:
```
logidx 7  ACTION_GETCFG   ; consulta rx_path_data por carrier — no captura
logidx 8  ACTION_ACQUIRE  ; captura: llena p_sample_capture_buffer (heap DSM interno)
logidx 9  ACTION_FETCH    ; lee el buffer y lo empaqueta en la respuesta DIAG (=FETCH_IQ)
```
Campos de la ventana de fetch (logidx 17/18/19):
```
MAX_DIAG_SIZE   (17) ; tope de bytes que caben en ESTA respuesta DIAG
SAMP_OFFSET     (18) ; offset (en muestras) dentro del buffer para este fetch
NUM_SAMP_BYTES  (19) ; cuántos bytes devolver en este fetch
```

### 1.3 ACTION_ACQUIRE llena un buffer del heap INTERNO (no compartido) [FACT]
El prop_action de captura aloca el buffer IQ del heap interno del modem:
```
rflte_ftm_iq_capture_prop_action_8bit_iq_buff   @0xce6ded20  (I8/Q8)
rflte_ftm_iq_capture_prop_action_16bit_iq_buff  @0xce6ded68  (I16/Q16)
```
Estructura de captura (asserts, seg27):
```
rx_capture_req_ptr->p_sample_capture_buffer != NULL          @0xce694300
rx_capture_req_ptr->tti_wp_capture_buffer_size_words != 0    @0xce694347
num_samples_captured <= rx_capture_db_ptr->num_samples_to_capture  @0xce694459
```
`p_sample_capture_buffer` es un puntero del heap/DSM del modem, **no** una región memshare (FACT
de memshare_request_flow.md: IQ_CAPTURE no toca el cliente QMI 52). **FACT.**

### 1.4 ACTION_FETCH copia una ventana del buffer a la respuesta DIAG [FACT contrato]
```
ftm_lte_iq_capture_get_samples_rsp_pkt_ptr  @0xce6deca0
```
Se **aloca un response packet** (get_samples_rsp_pkt) y el REPACK **copia** `NUM_SAMP_BYTES` bytes
desde `p_sample_capture_buffer + SAMP_OFFSET*bytes_per_sample` **dentro** de ese paquete, hasta el
tope `MAX_DIAG_SIZE`/`MAX_DIAG_CMD_PAYLOAD_BYTE`. El paquete vuelve al AP por el transporte DIAG.
El cuerpo exacto del prop_action vive en el pool dlpager (seg27, sólo strings/datos en el ELF; código
paginado) ⇒ **UNKNOWN-body**, pero el **contrato** (rsp_pkt + copia inline acotada) es **FACT** por
los strings y por el mecanismo genérico del framework (§2).

---

## 2. FORMATO FETCH_IQ Y PAGINACIÓN (canal = a, DIAG chunked)

### 2.1 El transporte: DIAG-subcmd 0x24 = respuesta de longitud variable [FACT literal]
En el registrar/dispatch DIAG **0xd8150ed8** (iq_final_values.md §1.2, re-verificado):
```
d8150f90: r18 = #0xfd0                              ; DEFAULT response len = 4048 (0xfd0)
d8150f98: memb(pkt+1) == 0x0b                       ; subsys FTM
d8150fa8: (memub(pkt+2)|memub(pkt+3)<<8) == 0x24    ; DIAG subsys-cmd 0x24  <-- FETCH/get_samples
d8150fb0: r3 = memb(pkt+0x9)
d8150fb8: r2 = memub(pkt+0x8)
d8150fbc: r2 |= asl(r3,#0x8)                         ; r2 = u16 @ pkt[0x08..0x09]
d8150fc0: r18 = maxu(r2, 0xfd0)                      ; response_len = max(pkt[0x08], 0xfd0)
d8150fc4: call 0xd80f40fc  (r2 = zxth(r18))          ; diagpkt_alloc(response_len)
```
**⇒ Contrato de paginación byte-a-byte:**
- **pkt[0x02..0x03] = 0x0024** (DIAG subsys-cmd) selecciona el path de **respuesta variable**.
- **pkt[0x08..0x09] = MAX_DIAG_SIZE** (u16 LE) = **cuántos bytes puede portar esta respuesta DIAG**.
  Rango **[0x0fd0 .. 0xffff]** = **[4048 .. 65535]** bytes. El modem aloca el diagpkt de ese tamaño.
- El AP **elige el tamaño del chunk** poniendo pkt[0x08]. Con 0x14 (subcmd normal) se ignora y usa
  4048 por default.

### 2.2 El cuerpo FTM: TLVs de la ventana de fetch [FACT nombres / INFERENCE encoding]
El cuerpo FTM (a partir de pkt[0x0a], layout TLV estándar QC `{u16 field_id, u16 len, value[len]}` LE,
FACT 0xd81897f4) para un FETCH lleva:
```
FETCH_IQ        (field 13)  = 1              ; arma ACTION_FETCH
SAMP_OFFSET     (logidx 18) = k              ; muestra inicial de esta ventana
NUM_SAMP_BYTES  (logidx 19) = M             ; bytes a devolver en esta ventana (M <= MAX_DIAG_SIZE)
MAX_DIAG_SIZE   (logidx 17) = pkt[0x08]      ; espejo del response-len del transporte
RX_CARRIER      (field 1)   = carrier        ; identifica la captura
```
> Los `field_id` del wire para SAMP_OFFSET/NUM_SAMP_BYTES/MAX_DIAG_SIZE son de la **vista interna
> normalizada** (28 campos @0xc906c8f8); su mapeo exacto a `field_id` del wire de IQ_CAPTURE está en
> el rango 0..0x32 de la jump-table @0xc37c0828 pero **no todos** tienen slot dedicado nombrado en
> la tabla del wire (los de fetch se derivan del contexto). El transporte (pkt[0x08]) es lo que fija
> el tope real. **INFERENCE (mapeo TLV exacto de la ventana) / FACT (transporte pkt[0x08]).**

### 2.3 Formato de las muestras en la respuesta [FACT mecanismo / INFERENCE layout]
- **8-bit vs 16-bit:** lo fija IQ_DATA_FORMAT (field 15) / SAMP_SIZE ⇒ `prop_action_8bit`
  (@0xce6ded20) o `prop_action_16bit` (@0xce6ded68). **FACT.**
- **Layout:** **IQIQIQ interleaved, signed two's-complement, little-endian** (convención QC FTM;
  8-bit = `int8 I, int8 Q, …`; 16-bit = `int16 I, int16 Q, …`). **INFERENCE fuerte.**
- `bytes_per_sample` = 2 (8-bit I/Q) o 4 (16-bit I/Q).

### 2.4 Cómo el AP junta las muestras (algoritmo de paginación) [INFERENCE con base FACT]
```
ACQUIRE una vez:
   IQ_CAPTURE { RX_CARRIER, IQ_CAPTURE_TYPE, NUM_OF_SAMPLES=N, SAMP_FREQ, IQ_DATA_FORMAT, FETCH_IQ=0 }
   -> llena p_sample_capture_buffer con N muestras (N*bps bytes) en el heap interno del modem.

FETCH en chunks (loop):
   total = N * bps
   chunk = MAX_DIAG_SIZE - header_overhead          ; p.ej. hasta ~65535 - unos pocos bytes
   for off in range(0, total, chunk):
       enviar IQ_CAPTURE con subcmd DIAG 0x24, pkt[0x08]=MAX_DIAG_SIZE,
              FETCH_IQ=1, SAMP_OFFSET=off/bps, NUM_SAMP_BYTES=min(chunk, total-off)
       leer la RESPUESTA DIAG -> copiar su payload (las muestras) al fichero, en orden.
   -> concatenar todos los payloads = buffer IQ completo.
   nº de requests = ceil(total / chunk)
```
Cada respuesta trae un chunk contiguo `[off, off+NUM_SAMP_BYTES)` del buffer, ya copiado inline. El
AP sólo concatena. **INFERENCE fuerte (base: get_samples_rsp_pkt + SAMP_OFFSET + response-len var).**

---

## 3. EL "ADDRESS" DEL REPACK — puntero interno, NO físico DDR

El REPACK loguea `[FTM.RFTEST][IQ_CAPTURE][REPACK]: [%2d][%3d][ %12s ][ %4d ][ 0x%8x ]`
(fmt @0xc37c0949): `{ idx, ?, name, size_bytes, 0xADDRESS }`.

**El `0x%8x` es el puntero FUENTE interno del modem, NO una dirección física exportada.** [FACT del
mecanismo + INFERENCE]:

1. El REPACK es el **serializador genérico de propiedades FTM** (`repacked_measurement_list`,
   `number_of_properties`, `num props %d, total size %d` @0xce6d3bce). Cada propiedad tiene
   `{name, size, data_ptr}`; el serializador **copia `size` bytes desde `data_ptr` al payload** de
   la respuesta (memscpy 0xc0989068 vía 0xd8051b58, offsets 0x9000/0x12000/0x1b000/… en el buffer de
   staging). **FACT** (verificado en el path RX_MEASURE, framework compartido).
2. Ese `data_ptr` es una **VA del address-space del modem** (`p_sample_capture_buffer` en el heap
   DSM interno). El `%12s`/`%4d`/`0x%8x` es una **traza de debug** de qué propiedad se empaquetó y
   desde qué VA, no un contrato de "aquí tienes memoria para mapear". **INFERENCE fuerte.**
3. El AP recibe los **bytes ya copiados** en el payload DIAG; **no** usa el `0x%8x` para nada. Si el
   AP intentara mapear ese `0x%8x` obtendría basura/XPU-fault: es una VA del modem, no física del AP,
   y la región es heap privado del modem (no compartida, no memshare). **INFERENCE (dura, dado que
   memshare está refutado y el buffer es DSM interno).**

**⇒ NO hay camino rápido por "mapear la address del REPACK".** Esa address es inútil para el AP. El
único canal es el payload inline de la respuesta DIAG (§2).

---

## 4. CANTIDAD DE MUESTRAS Y RELACIÓN CON MAX_DIAG_SIZE

### 4.1 Límite de captura (ACQUIRE) [FACT contrato]
```
num_samples_captured <= num_samples_to_capture (= NUM_OF_SAMPLES, field 14)   @0xce694459
p_sample_capture_buffer / sample_capture_buffer_size_words                     (heap interno)
NB_IQ: Insufficient buffer size for capture! buf_size_words %d, required %d     @0xce6943db
```
El tope de N lo fija `NUM_OF_SAMPLES` y el tamaño del buffer interno alocado (heap/DSM), **no** una
región de 5 MiB fija (eso era del modelo memshare, ahora descartado). El tamaño real del buffer
interno = **UNKNOWN estático** (lo fija `modem_mem_alloc` en runtime según N y bytes_per_sample).

### 4.2 Requests por N muestras (paginación DIAG) [FACT fórmula / INFERENCE overhead]
```
total_bytes = N * bytes_per_sample          ; bps = 2 (8-bit) o 4 (16-bit)
chunk_util  = MAX_DIAG_SIZE - overhead      ; MAX_DIAG_SIZE in [4048 .. 65535]
requests    = ceil(total_bytes / chunk_util)
```
Ejemplos (MAX_DIAG_SIZE=65535, overhead ~ header despreciable, bps=4 para 16-bit):
- N = 1,024 muestras (4 KiB) → 1 request.
- N = 16,384 (64 KiB) → 2 requests.
- N = 262,144 (1 MiB) → ~17 requests.
- N = 1,310,720 (5 MiB) → ~80 requests.
Con MAX_DIAG_SIZE=4048 (default): ~4× más requests. **FACT (fórmula) / INFERENCE (overhead exacto del
header de get_samples_rsp_pkt).**

### 4.3 Sample rate [FACT enum / UNKNOWN mapa]
SAMP_FREQ (field 16) → enum WB log2 (`lte_LL1_ue_wb_samp_rate_e`), base 1.92 MHz, rates 1.92/3.84/
7.68/15.36/23.04/30.72 MHz por BW (map_iq_capture.md §5). El rate afecta cuántas muestras son N
segundos, pero **no** cambia el canal de entrega. **FACT (enum) / UNKNOWN (TLV→Hz exacto).**

---

## 5. EL CAMINO OS RESULTANTE — qué necesita el AP (revisado)

**El path memshare de `os_iq_path.md` queda OBSOLETO para IQ_CAPTURE.** No se necesita: reservar DT,
hyp_assign, char device, dual-VMID, ni el daemon QMI 52 para las muestras. El canal es **DIAG puro**.

### 5.1 Lo que SÍ se necesita [FACT + INFERENCE]
1. **Cliente DIAG userspace sobre QRTR** (ya lo tenemos): enviar comandos y **leer respuestas**.
2. **Peer DIAG bien establecido para que las RESPUESTAS de comando vuelvan.** Las muestras viajan por
   la **respuesta de comando** (get_samples_rsp_pkt), que pasa por el gate `diagpkt_rsp_send`
   (0xc0d36c44) — exige el handshake **DIAGID (type 0x21) + FEATURE mask + TX-mode**
   (diag_transport_full.md, map_diag_core.md). Los F3/log/event drenan sin gate, pero **la respuesta
   con las muestras NO**. ⇒ **completar el handshake DIAG es el requisito crítico** (ya identificado
   como "nuestro problema de re-cableo diag"). **FACT (gate) / trabajo de userspace DIAG.**
3. **Iterar FETCH** con SAMP_OFFSET/NUM_SAMP_BYTES y pkt[0x08]=MAX_DIAG_SIZE (usar 0xffff para
   minimizar requests), concatenar los payloads. **INFERENCE (algoritmo §2.4).**

### 5.2 Secuencia completa AP → muestras
```
(1) Handshake DIAG: mandar CNTL DIAGID (0x21) + FEATURE mask + poner TX-mode  -> respuestas habilitadas
(2) Poner el modem en FTM/cal y activar la portadora (fuera de este doc: cal_mode_trigger.md, TECH_ENTER+RADIO_CONFIG)
(3) ACQUIRE: IQ_CAPTURE { RX_CARRIER, NUM_OF_SAMPLES=N, SAMP_FREQ, IQ_DATA_FORMAT, IQ_CAPTURE_TYPE, FETCH_IQ=0 }
(4) FETCH loop: por cada chunk, IQ_CAPTURE subcmd-DIAG=0x24, pkt[0x08]=0xffff,
       { FETCH_IQ=1, SAMP_OFFSET=k, NUM_SAMP_BYTES=M } -> leer payload de la respuesta DIAG
(5) Concatenar payloads -> fichero IQ (IQIQ interleaved, signed LE, 8/16-bit por IQ_DATA_FORMAT)
```

### 5.3 Trabajo restante (concreto)
| # | Pieza | Estado |
|---|---|---|
| 1 | Cliente DIAG/QRTR que envía cmd y **lee la respuesta de comando** | Existe (userspace). Verificar que capta la respuesta en el socket DATA. |
| 2 | **Handshake DIAG completo** (DIAGID+FEATURE+TX-mode) para que la respuesta con muestras vuelva | **BLOQUEADOR real.** Contrato en MASTER_MODEM_MAP.md §3 / diag_transport_full.md. Trabajo de userspace. |
| 3 | Poner subcmd-DIAG 0x24 y pkt[0x08]=MAX_DIAG_SIZE en el frame de FETCH | Ajuste de encoding del frame (trivial una vez (2) esté). |
| 4 | Loop FETCH con SAMP_OFFSET/NUM_SAMP_BYTES + concatenar | Escribir (lógica §2.4). |
| 5 | Entrar en modo cal + activar portadora (pre-ACQUIRE) | Fuera de este canal (cal_mode_trigger.md / enter_mode_path.md). |
| — | ~~memshare/DT/hyp_assign/char-device~~ | **NO NECESARIO** para IQ_CAPTURE. Descartar. |

---

## 6. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT**
- IQ_CAPTURE **no alcanza** el cliente memshare QMI 52 (reachability 0xd81893a4/0xd8189964).
  El buffer de captura sale de heap interno/DSM (`modem_mem_alloc`). Canal ≠ (b).
- Existe un **response packet de muestras**: `ftm_lte_iq_capture_get_samples_rsp_pkt_ptr` @0xce6deca0.
- DIAG-subcmd **0x24** habilita respuesta de **longitud variable**: `response_len = max(u16@pkt[0x08],
  0xfd0)`, rango 4048..65535 (0xd8150f90–0xd8150fc0 → diagpkt_alloc 0xd80f40fc). **Byte-exacto.**
- Payload inline acotado: `Assertion (payload_size<MAX_DIAG_CMD_PAYLOAD_BYTE)` @0xce593d5b.
- REPACK = serializador de propiedades que **copia** los datos al payload
  (`num props %d, total size %d` @0xce6d3bce; memscpy 0xc0989068 vía 0xd8051b58, offsets
  0x9000/0x12000/0x1b000/0x36000/0x3f000 en buffer de staging).
- prop_action 8/16-bit @0xce6ded20/0xce6ded68; buffer `p_sample_capture_buffer` (asserts @0xce694300…).
- FETCH_IQ handler 0xd8189964 sólo guarda el flag en el contexto (arma ACTION_FETCH).
- La respuesta con muestras pasa por el gate `diagpkt_rsp_send` (0xc0d36c44): exige handshake DIAG.

**INFERENCE (base dura)**
- **Canal = (a): muestras INLINE en la respuesta DIAG del FETCH, en chunks** paginados por
  SAMP_OFFSET/NUM_SAMP_BYTES, con tope MAX_DIAG_SIZE = pkt[0x08] (4048..65535).
- El `0x%8x` del REPACK es la **VA fuente interna** (heap del modem), **no** física para el AP; el AP
  recibe los bytes ya copiados y no mapea nada.
- Layout muestras: **IQIQ interleaved, signed two's-complement, little-endian**; bps=2 (8b) / 4 (16b).
- requests = ceil(N*bps / (MAX_DIAG_SIZE - overhead)).
- El path OS es DIAG-puro: sólo cliente DIAG + handshake completo; memshare/DT/hyp_assign NO hacen falta.

**UNKNOWN (pool dlpager / runtime)**
- Cuerpo exacto de `rflte_ftm_iq_capture_prop_action_*` y del get_samples repack (código paginado en
  seg27; sólo strings/datos en el ELF). El **contrato** está fijado por strings + framework, no el body.
- `field_id` del wire EXACTOS para SAMP_OFFSET/NUM_SAMP_BYTES/MAX_DIAG_SIZE dentro del cuerpo FTM del
  FETCH (la vista interna @0xc906c8f8 los nombra; el mapeo wire↔interno exacto = verificar en vivo).
- Overhead exacto del header de get_samples_rsp_pkt (para el chunk útil real).
- Tamaño del buffer interno de captura y N máximo (runtime `modem_mem_alloc`).
- Enums IQ_DATA_FORMAT / IQ_CAPTURE_TYPE / SAMP_FREQ→Hz (RFC).

---

## 7. REPRODUCIR
```bash
# Transporte: response-len variable (subcmd 0x24), byte-exacto
/tmp/modemre/dis36.sh 0xd8150ed8 0x120        # d8150f90..fc0: r18=max(pkt[0x08],0xfd0); diagpkt_alloc
# FETCH_IQ handler (sólo guarda el flag)
/tmp/modemre/dis36.sh 0xd8189964 0x40
# Serializador de propiedades (copia inline) — path RX_MEASURE (framework compartido)
/tmp/modemre/dis36.sh 0xd818a29c 0x840         # calls a 0xd818aaf4/0xd8051b58 (memscpy) con offsets de staging
/tmp/modemre/dis36.sh 0xd8051b58 0x10          # -> 0xc0989068 (memscpy)
# Strings de contrato (seg27_dec.bin, base 0xce480000):
python3 - <<'PY'
d=open('/tmp/modemre/seg27_dec.bin','rb').read(); b=0xce480000
for s in [b"ftm_lte_iq_capture_get_samples_rsp_pkt_ptr",
          b"rflte_ftm_iq_capture_prop_action_8bit_iq_buff",
          b"rflte_ftm_iq_capture_prop_action_16bit_iq_buff",
          b"p_sample_capture_buffer",b"num_samples_captured",
          b"payload_size<MAX_DIAG_CMD_PAYLOAD_BYTE",b"num props %d, total size %d"]:
    i=d.find(s); print(hex(b+i) if i>=0 else "NA", s.decode())
PY
```
