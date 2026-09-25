# MEMSHARE REQUEST FLOW — modem-side QMI client (service 52 / DHMS)

Baseband: Qualcomm SM6375, MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133 (Moto G82 5G).
Análisis 100% estático sobre `/tmp/modemre/modem.b*` + `clade_dec_36m.bin` (VA base CLADE
0xd8000000). Todas las VAs son `p_vaddr` (runtime). Método de xref: decodificador de calls/jumps
Hexagon (`/tmp/xref.py`, `/tmp/xref_ext.py`) + `llvm-objdump-18 --triple=hexagon --mcpu=hexagonv66`
+ búsqueda de punteros LE en segmentos de datos.

---

## TL;DR (respuesta directa a la hipótesis)

- **El cliente memshare del modem SÍ existe** como task RCINIT `memshare_qmiclient`. Su init corre
  al boot. **[FACT]**
- **El init es "connect-only": hace `qmi_client_init_instance` (conecta al servicio 52 si está
  arriba, timeout 5 s) y registra un indication-callback VACÍO. NO manda QUERY_SIZE ni ALLOC durante
  el init.** **[FACT]**
- **Las funciones que arman/mandan MEM_QUERY_SIZE (0x24), MEM_ALLOC_GENERIC (0x22) y
  MEM_FREE (0x23), y el handler de alocación por client_id, NO son llamadas desde NINGÚN punto de
  toda la imagen del firmware** (0 callers, 0 punteros válidos). **[FACT — barrido exhaustivo]**
- **IQ_CAPTURE NO dispara memshare.** El path de IQ capture (`0xd81893a4`, `0xd8189d38`, FETCH_IQ
  `0xd8189964`) no alcanza ni el ALLOC (`0xc103c000`) ni el handler (`0xc103bb0c`); usa el heap
  interno / DSM (`modem_mem_alloc`), no el cliente QMI 52. **[FACT — reachability]**
- **Conclusión sobre por qué el daemon no recibe nada:** en ESTA build, el cliente memshare del
  modem, tal como está compilado, **sólo intenta CONECTAR** al servicio 52 al boot; el código que
  realmente EMITE QUERY_SIZE/ALLOC está presente pero **no tiene disparador dentro del modem** —
  se dispara por un evento externo (indicación del AP / comando del framework RC / dependencia
  RCINIT que en stock existe pero aquí no se está satisfaciendo). Ver §5 para el camino concreto.

> ⚠️ Esto **corrige** la afirmación previa (`os_iq_path.md §3.1`) de "QUERY_SIZE una vez al boot,
> medido". Estáticamente **no hay** emisión de QUERY_SIZE gatillada por el propio modem. Lo que sí
> hay al boot es el `qmi_client_init_instance` (connect). Si en un log se vio un QUERY_SIZE, vino
> de un trigger externo (AP mandando indicación de "service up" o un comando), no de código
> auto-iniciado del modem.

---

## 1. CÓDIGO DEL CLIENTE MEMSHARE (VAs)

### 1.1 Task RCINIT y descriptor  **[FACT]**
- String `"memshare_qmiclient"` @ **0xc3577c60** (b21 rodata).
- Tabla name→descriptor (RCINIT) @ **0xc3581d38**: `memshare_qmiclient` → descriptor **0xc357c704**.
- Descriptor RCINIT @ **0xc357c704** (stride 0x44):
  - `+0x00` name = 0xc3577c60 ("memshare_qmiclient")
  - `+0x08` prio = **0x700**
  - `+0x0c` **entry/init fn = 0xc103c300**
  - `+0x14` group PRI_ORDER = "TC_PRI_ORDER"
  - `+0x18` stack = 0x1000
- ⇒ Es una task registrada en RCINIT, corre al bring-up del modem. **[FACT]**

### 1.2 Init del cliente — `0xc103c300` (b13)  **[FACT]**
Thunk en b13 hacia CLADE. Secuencia:
1. `call 0xc0986fc0` = `qmi_client_notifier_init` → notifier handle, guardado en **0xcb8bab08**.
2. `r5:4 = memd(gp+0x11a80)` (base de service-objects), `call 0xc103c38c → 0xd9f93b68`
   = **get_service_object(idx=1)** → guardado en **0xcb8bab10**.
3. `call 0xc103c394 → 0xd80cd474` = **`qmi_client_init_instance`** con:
   - r0 = service_obj (memshare, service_id interno "6" → svc 52)
   - r1 = **0xffff** (QMI_CLIENT_INSTANCE_ANY)
   - r2 = **0xc103c2fc = indication_cb → ES UN STUB VACÍO (`jumpr r31`)**
   - r4 = 0xcb8bab08 (notifier / cb_data)
   - r5 = **0x1388 (5000 ms timeout)**
   - stack[0] = &client_handle (0xcbf5b680-área)
4. client_handle → **gp+0xc69c**; status de init → byte **0xc8bedd54** (0xff=fail, 0/-1 según ret).
- **NO hay QUERY_SIZE ni ALLOC aquí.** Es un connect síncrono con callback no-op. **[FACT]**

### 1.3 Funciones que ARMAN/MANDAN los mensajes (b13, thunks→CLADE)  **[FACT]**
Descubiertas por el `r1 = #<msgid>` justo antes de la llamada al send genérico:

| VA (fn) | msg-id | Rol | envía vía |
|---|---|---|---|
| **0xc103c248** | `#0x24` | **MEM_QUERY_SIZE** (build+send) | `0xc103c174 → 0xd80cce9c` (send_sync) |
| **0xc103c000** | `#0x22` | **MEM_ALLOC_GENERIC** (build+send) | `0xc103c174 → 0xd80cce9c` |
| **0xc103c188** | `#0x23` | **MEM_FREE_GENERIC** (build+send) | `0xc103c174 → 0xd80cce9c` |
| 0xc103c174 | — | thunk → **0xd80cce9c** (`qmi_client_send_msg_sync`) | — |

Send genérico QCCI: **0xd80cce9c** (sync) y **0xd80cac78** (TX, log `"QCCI TX %s: Svc_id:0x%x ...
Txn:0x%x Msg:0x%x"` @0xc35aa65c). Confirma capa QMI-CCI real. **[FACT]**

### 1.4 Handler de alocación por client_id — `0xc103bb0c` (b13)  **[FACT]**
- Toma `r17 = client_id`; indexa tabla de estado por-cliente en **.bss @ 0xcb8baa60 + client_id*8**:
  - `memb(0xcb8baa64 + cid*8)` = flag "ya-alocado" (si ==1, salta el (re)pedido).
  - `memw(0xcb8baa60 + cid*8)` = contador/handle.
- Rama a **0xc103bbf8 → call 0xc103c000 (ALLOC_GENERIC)** cuando corresponde.
- **Este es el único punto que llama a ALLOC_GENERIC en toda la imagen.** **[FACT]**

### 1.5 IDL / service object  **[FACT]**
- get_service_object: **0xd9f93b68** (thunk 0xc103c38c). Función de mapeo (svc==6, ver==1/2/3…)
  que devuelve punteros a la IDL del memshare.
- Tabla IDL de mensajes @ **0xc8c1d3b0** (b23), pares `(msg_id, encode_fn)`:
  - `0x10020` → 0xd80cff24  (ALLOC 0x20)
  - `0x10021` → 0xd80cff84  (0x21)
  - `0x10022` → 0xd80d0014  (ALLOC_GENERIC 0x22)
  - `0x10023` → 0xd80d00a0  (QUERY 0x23/0x24 build)
  - seguido de constante **0x00800000 (8 MiB)** @0xc8c1d3d0 y timeout 0x7d0. **[FACT]**

### 1.6 Estado persistente del cliente (todo en .bss = b24, sin fichero)  **[FACT]**
| VA | contenido |
|---|---|
| 0xcb8bab08 | notifier handle |
| 0xcb8bab10 | service object ptr |
| gp+0xc69c  | client_handle (de init_instance) |
| gp+0xc698  | handle usado por los senders |
| 0xc8bedd54 | byte status de init (0xff=fail) |
| 0xcb8baa60 + cid*8 | **tabla de estado por client_id** (flag+contador) |
| 0xc8bedd54, 0xcb8baa64+cid*8 | flags "ya-alocado" |

**Todos están en b24 (.bss, 48 MB, sin backing en fichero) ⇒ se PONEN A CERO en cada carga del
modem.** No persisten entre restart de remoteproc (ver §3). **[FACT]**

---

## 2. ¿CUÁNDO PIDE? — init vs on-demand

**[FACT] Al boot (init RCINIT `0xc103c300`): SÓLO connect (`qmi_client_init_instance`), sin
QUERY_SIZE ni ALLOC. El indication-callback es un stub vacío (`0xc103c2fc`).**

**[FACT] On-demand (QUERY/ALLOC/FREE + handler): el código existe pero NO tiene ningún caller ni
puntero válido en toda la imagen.** Barrido exhaustivo:
- `0xc103c248` (QUERY): **0 callers, 0 punteros.**
- `0xc103c188` (FREE): **0 callers, 0 punteros.**
- `0xc103bb0c` (ALLOC handler): **0 callers, 0 punteros.**
- `0xc103c000` (ALLOC send): llamado **sólo** desde `0xc103bb0c` (que a su vez es huérfano).
- Único puntero encontrado a estas fns = 0xc103c300 (INIT) desde la tabla RCINIT (esperado); el
  "hit" a 0xc103c000 en b26 está **desalineado (offset 3)** ⇒ espurio (byte-pattern en CLADE
  comprimido, no un puntero real).

**Interpretación [INFERENCE, alta]:** el disparador de QUERY_SIZE/ALLOC del cliente memshare **no
es código auto-iniciado del modem** en esta build. En el diseño Qualcomm de DHMS/memshare, el ALLOC
lo dispara típicamente:
  (a) una **indicación del AP** ("service up" / `mem_share` config), que el cliente maneja en su
      indication-cb — **pero aquí ese cb es un stub vacío**, o
  (b) un **hook de otra task** (RF/cal/DPM) que llama al handler por client_id — **pero ningún
      caller existe en la imagen**.
Ambos caminos están cortados estáticamente. El código de request está "linkeado pero muerto" desde
el punto de vista del grafo de llamadas interno del modem.

---

## 3. ¿POR QUÉ NO RE-PREGUNTA EN RESTART DE REMOTEPROC?

- **[FACT]** Todo el estado del cliente (handles, flags "ya-alocado", tabla por client_id
  0xcb8baa60) vive en **.bss (b24, sin fichero) ⇒ se re-cero al recargar el modem.** Por lo tanto
  **NO es** que el modem "crea que ya tiene la memoria de un intento previo": tras un
  remoteproc stop/start el estado arranca limpio.
- **[FACT/INFERENCE]** La razón real de que "no re-pregunte" es la de §2: **el modem nunca pregunta
  espontáneamente en primer lugar** (el init sólo conecta; el path de request no tiene trigger
  interno). Reiniciar remoteproc re-ejecuta el mismo init connect-only ⇒ mismo resultado (silencio).
- **No hay un flag/handle "sticky" que limpiar** — el estado ya se limpia solo. Limpiar memoria del
  modem no cambia nada porque el problema no es estado rancio, es **ausencia de disparador**.

> Nota sobre la conexión: `qmi_client_init_instance` con timeout 5 s (§1.2). Si al momento del init
> el servicio 52 del AP **no está registrado todavía**, el connect **falla** (status 0xff en
> 0xc8bedd54) y **no se reintenta** (no hay indication-cb que lo re-dispare cuando el AP aparece,
> porque el cb es vacío). ⇒ **El daemon DEBE estar registrado en QMI 52 ANTES de que corra el init
> del modem** (grupo TC_PRI_ORDER del bring-up). Esto es lo único de timing que sí importa. **[FACT
> del código + INFERENCE de la semántica de init_instance].**

---

## 4. CLIENT-ID 1 Y EL TAMAÑO

- **[FACT]** El handler `0xc103bb0c` y el sender `0xc103c000` están **parametrizados por
  client_id** (índice a 0xcb8baa60 + cid*8). No hay un client_id "hardcodeado a 1" en el sender: el
  cid llega como argumento. La tabla por-cliente soporta múltiples client_ids.
- **[FACT]** La IDL trae la constante **0x00800000 = 8 MiB** (@0xc8c1d3d0) y un tamaño **0x2000**
  aparece en varios sitios del módulo; el `0x500000 (5 MiB)` de stock **no** está hardcodeado en el
  cliente — el tamaño va en el TLV del request/response (el AP responde el size). ⇒ El "5 MiB /
  client-id 1" es **contrato de la config del AP (peripheral-size)**, no una constante fija en el
  modem. **[FACT]**
- **[UNKNOWN]** Qué client_id concreto usaría IQ_CAPTURE **si** el path estuviera conectado —
  irrelevante aquí porque **IQ_CAPTURE no usa memshare** (§ siguiente). No se hallaron otros
  client_ids con propósito distinto porque no hay callers que pasen ningún cid.

### 4.1 ¿IQ_CAPTURE usa client-id 1 (5 MiB) para las muestras?  **NO**  **[FACT]**
- Reachability: `0xd81893a4` (IQ master), `0xd8189964` (FETCH_IQ) **no alcanzan** `0xc103c000`
  (ALLOC) ni `0xc103bb0c` (handler).
- El helper de contexto `0xd8189d38` aloca **28 bytes** (0x1c) del heap interno (`0xd8062414`), no
  un buffer IQ vía QMI 52.
- El buffer de muestras (`p_sample_capture_buffer`) se aloca por el allocator interno del modem
  (DSM / `modem_mem_alloc`), **no** por el cliente memshare.
- ⇒ **Correr IQ_CAPTURE NO dispara un request memshare.** La hipótesis "IQ on-demand pide memshare"
  queda **refutada estáticamente.** **[FACT]**

---

## 5. CÓMO FORZAR EL REQUEST — camino concreto

Dado que el disparador interno está ausente, las opciones ordenadas por probabilidad de éxito:

### (a) Orden de arranque — NECESARIO pero probablemente INSUFICIENTE  **[FACT+INFERENCE]**
- Tener el daemon **registrado en QMI 52 ANTES** de que corra el init del modem (grupo
  TC_PRI_ORDER). Semántica: `qmi_client_init_instance` (timeout 5 s) hace el connect; si el
  servicio está arriba, conecta y guarda el handle. Con el cb de indicación vacío, si el AP aparece
  tarde **no hay reintento**. ⇒ daemon+módulo listos antes del bring-up MSS.
- Esto asegura la **conexión**, pero por §2 el modem **igual no emite QUERY/ALLOC solo**.

### (b) Provocar una INDICACIÓN del servicio 52 desde el AP  **[INFERENCE — mejor candidato]**
- En DHMS, el flujo canónico es: el **AP** manda una indicación / el modem responde con ALLOC. El
  cb de indicación del modem aquí es un stub, así que una indicación cruda probablemente no
  dispare ALLOC en esta build. **UNKNOWN** si existe una variante de indicación que el framework
  QMI-CCI enruta a los encode-fns de la IDL (0xc8c1d3b0) sin pasar por el cb de la app.
- **Acción de prueba en vivo:** con el daemon arriba, emitir desde el AP los mensajes/indicaciones
  del servicio 52 que un memshare-server Qualcomm normalmente envía tras el registro del cliente
  (p.ej. la indicación de "config/ready") y observar si llega un QUERY (0x24) o ALLOC (0x22).

### (c) Disparador por comando FTM/Diag  **[UNKNOWN — descartado por ahora]**
- No se halló ningún caller FTM/Diag hacia el handler/senders (§2, §4.1). No hay evidencia de un
  comando que fuerce el ALLOC. Requiere fuzzing de comandos si (b) falla.

### (d) Camino de trabajo recomendado (concreto)
1. **Garantizar orden:** módulo `rhodep_memassign` cargado con `assigned=1` y daemon respondiendo
   QMI 52 con **5 MiB** (no 0) en QUERY_SIZE, **ANTES** del bring-up MSS
   (`Before=rmtfs.service`, añadir `ExecStartPre=/sbin/modprobe rhodep_memassign`). — Esto sólo
   asegura el connect (§5a).
2. **Instrumentar el daemon** para loguear TODO tráfico del servicio 52 (no sólo requests): también
   los `qmi_connect`/notify del framework, para confirmar que el connect del modem llega. Si el
   connect llega pero nunca un QUERY/ALLOC ⇒ confirma §2 en vivo.
3. **Emular el lado servidor completo (§5b):** hacer que el daemon, tras ver el connect del cliente
   del modem, **envíe la indicación de servicio** que el memshare-server de stock manda. Ver si el
   modem responde con QUERY/ALLOC.
4. **Verificación cruzada:** si (3) no dispara nada, el trigger real está fuera del cliente memshare
   (otra task del modem que en stock llama al handler `0xc103bb0c` y en esta build/config no se
   activa — p.ej. una feature RF/cal que está gated off). Buscar qué RCINIT-group/feature-flag
   habilita ese caller (fuera del alcance de este documento; requiere mapear el gating de features).

---

## 6. RESUMEN FACT / INFERENCE / UNKNOWN

**FACT**
- Cliente memshare = task RCINIT `memshare_qmiclient`, init `0xc103c300`, prio 0x700, grupo
  TC_PRI_ORDER, stack 0x1000. Descriptor @0xc357c704, string @0xc3577c60.
- Init = `qmi_client_init_instance` (0xd80cd474) connect-only, timeout 5 s, **indication-cb vacío
  (0xc103c2fc)**. No manda QUERY/ALLOC.
- Senders: QUERY `0xc103c248` (msg 0x24), ALLOC_GENERIC `0xc103c000` (msg 0x22), FREE `0xc103c188`
  (msg 0x23). Send genérico QCCI `0xd80cce9c`/`0xd80cac78`.
- Handler por client_id `0xc103bb0c`; tabla de estado .bss @0xcb8baa60+cid*8.
- IDL msg-table @0xc8c1d3b0 (0x10020..0x10023), constante 8 MiB @0xc8c1d3d0.
- **QUERY, FREE y el handler tienen 0 callers y 0 punteros en toda la imagen.** ALLOC sólo lo llama
  el handler huérfano.
- **IQ_CAPTURE no alcanza memshare** (reachability desde 0xd81893a4/0xd8189964/0xd8189d38).
- Todo el estado del cliente está en .bss (b24) ⇒ se re-cero al recargar el modem (no persiste).

**INFERENCE**
- El disparador de QUERY/ALLOC es externo (indicación AP o task gated); en esta build está cortado.
- El único requisito de timing real es daemon@QMI52 listo antes del init del modem (por el
  connect síncrono sin reintento). El re-query no ocurre por ausencia de trigger, no por estado
  rancio.
- Camino más prometedor: emular el lado servidor completo (indicación de servicio) desde el daemon.

**UNKNOWN**
- Si el framework QMI-CCI enruta alguna indicación del servicio 52 a los encode-fns IDL sin pasar
  por el cb vacío (probar en vivo, §5b).
- Qué feature/RCINIT-group habilita, en stock, el caller del handler `0xc103bb0c` (el trigger real
  del ALLOC). Requiere mapear el gating de features RF/cal.
- Qué client_id/size usaría ese caller (irrelevante mientras el path esté cortado).

---

## 7. HERRAMIENTAS/COMANDOS USADOS (reproducibilidad)
- Descubrir string/descriptor: búsqueda de `memshare_qmiclient` en `modem.b21` (off 0x24c60 →
  VA 0xc3577c60) y punteros LE en b21/b23.
- Desensamblar CLADE: `/tmp/modemre/dis36.sh <va> <len>` (base 0xd8000000).
- Desensamblar b13/b05: extraer slice + `mkelf.py` + `llvm-objdump-18 --triple=hexagon
  --mcpu=hexagonv66`.
- Xrefs: `/tmp/xref.py` (relativos) y `/tmp/xref_ext.py` (con immext) + barrido de punteros LE en
  todos los `modem.b*` y `clade_dec_36m.bin`.
- Reachability: `/tmp/reach_ms.py <root_va> <target_va...>`.
