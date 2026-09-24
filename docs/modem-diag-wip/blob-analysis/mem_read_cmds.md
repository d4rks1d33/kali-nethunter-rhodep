# DIAG — Comandos de LECTURA DE MEMORIA soportados por el modem SM6375 (Moto G82 5G)

Firmware: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (Hexagon / QDSP6, SM6375)
ELF reensamblado: `/tmp/modemre/modem_full.elf` — análisis 100% estático.
Objetivo: leer el rango runtime `0xd8000000-0xd9000000` (ventana q6zip descomprimida,
`ftm_common_dispatch @ 0xd8150ed8`).

Marcado de confianza:
- **FACT** = evidencia estática directa en el blob.
- **INFERENCE** = deducción por convención Qualcomm / estructura.
- **UNKNOWN** = no determinable estáticamente (handler en 0xd8xxxxxx no mapeado).

====================================================================
## TL;DR (respuesta corta)
====================================================================
1. **DIAG_PEEKB/PEEKW/PEEKD (0x02/0x03/0x04) y POKE (0x05/0x06/0x07) NO están
   registrados en este firmware.** La tabla de dispatch legacy del diag core
   (subsys 0xFF) no contiene ningún handler para esos command codes. → PEEK está
   **compilado fuera (feature-fused off)**, no simplemente "gated por password". **FACT.**
2. **No existe ningún subsys de "Memory Operations / MEMORY_DEVICES / peek arbitrario"
   registrado.** El único candidato (subsys 47 = CORE) NO expone read-by-address. **FACT.**
3. **No hay un check de "diag password / secure diag / SP unlock"** protegiendo un peek,
   sencillamente porque no hay peek. La única autenticación DIAG presente es
   `diag_dci_auth.c` (auth de clientes DCI), irrelevante para memoria. **FACT.**
4. El RAM/mini-dump (`/ramdumps/…`) es un **artefacto de crash/SSR** que se escribe al FS,
   NO un comando DIAG de lectura de memoria en vivo. **FACT.**
5. **Para llegar a `0xd8150ed8` en runtime NO se lee memoria: se INVOCA el dispatcher**
   vía el transporte FTM (`0x4B 0x0B <ftm_cmd_id:u16> …`). Esa es la vía soportada. **FACT.**

====================================================================
## 1. Tabla maestra de comandos DIAG legacy (0x00-0x7F)
====================================================================
### Mecánica de dispatch (FACT)
El diag core usa el patrón clásico Qualcomm `diagpkt_master_table` como **lista enlazada
de nodos de registro** (no una tabla contigua). Cada nodo mide 16 bytes efectivos:

    struct diagpkt_master_table_node {   // en seg23 (RW data, 0xc8b6a000+)
        uint16 cmd_code_lo;   // +0x00  (0x0000)
        uint16 cmd_code_hi;   // +0x02  (0x00FF)  <- marcador de nodo
        uint16 subsys_id;     // +0x04  (0xFF = LEGACY / diag core)
        uint16 count;         // +0x06  nº de entradas en la user_table
        ...                   // +0x08..0x0F  (next ptr / flags, runtime)
        const user_entry* user_table;  // +0x10  -> tabla en seg21 rodata
    };

Cada `user_table` (en seg21 rodata, 0xc3553000+) es un array de:

    struct diagpkt_user_table_entry {    // 8 bytes
        uint16 cmd_code_lo;
        uint16 cmd_code_hi;
        void*  handler;       // -> código (seg13 0xc0d3.. o ventana paged 0xd8..)
    };

Los nodos con `subsys_id == 0xFF` son los **comandos LEGACY** (los que el diag core
dispatchea directamente por command code 0x00-0x7F). **FACT.**

### Command codes legacy REGISTRADOS en este build (subsys 0xFF)  — FACT
Extraídos de los 7 nodos legacy (user_tables 0xc35ca640, …648, …650, 0xc35ccc88,
0xc35ccca8, 0xc35cd498, 0xc3794988):

| cmd  | nombre clásico (diagcmd.h)         | handler        | ubicación        |
|------|------------------------------------|----------------|------------------|
| 0x00 | DIAG_VERNO_F (version)             | 0xc0d7b818     | en-ELF (seg13)   |
| 0x01 | DIAG_ESN_F                         | 0xc0d7b9a8     | en-ELF (seg13)   |
| 0x0C | DIAG_STATUS_F                      | 0xdc00fcb8     | PAGED (q6zip)    |
| 0x19 | (0x19)                             | 0xda0243cc     | PAGED            |
| 0x1A | (0x1A)                             | 0xdc6863b4     | PAGED            |
| 0x1B | (0x1B)                             | 0xdc686640     | PAGED            |
| 0x1C | DIAG_TS_F (timestamp)              | 0xc0d64fc4     | en-ELF           |
| 0x1D | (0x1D)                             | 0xc0d650c8     | en-ELF           |
| 0x24 | (0x24)                             | 0xdc68ecc8     | PAGED            |
| 0x2C | DIAG_FEATURE_QUERY_F (feature mask)| 0xda024094     | PAGED            |
| 0x2D | (0x2D)                             | 0xda024060     | PAGED            |
| 0x3F | (0x3F)                             | 0xda01cd60     | PAGED            |
| 0x40 | DIAG_SER_RESET_F ?                 | 0xda01cc60     | PAGED            |
| 0x43 | (0x43)                             | 0xdc68eac8     | PAGED            |
| 0x46 | (0x46)                             | 0xc0d65064     | en-ELF           |
| 0x51 | (0x51)                             | 0xc0d64fe0     | en-ELF           |
| 0x5A | (0x5A)                             | 0xda01cb30     | PAGED            |
| 0x62 | (0x62)                             | 0xdc68e7f0     | PAGED            |
| 0x63 | DIAG_STATUS_SNAPSHOT ?             | 0xda014654     | PAGED            |
| 0x67 | (0x67)                             | 0xc0d650fc     | en-ELF           |
| 0x70 | (0x70)                             | 0xdc686670     | PAGED            |
| 0x71 | (0x71)                             | 0xd9d64d30     | PAGED            |
| 0x72 | (0x72)                             | 0xdc69f2f4     | PAGED            |
| 0x77 | (0x77)                             | 0xda013ba4     | PAGED            |
| 0x7B | DIAG_QSR_EXT_MSG_TERSE_F           | 0xc0d6f678     | en-ELF           |
| 0x7C | DIAG_EXT_BUILD_ID / GET_BUILD_ID   | 0xc0d7b9fc     | en-ELF           |
| 0x97 | (0x97)                             | 0xc0d75508     | en-ELF           |

Command codes NO listados arriba **NO tienen handler** → el diag core responde
`DIAG_BAD_CMD_F (0x13)` (eco del cmd con status de comando desconocido). INFERENCE
(protocolo estándar; el fallthrough del master-table lookup devuelve BAD_CMD).

### Estado de 0x02/0x03/0x04/0x05/0x06/0x07 (PEEK/POKE)  — FACT
    0x02 DIAG_PEEKB_F  -> NO REGISTRADO
    0x03 DIAG_PEEKW_F  -> NO REGISTRADO
    0x04 DIAG_PEEKD_F  -> NO REGISTRADO
    0x05 DIAG_POKEB_F  -> NO REGISTRADO
    0x06 DIAG_POKEW_F  -> NO REGISTRADO
    0x07 DIAG_POKED_F  -> NO REGISTRADO
    0x0F/0x10 DIAG_NV_PEEK/POKE_F -> NO REGISTRADO

⚠️ Nota anti-falso-positivo: existe OTRA tabla (subsys 18 = AUDIO_SETTINGS, user_table
`0xc35ca668`) cuya PRIMERA entrada es un rango `0x02-0x05` → handler `0xc0d65620`.
**NO es peek.** Se verificó por desensamblado: `0xc0d65620` y sus subhandlers
(0xc0d84bc4/0xc0d84c94/0xc0d83ed4/0xc0d83dd4) manipulan arrays de bitmask en
`0xcb933xxx` / locks `0xc9508xxx` (msg/log/event mask), con bound-check de longitud
`cmp.gtu(r,##0xCFE)` = tamaño de tabla de mask, NO lectura de dirección arbitraria.
Esos sub-command 0x02-0x05 son del subsys 18, alcanzables sólo vía `0x4B 0x12 …`,
y no leen memoria. (FACT — desensamblado en `_dis_b13.txt` líneas ~48465-48517 y
~80534-80707.)

Igualmente, la tabla `0xc3584068` (codes 0x00-0x05, handlers 0xc103bxxx) que aparece en
un primer escaneo pertenece al heap DAL (`DYN_MEM_PHYS_POOL` / `amss_mem_heap`), NO a DIAG.

====================================================================
## 2. Subsys DIAG registrados — foco MEMORIA / DEBUG / DUMP
====================================================================
Se enumeraron **todos** los nodos de registro subsys en seg23 (patrón
`cmd_hi=0x00FF` + user_table→rodata). subsys_id → count → user_table:

    subsys   0 (0x00) OEM/legacy-mot   cnt=26  tbl=0xc41118d8  ("mot_diag.c")
    subsys   4 (0x04) WCDMA            cnt= 1   tbl=0xc4149ff8
    subsys   5 (0x05) HDR              cnt=10  tbl=0xc3fd2598
    subsys   8 (0x08) GSM              cnt= 6   tbl=0xc424b4e0
    subsys   9 (0x09) UMTS             cnt= 4   tbl=0xc424b4c0
    subsys  10 (0x0A) HWTC             cnt= 1   tbl=0xc37bd440
    subsys  11 (0x0B) FTM              cnt=75  tbl=0xc37bd1e8  <-- RF-test / FTM
    subsys  12 (0x0C) REX              cnt= 2   tbl=0xc35e0908
    subsys  18 (0x12) AUDIO_SETTINGS   varios  (msg/log/event mask, NO memoria)
    subsys  27 (0x1B) APPS/MODEM_PROC  cnt= 6   tbl=0xc3796660
    subsys  29 (0x1D) SENSORS?         cnt= 9   tbl=0xc37988b0
    subsys  42 (0x2A)                  cnt= 1   tbl=0xc4255280
    subsys  47 (0x2F) CORE             cnt=15  tbl=0xc3fb6060  <-- ver abajo
    subsys  50 (0x32) APPS?            cnt= 3   tbl=0xc35ca690
    subsys  54 (0x36)                  cnt= 1   tbl=0xc4149878
    subsys  68 (0x44)                  varios
    subsys  70 (0x46)                  cnt= 9   tbl=0xc35b9ab0
    subsys  73 (0x49)                  cnt= 1   tbl=0xc35e1568
    subsys  75 (0x4B)                  cnt= 3/5
    subsys  77 (0x4D)                  cnt= 1
    subsys  78 (0x4E)                  cnt= 2
    subsys  85 (0x55) CMAPI           cnt=27  tbl=0xc3fa4038
    subsys  86 (0x56)                  cnt= 1/1
    subsys  89 (0x59)                  cnt= 3/1/2
    subsys  90 (0x5A)                  varios
    subsys  91 (0x5B)                  cnt=73  tbl=0xc3fabb38
    subsys  96 (0x60)                  cnt= 3/3/1
    subsys 100 (0x64)                  cnt= 1
    subsys 106 (0x6A)                  cnt=21  tbl=0xc3fb6618
    subsys 109 (0x6D)                  cnt= 6   tbl=0xc3584068
    subsys 114 (0x72)                  cnt=10  tbl=0xc3fb2b68
    subsys 119 (0x77)                  cnt= 1/7
    subsys 120 (0x78)                  cnt= 1
    subsys 122 (0x7A)                  cnt= 1
    subsys 255 (0xFF) LEGACY diag core (comandos 0x00-0x7F, §1)

### Candidatos MEMORIA/DEBUG/DUMP
- **subsys 47 (0x2F) = DIAG_SUBSYS_CORE** — el único "core/debug". user_table
  `0xc3fb6060`, 15 subcomandos: `0x01, 0xE1, 0xE2, 0xF1..0xFA, 0xFC, 0xFD, 0xFE`
  (handlers en 0xda0742xx, ventana paged). **NO hay ningún subcomando de "read memory
  by address"** con formato peek. Los 0xF1-0xFE son *stubs cortos consecutivos*
  (0xda0742a4,a8,ac,b0,b4,b8… con +4 bytes cada uno → probables getters de contadores/
  flags, no un peek de N bytes). **INFERENCE.** No expone lectura arbitraria. **FACT**
  (ausencia de comando peek en la tabla).
- **subsys 20 (0x14) = EFS2 / FS**: hay CÓDIGO EFS (`fs_diag.c`, `fs_efs2.c`,
  `fs_efs2_names.c` @0x1a21d57; string `EFS2` @0x1aa1698) **pero el subsys 20 NO está
  registrado en el diag core del PD modem.** El acceso EFS2-via-DIAG lo sirve el
  Root-PD/APPS, no este PD. → No usable desde el canal DIAG del modem para leer 0xd8xxxxxx
  (además EFS2 lee ficheros del FS, no RAM). **FACT** (subsys 20 ausente de la lista).
- **No existe** subsys "Memory Operations", "MEMORY_DEVICES", ni un subsys de
  RAM-dump/peek registrado. **FACT** (búsqueda de strings y de nodos negativa:
  `Memory Read/MEMORY_READ/read_memory/MemoryDevice/RAMDUMP/MemoryOp` → NOT FOUND).

====================================================================
## 3. Formato EXACTO del comando de PEEK (si estuviera disponible)
====================================================================
En este build PEEK NO existe, pero para referencia el layout clásico Qualcomm
(diagdiag_common.c) es:

    // Request (host -> target)
    struct diag_peek_req {
        uint8  cmd_code;   // 0x02 PEEKB / 0x03 PEEKW / 0x04 PEEKD
        uint32 addr;       // dirección base (LE)
        uint16 length;     // nº de unidades (bytes / words / dwords)
    } __packed;            // = 7 bytes

    // Response (target -> host)
    struct diag_peek_rsp {
        uint8  cmd_code;   // eco (0x02/0x03/0x04)
        uint32 addr;       // eco
        uint16 length;     // eco
        uint8  data[length * unit_size];  // unit_size = 1/2/4
    } __packed;

Bytes de ejemplo para leer 0x100 bytes desde 0xd8150ed8 (SI existiera 0x02):
    02  d8 0e 15 d8  00 01
(cmd=02, addr=0xd8150ed8 LE = d8 0e 15 d8, length=0x0100 LE = 00 01)

⚠️ **Este comando NO será respondido por el modem SM6375** — devolverá BAD_CMD.
**FACT** (0x02 no registrado). No malgastes tiempo intentándolo.

====================================================================
## 4. ¿PEEK gated por seguridad?
====================================================================
- **No.** No está "gated", está **ausente de la tabla de dispatch**. **FACT.**
- No hay strings `diag password`, `Secure Diag`, `secure_diag`, `SP unlock`,
  `security level`, `access denied`, `command deny`, `privileged (diag)`. **FACT**
  (búsqueda exhaustiva; los hits de "not allowed" son de HOB/PLMN/HS-SCCH, no DIAG;
  "Privileged TRAP attempted by a usermode thread" @0x103fd4 es del MMU/tlb.c, no DIAG).
- Única autenticación DIAG presente: `diag_dci_auth.c` (@0x1a2192d) — autentica
  CLIENTES DCI (Diag Command Interface embebido), NO protege un peek. **FACT.**
- Conclusión: aunque quitaras cualquier "gate", el handler no existe. Para tener
  peek habría que parchear la imagen y re-registrar un handler (fuera de alcance del
  canal DIAG en vivo).

====================================================================
## 5. Alternativas para leer 0xd8150ed8 en runtime
====================================================================
Ordenadas por viabilidad sobre el canal DIAG/QRTR que ya tienes funcionando.

### 5.A — NO NECESITAS LEER: INVOCAR el dispatcher  (recomendado)  [FACT]
`0xd8150ed8` es `ftm_common_dispatch`, y TODAS las 75 entradas del subsys FTM
(user_table 0xc37bd1e8) apuntan ahí. No hace falta leer el código: se EJECUTA
mandando un paquete DIAG-subsys-FTM:

    0x4B  0x0B  <ftm_cmd_id:u16 LE>  <payload…>
    (DIAG_SUBSYS_CMD_F=0x4B, subsys FTM=0x0B, luego el ftm_cmd_id de 16 bits)

ftm_cmd_id válidos (indexan 0xc37bd1e8): 0x00,0x01,0x02,0x03,0x07,0x08,0x09,0x0A,0x0B,
0x0D,0x10,0x11,0x12,0x14(WLAN),0x27(LTE),0x28(TDSCDMA),0x8000/0x8001(NR5G), etc.
(lista completa: findings/rftest_command_format.md §1). **FACT.**
El (un)packing es TLV; ver rftest_command_format.md / rftest_entry_0x14.md.

### 5.B — Extraer el código paginado OFF-LINE del q6zip  (para RE estático)  [INFERENCE]
La ventana 0xd8xxxxxx-0xdcxxxxxx es q6zip descomprimido en runtime. Si el objetivo es
LEER/DESENSAMBLAR `ftm_common_dispatch`, descomprime el q6zip del propio blob en vez de
leer RAM en vivo:
- El material previo ya trabajó en esto: `scan_compressed.py`, `find_q6*.py`,
  `seg26_dec.bin`, `seg27_dec.bin` en /tmp/modemre. La tabla de páginas q6zip y los
  metadatos viven en las secciones de datos (seg26 = 38MB main data / seg20).
- Esta es la vía CORRECTA para obtener 0xd8150ed8 sin peek. (Ver seg26_dec.README.txt.)

### 5.C — DIAG_STATUS_SNAPSHOT / STATUS  (0x0C, 0x63)  [INFERENCE]
Registrados (§1). Devuelven estructuras de estado del modem (no memoria arbitraria),
pero pueden revelar punteros/estado útil. No sirven para volcar 0xd8xxxxxx. Bajo valor.

### 5.D — RAM dump / minidump (crash artifact)  [FACT: es de crash, no live]
Strings: `/ramdumps/`, `MPSS:PD dump fname open`, `wlan_minidump`,
`Solo coredump not saved to minidump`. Es el mecanismo SSR: cuando el PD/modem
crashea, escribe un ELF de dump a `/ramdumps/` vía RFS. Para provocarlo tendrías que
forzar un crash del modem (p.ej. un comando que dispare error fatal) y luego leer el
ELF de `/ramdumps/` desde el AP — el dump SÍ contendría la ventana 0xd8xxxxxx ya
descomprimida. Es intrusivo (tira el modem) y requiere acceso al FS /ramdumps del AP.
Viable como último recurso, no como lectura puntual.

### 5.E — EFS2 (subsys 20)  [FACT: no disponible en este PD]
No registrado en el diag core del modem (§2). Y aunque lo estuviera, lee FICHEROS EFS,
no RAM. No sirve para 0xd8150ed8.

### 5.F — Extended Build ID (0x7C) / QSR4  [FACT registrado, no da memoria]
0x7C está registrado (build id). Devuelve strings de build, no tabla de páginas q6zip.

====================================================================
## RESUMEN ACCIONABLE
====================================================================
1. **PEEK/POKE (0x02-0x07) = NO registrados** → no hay lectura de memoria arbitraria
   por DIAG en este modem. No está "gated": está compilado fuera. **FACT.**
2. **Ningún subsys** de memoria/peek/dump registrado (CORE=47 no expone read-by-addr;
   EFS2=20 no registrado). **FACT.**
3. **Para tocar `0xd8150ed8`**: no se lee, se INVOCA → `0x4B 0x0B <ftm_cmd_id:u16> …`
   (transporte FTM). **FACT.**
4. **Para RE estático del código paginado**: descomprimir q6zip off-line (seg26/seg20
   + scripts find_q6*.py / scan_compressed.py). **INFERENCE / vía correcta.**
5. Volcado en vivo de RAM sólo posible provocando un crash → ELF en `/ramdumps/`
   (intrusivo). **FACT (mecanismo existe, es de crash).**

--------------------------------------------------------------------
### Apéndice — evidencia / reproducibilidad
--------------------------------------------------------------------
- Nodos subsys: escaneo de seg23 (0xc8b6a000) patrón `w0==0x00ff0000`, ptr@+0x10→rodata.
- Tablas legacy (subsys 0xFF): user_tables 0xc35ca640/648/650, 0xc35ccc88, 0xc35ccca8,
  0xc35cd498, 0xc3794988 (seg21 rodata).
- Desensamblado handlers: /tmp/modemre/_dis_b13.txt (seg13, vaddr-based).
- FTM dispatch: user_table 0xc37bd1e8 (75 entradas → 0xd8150ed8).
- Strings de seguridad: búsqueda directa sobre modem_full.elf (negativa para secure-peek).
- Helper de mapeo vaddr↔offset: /tmp/modemre/mem_helpers.py.
