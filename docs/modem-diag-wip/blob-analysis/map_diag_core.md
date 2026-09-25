# DIAG de punta a punta — mapa COMPLETO del subsistema DIAG del modem SM6375 (Moto G82 5G)

**Build**: MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 · Hexagon/QDSP6 (v66) · SM6375
**Imágenes**: `modem_full.elf` (reensamblado, VA-based) · `_dis_b13.txt` (seg13, DIAG core no-comprimido, VA 0xc0d36000+) · `_dis_b10.txt` (0xc0axxxxx) · rodata seg21 (VA 0xc3553000+, tablas de usuario) · RW-data seg23 (VA 0xc8b6a000+, nodos de registro del master table) · `clade_dec_full.bin` (páginas paginadas 0xd8xxxxxx).
**Método**: 100% estático. Todas las VA de nodos/tablas leídas con `mem_helpers.py` (v2o/read); handlers en 0xc0d3xxxx desensamblados de `_dis_b13.txt`; handlers en 0xd8xxxxxx del `clade_dec_full.bin` vía `dis.sh`.

**Leyenda**: **FACT** = leído directo del blob (VA citada) · **INFERENCE** = deducción por convención Qualcomm consistente con la evidencia · **UNKNOWN** = sólo determinable en vivo.

---

## 0. TL;DR

1. El diag core despacha por **cmd_code = pkt[0]** en la función maestra **`diagpkt_master_dispatch @ 0xc0d55df8`**. Casos especiales inline (0x80, 0x4b, 0x73, 0x7d, 0x82, 0x29); todo lo demás se busca en el **master table** (lista de nodos en seg23 → user_tables en seg21). **FACT**.
2. **SUBSYS_CMD (0x4b)**: `subsys = pkt[1]`, `subsys_cmd = memuh(pkt+2)`. Busca el nodo cuyo `subsys_id` coincide y luego el rango `[cmd_lo,cmd_hi]` en su user_table. **FACT** (0xc0d55f28/0xc0d55f48).
3. **67 nodos de registro** en seg23 → **32 subsys distintos** + LEGACY (0xFF). FTM = subsys **0x0B**, 75 entradas, TODAS → wrapper único **`0xd8150ed8`** (ftm_common_dispatch). **FACT**.
4. **PEEK/POKE (0x02–0x07) y NV/EFS peek NO están registrados** → compilados fuera. No hay lectura de memoria arbitraria por DIAG en este PD. Para tocar `0xca79c494`/`0xd8150ed8` en vivo hay que **invocar** handlers, no leer RAM. **FACT**.
5. **Transporte**: RX por canal **CMD** (QRTR), respuestas SIEMPRE por canal **DATA** (io_type==2), con gate **feature-mask + DIAGID** en `diagpkt_rsp_send @ 0xc0d36c44`. Los **F3/log/event NO cruzan ese gate** (drenan aunque las respuestas de comando estén bloqueadas). **FACT** (detalle en `diag_transport_full.md`).
6. **SSID FTM/RF = 23 (0x17)**; vecinos útiles: 14 (NR5G/IRAT), 6004 (cal xPT). **FACT** (detalle en `ssid_ftm.md`).

---

## 1. Mecánica de dispatch maestro (FACT)

### 1.1 Estructura de los nodos del master table (seg23, RW-data)
Los nodos son **arrays estáticos de 20 bytes (0x14)** en seg23 (0xc8b6a000+). Layout confirmado leyendo el nodo FTM @0xc8dc3b50:

```
struct diagpkt_master_table_node {   // 20 bytes
    uint16 cmd_code_lo;   // +0x00  (0x0000)
    uint16 cmd_code_hi;   // +0x02  (0x00FF)  <- marcador de nodo del master table
    uint16 subsys_id;     // +0x04
    uint16 count;         // +0x06  nº de entradas de la user_table
    uint32 flags;         // +0x08  (0x000000ff / a veces 0x00000000/2/3)
    uint32 next;          // +0x0c  (0xffffffff en imagen estática)
    const user_entry* user_table;  // +0x10 -> tabla en seg21 rodata
};
```
> Ejemplo (FACT): `0xc8dc3b50: 00 00 ff 00 | 0b 00 4b 00 | ff 00 00 00 | ff ff ff ff | e8 d1 7b c3`
> → cmd 0x0000..0x00FF, subsys=0x0B, count=0x4B(75), tbl=0xc37bd1e8.

Cada `user_table` (seg21 rodata) es array de:
```
struct diagpkt_user_table_entry {   // 8 bytes
    uint16 cmd_code_lo;   // +0x00
    uint16 cmd_code_hi;   // +0x02  (rango; lo==hi = comando único)
    void*  handler;       // +0x04
};
```
**FACT** (leído con `read(va+i*8,8)` → `struct.unpack('<HHI')`).

### 1.2 `diagpkt_master_dispatch @ 0xc0d55df8` — el switch principal (FACT)
`r17 = pkt`, `r22 = memub(r17+0)` = **cmd_code**:

| cmd_code (pkt[0]) | qué hace | VA | destino |
|---|---|---|---|
| **0x80** | delayed-response / subsys-80 | 0xc0d55e20 | jump 0xc0d36424 |
| **0x4b** | **SUBSYS_CMD**: r21=`memub(pkt+1)`=subsys_id, r22=`memuh(pkt+2)`=subsys_cmd | 0xc0d55e30 / 0xc0d55f28 / 0xc0d55f48 | busca nodo por subsys+rango |
| **0x29** | (DIAG_LOG_ON_DEMAND-ish) inline | 0xc0d55e5c | jump 0xc0d3637c |
| **0x73** | **LOG_CONFIG** inline (`DIAG_LOG_CONFIG_F`) | 0xc0d55e68 | jump 0xc0d3626c |
| **0x7d** | **EXT_MSG_CONFIG** inline (`DIAG_EXT_MSG_CONFIG_F`) | 0xc0d55e78 | jump 0xc0d363c0 |
| **0x82** | (DIAG_QSR4/ext) inline | 0xc0d55e84 | jump 0xc0d3632c |
| resto (<0x7c) | LEGACY: r20=0xFF, r21=0xFF, r22=cmd | 0xc0d55eb8 | búsqueda master table |

El loop de búsqueda del master table está en **0xc0d55f58** (`loop1 #0x80`, itera 2 arrays de punteros de nodos en **0xc92e4400** y **0xc92e4600**, BSS/runtime). Match: `memh(node+0x4)==subsys(r21)` **AND** `memh(node+0x2)==cmd_hi(r20)`, luego recorre la user_table comparando `cmd_lo..cmd_hi` contra r22 (0xc0d55fbc–0xc0d55fc8) y salta al `handler`. Si no hay handler → **DIAG_BAD_CMD (0x13)** (0xc0d364d8: `combine(r17,#0x13)` → 0xc0d560c8). **FACT**.

> Nota: `0xc37bd1e8` = user_table del **FTM (subsys 0x0B)**. La frase previa "indexada por `memuh(pkt+0x2)`" es correcta para el path 0x4b: `memuh(pkt+2)` = subsys_cmd que se busca en el rango de la user_table FTM. **FACT**.

---

## 2. Tabla COMPLETA cmd_code DIAG → handler → nombre

### 2.1 LEGACY (cmd_code directos 0x00–0x7F, subsys 0xFF) — FACT
Nodos 0xFF (seg23) → user_tables (seg21). Counts REALES (verificados; corrige lecturas previas que sobre-contaban por solapamiento de nodos):

| cmd | nombre (diagcmd.h) | handler | ubicación |
|-----|--------------------|---------|-----------|
| **0x00** | DIAG_VERNO_F (version) | 0xc0d7b818 | en-ELF seg13 |
| **0x01** | DIAG_ESN_F | 0xc0d7b9a8 | en-ELF |
| **0x1C** | DIAG_TS_F (timestamp) | 0xc0d64fc4 | en-ELF |
| **0x1D** | (0x1D) | 0xc0d650c8 | en-ELF |
| **0x46** | (0x46) | 0xc0d65064 | en-ELF |
| **0x51** | (0x51) | 0xc0d64fe0 | en-ELF |
| **0x67** | (0x67) | 0xc0d650fc | en-ELF |
| **0x97** | (0x97, ext-cmd/DCI) | 0xc0d75508 | en-ELF |
| **0x7B** | DIAG_QSR_EXT_MSG_TERSE_F | 0xc0d6f678 | en-ELF |
| **0x7C** | DIAG_EXT_BUILD_ID_F (build) | 0xc0d7b9fc | en-ELF |
| **0x0C** | DIAG_STATUS_F | 0xdc00fcb8 | PAGED |
| **0x19** | (0x19) | 0xda0243cc | PAGED |
| **0x1A** | (0x1A) | 0xdc6863b4 | PAGED |
| **0x1B** | (0x1B) | 0xdc686640 | PAGED |
| **0x24** | (0x24) | 0xdc68ecc8 | PAGED |
| **0x2C** | DIAG_FEATURE_QUERY_F | 0xda024094 | PAGED |
| **0x2D** | (0x2D) | 0xda024060 | PAGED |
| **0x3F** | (0x3F) | 0xda01cd60 | PAGED |
| **0x40** | DIAG_SER_RESET_F(?) | 0xda01cc60 | PAGED |
| **0x43** | (0x43) | 0xdc68eac8 | PAGED |
| **0x5A** | (0x5A) | 0xda01cb30 | PAGED |
| **0x62** | (0x62) | 0xdc68e7f0 | PAGED |
| **0x63** | DIAG_STATUS_SNAPSHOT(?) | 0xda014654 | PAGED |
| **0x70** | (0x70) | 0xdc686670 | PAGED |
| **0x71** | (0x71) | 0xd9d64d30 | PAGED |
| **0x72** | (0x72) | 0xdc69f2f4 | PAGED |
| **0x77** | (0x77) | 0xda013ba4 | PAGED |

*Fuente*: nodos 0xFF @ 0xc8c25708(tbl 0xc35ca640,cnt1→0x51), 0xc8c2571c(0xc35ca648,cnt1→0x1d), 0xc8c25730(0xc35ca650,cnt3→0x1c/0x46/0x67), 0xc8c25918(0xc35ccc88,cnt1→0x97), 0xc8c25954(0xc35ccca8,cnt1→0x7b), 0xc8c2d81c(0xc35cd498,cnt3→0x00/0x7c/0x01), 0xc8d0a42c(0xc3794988,cnt17→0x0c,0x70,0x1a,0x1b,0x62,0x43,0x24,0x72,0x71,0x19,0x2d,0x2c,0x3f,0x40,0x5a,0x63,0x77). **FACT**.

### 2.2 Comandos manejados INLINE (no en master table) — FACT

| cmd | nombre | punto de entrada inline | detalle |
|-----|--------|--------------------------|---------|
| **0x73** | DIAG_LOG_CONFIG_F | 0xc0d3626c | subcmd = `memub(pkt+4..7)`; valida subcmd==3 (SET). Log mask por equip_id. |
| **0x7D** | DIAG_EXT_MSG_CONFIG_F | 0xc0d363c0 | subcmd = `memb(pkt+1)`; **subcmd 0x04 = SET_ALL** (loop 0xc0d363e4 sobre ss_id_first..last, `call 0xc0d78a14` por rango); subcmd 0x01/0x02/0x03 = GET_RANGES/GET_MASK/SET_MASK. |
| **0x82** | DIAG_QSR4_EXT_MSG(?) | 0xc0d3632c | terse/QSR path. |
| **0x29** | DIAG_LOG_ON_DEMAND(?) | 0xc0d3637c | subcmd = `memb(pkt+1)`. |
| **0x80** | subsys delayed-rsp | 0xc0d36424 | wrap de respuesta diferida (delayed_rsp_id). |

> **msg-mask / event-mask / log-mask** también tienen handlers **registrados como subsys 0x12 (DIAG_SERV)** — ver §3. Los códigos inline 0x73/0x7d son el path "legacy" clásico; el subsys 0x12 es la variante moderna por stream. Ambos parsean rangos `{ssid_first, ssid_last, stream_id}` (strings `ssid_range_ctrl_pkt ... num_ssid_ranges=%d` @0xc35cd172, `msg_mask: update ... ssid_first=%d ssid_last=%d stream_id=%d` @0xc35ccfcf). **FACT (strings) / INFERENCE (mapeo exacto de subcodes)**.

### 2.3 PEEK/POKE/NV — estado (FACT)
```
0x02 DIAG_PEEKB_F     NO REGISTRADO
0x03 DIAG_PEEKW_F     NO REGISTRADO
0x04 DIAG_PEEKD_F     NO REGISTRADO
0x05 DIAG_POKEB_F     NO REGISTRADO
0x06 DIAG_POKEW_F     NO REGISTRADO
0x07 DIAG_POKED_F     NO REGISTRADO
0x0F/0x10 NV_PEEK/POKE NO REGISTRADO
0x26 DIAG_NV_READ_F   NO REGISTRADO (no aparece en ningún nodo/user_table)
0x27 DIAG_NV_WRITE_F  NO REGISTRADO  (0x27 aquí es el ftm_cmd LTE, NO el cmd legacy)
```
⚠️ El rango `0x02..0x05` que aparece con handler 0xc0d65620 pertenece al **subsys 0x12** (user_table 0xc35ca668), NO al legacy, y NO es peek — manipula bitmasks de msg/log/event mask (§3). **FACT**.

---

## 3. Subsys IDs registrados y sus handlers (FACT)

67 nodos → 32 subsys. Tabla por subsys (VA nodo / user_table / count / handlers destacados):

| subsys | nombre (INFERENCE por convención Qualcomm salvo indicado) | user_table(s) | count | handlers (base) |
|--------|-----------------------------------------------------------|---------------|-------|-----------------|
| **0x00 (0)** | OEM / **Motorola** (`mot_diag.c`) **FACT** | 0xc41118d8 | 26 | 0xc0f9f4a0.. (cmd 0x00–0x1c, 0xf9–0xfd) |
| **0x04 (4)** | WCDMA | 0xc4149ff8 | 1 | 0xd8... |
| **0x05 (5)** | HDR (1xEV-DO) | 0xc3fd2598 | 10 | — |
| **0x08 (8)** | GSM | 0xc424b4e0 | 6 | — |
| **0x09 (9)** | UMTS | 0xc424b4c0 | 4 | — |
| **0x0A (10)** | HWTC | 0xc37bd440 | 1 | — |
| **0x0B (11)** | **FTM (RF-test)** **FACT** | **0xc37bd1e8** | **75** | **TODAS → 0xd8150ed8** |
| **0x0C (12)** | REX/OS | 0xc35e0908 | 2 | — |
| **0x12 (18)** | **DIAG_SERV**: msg/log/event mask + self-test **FACT** | 0xc35ca668(5), 0xc35cccb0(23), 0xc35ccc68/78/80/90/98, 0xc35cdb90, 0xc35cf700 | ~40 | 0xc0d65620 (mask 0x02-05), 0xc0d656f4 (0x0b-0e,0x11-12), 0xc0d65858 (0x09-0a), 0xc0d6592c (0x0f-10), 0xc0d738c8 (0x00), 0xc0d708e4 (0x01), 0xc0d6f6ac (0x29), 0xc0d6f7f0 (0x34-3c,0x50-52,0x80f,0x821-823) |
| **0x1B (27)** | SP / MODEM-PROC dispatch | 0xc3796660 | 6 | 0xd9b07e1c.. (cmd 0x06-0x0b) PAGED |
| **0x1D (29)** | SENSORS (SNS) | 0xc37988b0 | 9 | 0xd974xxxx (cmd 0x01-07,0xa1,0xe2,0xe4) |
| **0x2A (42)** | COREBSP/PM(?) | 0xc4255280 | 1 | — |
| **0x2F (47)** | **CORE** (DIAG_SUBSYS_CORE) **FACT** | 0xc3fb6060 | 15 | 0xda0742xx (cmd 0x01,0xe1,0xe2,0xf1-fa,fc-fe) — **stubs de contadores, NO peek** |
| **0x32 (50)** | APPS(?) | 0xc35ca690 | 3 | — |
| **0x36 (54)** | ? | 0xc4149878 | 1 | — |
| **0x44 (68)** | **LTE** (DIAG_SUBSYS_LTE) | 0xc371da40, 0xc37249b8, 0xc4068ee0, 0xc4081610 | 1 c/u | rangos 0x7000-7fff→0xda08c85c, 0x9000-9fff→0xda0b59ac, 0x8001-8fff→0xd94084e0, 0x4000-5fff→0xd9503194 PAGED |
| **0x46 (70)** | TDSCDMA(?) / RF-assoc | 0xc35b9ab0 | 9 | 0xd846xxxx (cmd 0x00-0a) PAGED |
| **0x49 (73)** | ? | 0xc35e1568 | 1 | 0xc0d9e8ec (cmd 0x00) |
| **0x4B (75)** | (subsys anidado; A=cal, B=?) | 0xc35a2b80(3), 0xc35abac8(5) | 3+5 | A: 0x100-102→0xd80fc3xx; B: 0x1e,1f,23,24,25→0xc1048xxx |
| **0x4D (77)** | ? | 0xc35d0270 | 1 | 0x200-20f→0xc104fedc |
| **0x4E (78)** | ? | 0xc41092f0 | 2 | 0x00→0xc0f60514, 0x01→0xc0f606a0 |
| **0x55 (85)** | **CMAPI** (Common Modem API) | 0xc3fa4038 | 27 | 0xc0de93xx-0xc0dea2xx (cmd 0x01-0x23) en-ELF |
| **0x56 (86)** | ? | 0xc35e09c8, 0xc35e0a10 | 1+1 | — |
| **0x59 (89)** | ? | 0xc35adf38(3), 0xc3ff3370(1), 0xc408b420(2) | 6 | — |
| **0x5A (90)** | GNSS(?) | 0xc35a0538/0548/0648/0678/0720/0d50 | 17 | 0xd8082xxx-0xd8084xxx (cmd 0x0200-0x02ff) PAGED |
| **0x5B (91)** | Modem-mgr/QMI(?) | 0xc3fabb38 | 73 | 0xda019xxx-0xda01axxx (cmd 0x00-0x4b) PAGED |
| **0x60 (96)** | ? | 0xc35b6c68(3), 0xc3faca70(3), 0xc3fb0100(1) | 7 | 0xd8524xxx (cmd 0x101-103) |
| **0x64 (100)** | ? (catch-all 0x0000-0xffff) | 0xc3753e18 | 1 | 0xd8a9e4b0 |
| **0x6A (106)** | ? | 0xc3fb6618 | 21 | 0xda080xxx-0xda081xxx (cmd 0x00-0x15) PAGED |
| **0x6D (109)** | DAL/heap-diag(?) | 0xc3584068 | 6 | 0xc103axxx-0xc103bxxx (cmd 0x00-0x05) |
| **0x72 (114)** | ? | 0xc3fb2b68 | 10 | 0xd8af1xxx (cmd 0xa1-0xab) PAGED |
| **0x77 (119)** | ? | 0xc3fa45a0(1), 0xc3fabf10(7) | 8 | 0x03→0xc0fc00d8 + 0xda... |
| **0x78 (120)** | ? (catch-all 0x0000-0xfffe) | 0xc3fabfd8 | 1 | 0xda01dd28 |
| **0x7A (122)** | ? (rango 0x4001-0x5fff) | 0xc400c638 | 1 | 0xd96f6d84 PAGED |
| **0xFF (255)** | **LEGACY diag core** (§2.1) | 7 tbls | 27 | 0xc0d6/0xc0d7/PAGED |

**Ruteo del subsys 0x0B (FTM)** — cómo el core llega al FTM y otros subsys (FACT):
1. cmd_code 0x4b → `subsys=pkt[1]=0x0B`, `subsys_cmd=memuh(pkt+2)`.
2. Master-table match subsys 0x0B → user_table 0xc37bd1e8, busca el rango que contiene subsys_cmd.
3. TODAS las 75 entradas apuntan a **0xd8150ed8** (wrapper de registro DIAG FTM). Este valida: `pkt[1]==0x0B`, `memuh(pkt+2)==0x14`, `id&0xfffe==0x35a` (0xd8150f00–0xd8150f28), luego llama a `ftm_common_dispatch` (0xd8150e14/0xd814d760) que re-despacha por el **ftm_cmd_id** (0x27=LTE, 0x14=WLAN, 0x8000/0x8001=NR5G, etc.). **FACT** (`dis.sh 0xd8150ed8`).
   > Para otros subsys "paginados" (LTE 0x44, GNSS 0x5A, 0x5B, 0x6A…) el handler está en la ventana q6zip 0xd9xx/0xda xx y sigue el mismo patrón de re-dispatch interno. **INFERENCE**.

---

## 4. Transporte completo + routing de respuestas (resumen con VAs — FACT)

*(desarrollo completo en `diag_transport_full.md`; aquí el resumen verificado)*

### 4.1 Canales (QRTR sockets), `diagcomm_io_socket_init @ 0xc0d813dc`
| canal | gp off | type@+0x18 | rol |
|-------|--------|-----------|-----|
| **CNTL** | gp+0x6998 | 9 | control (handshake) |
| **DATA** | gp+0x69a0 | 9 (io_type==2) | **RX-nada / TX de TODAS las respuestas + logs + eventos** |
| **CMD**  | gp+0x699c | 3 | **RX de comandos** |
| **DCI**  | gp+0x69a4 | 6 | Diag Command Interface |

Descriptor de canal (0x84B), alloc/registro `0xc0d8b004`; tabla de descriptores `0xcb93fec0`; `port=0x1001` (@+0x14), `allow_flow=1` (@+0x84, sólo io_type!=0). **FACT**.

### 4.2 Handshake (ctrl-msgs por CNTL, parser `diagpkt_process_ctrl_msg @ 0xc0d66264`, jumptable @0xc35c9b78 index=type-3)
| ctrl type | handler | efecto | flag global |
|-----------|---------|--------|-------------|
| **8 = FEATURE mask** | 0xc0d66f34 | set feature-recibido | `memb(0xc92e43e0)=1` (0xc0d66fa8) |
| **0x21 = DIAGID** | 0xc0d67484 | set diagID-recibido | `memw(0xc92e4754)` bit0 |
| **0x11 = TX MODE** (por stream) | 0xc0d6711c → setter 0xc0d7dc78 | stream real-time + flush | `stream_obj+0xaf` |
| **3 = DIAGMODE** | 0xc0d66348 | buffering global (data_len=0x24) | — (no gate) |
| APPS_BUFFERING_MODE | 0xc0d67538 | buffering por stream | `stream_obj+0xb3` |

### 4.3 El GATE de respuestas — `diagpkt_rsp_send @ 0xc0d36c44` (FACT)
```
c0d36c48: r2 = memw(0xc92e4754)          ; DIAGID flag
c0d36c4c: p0 = tstbit(r2,#0); if(!p0) skip
c0d36c64: r2 = memb(0xc92e43e0)          ; FEATURE-mask flag
c0d36c6c: if (r2==0) jump 0xc0d58b9c     ; -> "Attempt to send response before feature mask OR diagID"
```
**Los F3/log/event NO pasan por este gate** (usan diagbuf/log-commit directo) → drenan aunque las respuestas de comando estén bloqueadas. Ésta es la diferencia observable "F3 llegan pero respuestas no". **FACT**.

### 4.4 Drain hacia el peer (FACT)
`diagbuf_send_pkt @ 0xc0d562dc` itera canales (array 0xc9508b80), gate `descriptor+0x94==0` (peer listo) → `diagcomm_io_transmit @ 0xc0d56520` (exige io_type==2, allow_flow(+0x84)!=0) → `0xc0d93ed4` → sendto real `0xc0d95f64`.
**Destino sendto** = `node=memw(desc+0x8c)`, `port=memw(desc+0x90)`, poblados desde el **QRTR NEW_SERVER (type 8)** del AP: handler `0xc0d830b4` guarda `node→0xc8c2d870`, `port→0xc8c2d874` (0xc0d83018/0xc0d83020). **FACT**.
Flow-control STOP 0xF4/0xF7 → allow_flow=0 (0xc0d373ac); re-arma tras TX ok (0xc0d565b0). Flag "connected/drain" 0xc93022fc.

### 4.5 Secuencia mínima para drenar respuestas de comando al TU socket DATA
1. Publicar servicios QRTR (CNTL inst0, **DATA inst2**, DCI inst4, service 0x1001) **ANTES** de comandar → genera NEW_SERVER → puebla node/port y `+0x94=0`.
2. FEATURE (type 8) → `0xc92e43e0=1`.
3. DIAGID (type 0x21) → `0xc92e4754` bit0.
4. TX MODE (type 0x11) stream_id=1, real-time → flush.
5. Comandar por **CMD (inst 1)**; **leer por DATA (inst 2)**.

---

## 5. F3 / log / event — emisión y suscripción

- **SSID FTM/RF = 23 (0x17)** — TODA la familia `[FTM.RFTEST]/[FTM.RFDEBUG]/[FTM.CMN]`, IQ_CAPTURE, RX_MEASURE, RADIO_CONFIG, TX_MEASURE, cal-V3. **FACT** (`msg_const_type.packed>>16 == 0x17`, ~128 mensajes `ftm_*`; ver `ssid_ftm.md`).
- **SSIDs RF vecinos**: **14 (0x0E)** (NR5G/IRAT: `ftm_nr5g_rf_debug_tx_override.c`, `ftm_rf_test_irat_config.c`), **6004 (0x1774)** (`ftm_calv3_xpt_seq_class.cpp`), **6056** (rf_nr5g_pwr_mgr), **47** (CORE/memheap incidental). **FACT**.
- **Rango de SSIDs soportados**: min 0, max 7389. Para "capturar todos los F3 de debug RF" basta habilitar 23 (y 14). **FACT**.
- **Suscripción (msg mask)** — dos caminos:
  - Legacy inline `0x7D` (DIAG_EXT_MSG_CONFIG_F), subcmds: 0x01 GET_RANGES, 0x02 GET_MASK, 0x03 SET_MASK, **0x04 SET_ALL** (handler inline 0xc0d363c0; el loop 0x04 en 0xc0d363e4). **FACT (path 0x7d) / INFERENCE (numeración subcmd)**.
  - Subsys 0x12 (DIAG_SERV) variante por stream (handlers 0xc0d6f7f0/0xc0d738c8…). Strings `msg_mask: update ... ssid_first/ssid_last/stream_id` @0xc35ccfcf. **FACT**.
- **Habilitar SÓLO RF-test (SSID 23)**: `7D 03 17 00 17 00 00 00 FF FF FF FF`.
- **Habilitar TODO**: `7D 04 00 00 FF FF FF FF` (SET_ALL). Ver `ssid_ftm.md` §4 para variantes.
- **Log packets / events**: cmd legacy 0x73 (LOG_CONFIG, inline 0xc0d3626c, por equip_id) y el path de eventos (event mask). No cruzan el gate de rsp_send → drenan con sólo tener el canal DATA suscrito. **FACT**.

---

## 6. Comandos de read/peek/NV/EFS disponibles (respuesta directa)

**No existe ningún comando DIAG de lectura de memoria/NV/EFS/peek registrado en este PD del modem.** Verificado exhaustivamente:

| capacidad | estado | evidencia |
|-----------|--------|-----------|
| PEEK/POKE (0x02-0x07) | **AUSENTE** (compilado fuera) | no en ningún nodo/user_table §2.3 **FACT** |
| NV_READ/WRITE (0x26/0x27 legacy) | **AUSENTE** | 0x27 aquí es ftm_cmd LTE, no legacy **FACT** |
| NV_PEEK/POKE (0x0F/0x10) | **AUSENTE** | idem **FACT** |
| subsys "Memory Ops / MEMORY_DEVICES / RAMDUMP peek" | **NO EXISTE** | búsqueda de strings negativa **FACT** |
| CORE (subsys 47) 0xf1-0xfe | son **stubs de contadores/flags** (0xda0742a4+4c/u), NO read-by-address | **INFERENCE** (tamaño 4B) + **FACT** (no formato peek) |
| EFS2 (subsys 20) | **subsys 20 NO registrado** en este PD; y aunque estuviera, lee ficheros FS, no RAM | **FACT** (subsys 20 ausente de los 67 nodos) |
| DIAG password / secure-diag gate | **NO existe** (no está "gated", está ausente) | búsqueda negativa **FACT** |

→ **Para leer `0xca79c494` (o cualquier RAM) en vivo por DIAG: NO es posible con este firmware.** El código de esa región (0xc0axxxxx = seg10, no-comprimido) se lee **off-line** del `modem_full.elf` / `_dis_b10.txt`. Para el estado en vivo del modem, la única vía es **invocar** un handler que devuelva ese estado en su respuesta (p.ej. STATUS 0x0C, STATUS_SNAPSHOT 0x63, FEATURE_QUERY 0x2C, o un subcomando FTM/CMAPI específico), no leer memoria arbitraria. **FACT**.
  - Único mecanismo de volcado de RAM cruda = **crash → ELF en /ramdumps/** (SSR, intrusivo). **FACT**.
  - Nota: existe `LFW_MAX_EFS_DIAG_CMD_PAYLOAD_BYTE` (string) → hay un path EFS-over-DIAG en el firmware, pero **no está registrado como subsys DIAG en este PD** (es del Root-PD/APPS). **FACT (string) / INFERENCE (no accesible desde este canal)**.

---

## 7. FACT / INFERENCE / UNKNOWN (cierre con VAs)

### FACT
- master-dispatch `diagpkt_master_dispatch @ 0xc0d55df8`; switch cmd_code: 0x80(0xc0d36424), 0x4b(subsys, 0xc0d55e30/f28/f48), 0x73(0xc0d3626c), 0x7d(0xc0d363c0), 0x82(0xc0d3632c), 0x29(0xc0d3637c); loop master table @0xc0d55f58 sobre 0xc92e4400/0xc92e4600; BAD_CMD 0x13 @0xc0d364d8.
- 67 nodos de registro en seg23 (marcador `00 00 ff 00`, node=20B, tbl@+0x10); layout {cmd_lo,cmd_hi,subsys,count,flags,next,tbl}.
- user_table entry = 8B {cmd_lo,cmd_hi,handler}.
- FTM subsys 0x0B: user_table 0xc37bd1e8, count 75, TODAS → 0xd8150ed8; wrapper valida subsys 0xb / cmd 0x14 / id 0x35a (0xd8150f00-f28).
- Legacy (0xFF): 27 comandos §2.1 con VAs de handler.
- Todos los subsys §3 con VA de nodo, user_table, count y handler base.
- PEEK/POKE/NV/EFS ausentes §6.
- Gate rsp_send @0xc0d36c44 (flags 0xc92e4754 bit0 + 0xc92e43e0).
- Transporte: canales gp+0x6998/69a0/699c/69a4; NEW_SERVER 0xc0d830b4→node/port 0xc8c2d870/74; drain 0xc0d562dc/0xc0d56520/0xc0d95f64.
- SSID FTM=23; vecinos 14/6004.

### INFERENCE
- Nombres de subsys marcados "(?)" (49,50,54,56,59,60,77,78,89,96,100,106,114,119,120,122) — asignados por convención Qualcomm/heurística; el blob no deja strings de nombre de subsys directos.
- Numeración exacta de subcmds de 0x7D (0x01-0x04) por convención (path inline confirmado).
- Handlers PAGED (0xd8/0xd9/0xda xx) re-despachan internamente como el FTM (patrón observado en FTM).
- CORE 0xf1-0xfe = getters de contadores (por tamaño 4B), no peek.

### UNKNOWN (sólo en vivo)
- Layout exacto del payload DIAGID (type 0x21) que este build valida.
- Semántica tx_mode 1 vs 0 en 0xc0d7dc78.
- Port QRTR numérico de tu DATA (lo asigna el bind del AP).
- Feature-bits mínimos que exige el build para abrir el flujo diagID.
- Nombres canónicos de los subsys "(?)" (requeriría cruzar con un diagcmd.h de este árbol).

---

## 8. Apéndice — reproducibilidad
- Nodos de registro: escaneo de seg23 (0xc8b6a000, filesz 0x5cdd97) por marcador `00 00 ff 00` + validación de tbl en rodata + count<0x200 → 67 nodos (script en la sesión).
- user_tables: `mem_helpers.read(va+i*8,8)` → `struct.unpack('<HHI')`.
- Handlers en-ELF: `_dis_b13.txt` (grep `^<VA>:`).
- Handlers paginados: `dis.sh <va> <len>` (base 0xd8000000, clade_dec_full.bin).
- Cross-refs: `diag_transport_full.md`, `diag_response_routing.md`, `ssid_ftm.md`, `mem_read_cmds.md`, `rftest_command_format.md`.
