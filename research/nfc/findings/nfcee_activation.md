# NFCEE MODE_SET 0x03 + listen no activa — analisis NCI (S3FWRN5 / S3NRN4V, rhodep)

Analisis de protocolo sobre los logs en `/tmp/nfcre3/` y los patches 0111/0112/0113.
No se toco el repo ni los `.tgz`. Toda afirmacion esta etiquetada FACT / INFERENCE /
UNKNOWN.

---

## 0. El flujo real capturado (rawnfcee.log), decodificado byte-a-byte

FACT — secuencia de la corrida "listen eSE" (rawnfcee.log #11..#22):

```
#11 20 02 05  01 00 02 e8 03          CORE_SET_CONFIG  TOTAL_DURATION=0x03e8 (1000 ms)   -> #12 status 0x0
#13 20 02 04  01 50 01 00             CORE_SET_CONFIG  LF_PROTOCOL_TYPE=0x00             -> #14 status 0x0
#15 22 01 02  83 01                   NFCEE_MODE_SET   NFCEE=0x83 mode=0x01 ENABLE       -> #16 42 01 01 03  status 0x03 REJECTED
#17 21 01 1b  <27 bytes>              RF_SET_LISTEN_MODE_ROUTING (LMRT)                  -> #18 status 0x0
#19 21 03 05  02 80 01 82 01          RF_DISCOVER  (NFC-A listen 0x80 + NFC-F listen 0x82)-> #20 status 0x0
... sin RF_INTF_ACTIVATED_NTF ...
#21 21 06 01 00                       RF_DEACTIVATE (timeout)                            -> #22 status 0x0
```

FACT — el LMRT (`21 01 1b 00 05 00 03 83 3b 00 01 03 00 3b 02 01 03 83 3b 80 00 03 00 3b 02 01 03 00 3b 05`)
decodifica a **5 entradas** (no 3), `more=0`:

| # | tipo        | nfcee_id | power | valor          |
|---|-------------|----------|-------|----------------|
| 1 | TECHNOLOGY  | **0x83** | 0x3b  | NFC-A (0x00)   |
| 2 | PROTOCOL    | 0x00     | 0x3b  | T2T (0x02)     |
| 3 | PROTOCOL    | **0x83** | 0x3b  | MIFARE (0x80)  |
| 4 | TECHNOLOGY  | 0x00     | 0x3b  | NFC-F (0x02)   |
| 5 | PROTOCOL    | 0x00     | 0x3b  | NFC-DEP (0x05) |

FACT — **la tecnologia NFC-A (entry#1) y el protocolo MIFARE (entry#3) estan ruteados
al NFCEE 0x83**, el mismo NFCEE cuyo `NFCEE_MODE_SET(ENABLE)` acaba de ser **RECHAZADO**.

FACT CRITICO — **en TODA la captura no aparece ningun `RF_DISCOVER_MAP` (opcode `21 00`,
GID=1 OID=0)** antes del `RF_DISCOVER`. El unico `RF_DISCOVER_MAP` del log es el de la
corrida de *poll/reader* (no visible; en la corrida de listen no se envia). El flujo de
listen es: `SET_CONFIG -> MODE_SET -> LMRT -> RF_DISCOVER`. Falta MAP.

---

## 1. Por que `NFCEE_MODE_SET(0x83, ENABLE)` -> status 0x03 REJECTED

**Causa (INFERENCE, alta confianza): el NFCEE 0x83 ya esta ENABLED, y se le esta
mandando un ENABLE redundante — o el estado del NFCEE quedo "sucio" de una corrida previa
sin un `NFCEE_DISCOVER` nuevo que lo devuelva a estado conocido.**

Evidencia que sostiene esto:

- FACT — nfc.md:510 documenta que el `NFCEE_DISCOVER_NTF` reporta
  `83 01 01 80 01 a0 01 02 00`, es decir **status byte = 0x01 = CONNECTED/ENABLED** ya en
  enumeracion. El NFCEE 0x83 se enumera *ya conectado*. En NCI, `NFCEE_MODE_SET(ENABLE)`
  sobre un NFCEE que ya esta enabled es un no-op legal en algunos CLF pero **muchos
  firmwares (incluido el patron Samsung) lo contestan REJECTED (0x03)** en vez de OK.
- FACT — current_flow.txt:9: "antes MODE_SET dio 0x0 una vez (intermitente), ahora 0x3
  consistente". Ese patron es exactamente el de un NFCEE que quedo ENABLED: la **primera**
  vez (estado fresco tras discover) acepta el ENABLE (0x0), y las siguientes lo rechazan
  (0x3) porque ya esta enabled. El estado del NFCEE persiste entre corridas del tool
  porque el adaptador NCI no se resetea entre `rhodep-nfc listen` sucesivos.
- FACT — el patch 0112 emite `NFCEE_DISCOVER` **una sola vez, al final de
  `nci_open_device`** (0112 lineas 45-52). No se re-emite por cada `start_poll`/listen.
  Asi que el NFCEE_DISCOVER que "limpia" el estado solo ocurre en el bring-up del
  adaptador, no en cada intento de listen -> el estado ENABLED se acumula.

**Descartes:**
- (b)/(c) "falta NFCEE_DISCOVER antes" — FACT: el discover SI se hizo (en open_device),
  el 0x83 se enumero. No es que falte del todo; es que no se re-hace y el estado quedo
  enabled. INFERENCE.
- (d) "orden MODE_SET debe ir DESPUES de DISCOVER_NTF y ANTES de RF_DISCOVER" — FACT: el
  orden temporal es correcto (discover en open, mode_set en listen antes de RF_DISCOVER).
  El orden **no** es la causa del 0x03.
- NOTA (INFERENCE): status 0x03 en el contexto de MODE_SET tambien puede significar
  "NFCEE_STATUS: el NFCEE no admite el cambio de modo pedido en este momento" (p.ej.
  requiere una activacion de red HCI/ETSI previa que este CLF no tiene, o esta ocupado).
  Pero el patron intermitente 0x0->0x3 apunta primero a "ya enabled".

### Fix del 0x03 (dos opciones, la primera es la correcta)

**Fix A (recomendado) — no re-enable si ya esta enabled; y re-discover antes del listen.**
1. Registrar el status del `NFCEE_DISCOVER_NTF` (byte 2). Si 0x83 ya viene ENABLED
   (0x01), **no** mandar `NFCEE_MODE_SET(ENABLE)` en el path de listen; tratar el eSE
   como ya habilitado. INFERENCE: esto elimina el 0x03 (no se manda un ENABLE redundante).
2. Alternativa robusta: antes de cada sesion de listen, re-emitir `NFCEE_DISCOVER`
   (`22 00 00` en NCI 2.0) para releer el estado real de cada NFCEE, y solo mandar
   `MODE_SET(ENABLE)` si el NTF dice DISABLED (status != 0x01).

**Fix B (diagnostico) — probar DISABLE->ENABLE.** Mandar `NFCEE_MODE_SET(0x83, 0x00)`
(DISABLE) y luego `NFCEE_MODE_SET(0x83, 0x01)` (ENABLE). Si el ENABLE post-disable da
0x0, confirma FACT que el 0x03 era "ya enabled". UNKNOWN si el firmware permite DISABLE
del eSE.

UNKNOWN — no hay captura del `NFCEE_MODE_SET_NTF` (GID=2 OID=1 como NTF, `62 01 ...`).
En NCI el MODE_SET real se completa con un NTF, no solo el RSP. Habria que capturarlo
para confirmar el estado final. El patch 0113 asume que llega ("plus the
NFCEE_MODE_SET_NTF", nfc.md:529) pero en rawnfcee.log **no aparece** ningun `62 01`.

---

## 2. Por que NO activa el listen (aunque RF_DISCOVER de status 0x0)

Hay **tres** causas concurrentes; cualquiera de ellas sola ya impide la activacion. Un
`status 0x0` en RF_DISCOVER solo dice "acepte la config de discovery", NO "voy a activar".

### 2a. FACT — Falta `RF_DISCOVER_MAP` con entradas LISTEN (bloqueante duro)

En el flujo de listen capturado **no se envia `RF_DISCOVER_MAP` (`21 00`)**. Sin un MAP
que asocie, en modo LISTEN, cada protocolo con una interfaz RF, el CLF **no sabe con que
interfaz activar** cuando un lector lo selecciona -> no levanta `RF_INTF_ACTIVATED_NTF`.

- FACT — el orden canonico AOSP/NFA es: `CORE_SET_CONFIG -> RF_DISCOVER_MAP -> (NFCEE
  stuff) -> RF_SET_LISTEN_MODE_ROUTING -> RF_DISCOVER`. El flujo actual saltea el MAP.
- FACT — el patch 0111 SI construye un `RF_DISCOVER_MAP` con T2T->FRAME en LISTEN
  (0111 lineas 176-187), y en la corrida host de nfc.md:260 se ve enviado
  (`GID=0x1 OID=0x0 plen=19 -> status 0x0`). Pero **en la corrida eSE del rawnfcee.log
  ese MAP no esta**. INFERENCE: el path eSE (patch 0113) reordeno / omitio el envio del
  DISCOVER_MAP, o el MAP se hace en open_device y no en start_poll y por eso no reaparece;
  de cualquier modo, **en la ventana de listen que fallo no hubo MAP con LISTEN**.
- INFERENCE: para ruteo off-host puro (todo al eSE via NFCEE Direct) el MAP de host podria
  no ser estrictamente necesario, PERO como el LMRT tambien tiene entradas al HOST (T2T,
  NFC-DEP) y NFC-A tech al eSE, el CLF necesita el MAP para saber que interfaz usar.

### 2b. FACT/INFERENCE — Se rutea a un NFCEE DESHABILITADO

FACT — LMRT entry#1 (NFC-A tech) y entry#3 (MIFARE) apuntan a NFCEE 0x83. FACT — el
`MODE_SET(0x83)` fue REJECTED. INFERENCE — aunque el chip enumero 0x83 como CONNECTED,
el stack no tiene confirmacion de que el eSE este listo para recibir el campo (no hay
`NFCEE_MODE_SET_NTF` capturado, no hay `RF_NFCEE_ACTION_NTF`). Rutear NFC-A tech a un
NFCEE que el propio stack considera "no habilitado con exito" es incoherente y es un
segundo motivo para que el CLF no active.

### 2c. INFERENCE — El front-end de listen NFC-A no se enciende por perfil RF vendor

Esta es la conclusion ya alcanzada en nfc.md:606-616: con Flipper Zero (lector permisivo)
tampoco activa, ni en host ni en eSE, aun con todo status 0x0. INFERENCE (del propio doc):
el firmware Samsung solo arma el listen NFC-A a traves de un **perfil RF de registros que
el HAL carga** y que el path NCI plano no reproduce. Esto es una hipotesis fuerte pero
**UNKNOWN** hasta capturar/replicar el stream vendor de Android en listen.

**Prioridad de ataque:** 2a y 2b son bugs de protocolo concretos y baratos de arreglar y
**deben corregirse primero** (aun no se probaron: el flujo nunca tuvo MAP-listen + eSE
realmente enabled). Solo si tras arreglar 2a+2b sigue sin activar, 2c (perfil RF vendor)
queda como el muro real.

---

## 3. La secuencia NCI correcta byte-a-byte (activar + rutear al eSE)

INFERENCE — orden canonico. Todos los opcodes verificados contra los structs de los
patches y el CORE_INIT (`nfcc_features 0x80067e1a`: technology-based routing OK,
interfaces 0x00-0x03, max_routing_table_size 1170).

### (a) Habilitar el eSE 0x83 correctamente

```
# 1. Re-enumerar para leer estado real (NCI 2.0, sin payload)
TX  22 00 00                          NFCEE_DISCOVER_CMD
RX  42 00 01 00                       NFCEE_DISCOVER_RSP status 0x0
RX  62 00 09 83 01 01 80 01 a0 01 02 00   NFCEE_DISCOVER_NTF (id 0x83, status 0x01=ENABLED, proto 0x80)
RX  62 00 0e 15 01 01 00 01 04 ...    NFCEE_DISCOVER_NTF (id 0x15 UICC)

# 2. MODE_SET SOLO si el NTF dice DISABLED (status != 0x01).
#    Si 0x83 ya viene 0x01 (ENABLED), OMITIR este comando -> evita el 0x03.
#    Si viniera DISABLED:
TX  22 01 02 83 01                    NFCEE_MODE_SET(0x83, ENABLE)
RX  42 01 01 00                       RSP status 0x0
RX  62 01 02 00 83                    NFCEE_MODE_SET_NTF  (ESPERAR ESTE NTF antes de seguir)
```

FACT — el problema actual es mandar el paso 2 incondicionalmente. El fix es hacerlo
condicional al status del NTF.

INFERENCE — opcional NCI 2.0, mantener el eSE alimentado/enlazado en listen (Android lo
usa segun `OFFHOST_AID_ROUTE_PWR_STATE=0x3B`):
```
TX  20 03 02 83 01                    NFCEE_POWER_AND_LINK_CTRL (GID=2 OID=3), NFCEE 0x83, config 0x01
```
UNKNOWN — si el firmware contesta UNKNOWN_OID (0x08) no lo soporta; probar y observar.
(nfc.md:572 sugiere `21 03 02 83 03`; OJO, el GID/OID correcto de NFCEE_POWER_AND_LINK
es GID=2 OID=3 -> opcode `20 03`, no `21 03`. Verificar contra la spec NCI 2.0 del CLF —
FACT: `21 03` es RF_DISCOVER, no power-and-link; el ejemplo previo del doc estaba mal.)

### (b) RF_DISCOVER_MAP con LISTEN (imprescindible, hoy falta)

INFERENCE — mapear en LISTEN los protocolos que se van a rutear. Para MIFARE-via-eSE el
CLF usa NFCEE Direct (0x00) para el protocolo off-host; T2T en FRAME si el host tambien
participa. Formato: `21 00 <len> <num> [proto mode intf]...`

```
TX  21 00 07  02  04 02 00  02 02 01
              |   |         |
              |   |         +-- entry2: ISO-DEP(0x04)? -> no. Ver nota.
              |   +-- entry1: proto=0x04? 
              +-- num_mappings=2
```
NOTA (FACT del patch 0111): el CLF **rechaza (status 0x1) si 0x80 (MIFARE) aparece en el
DISCOVER_MAP**. Por eso MIFARE NUNCA va en el MAP; va solo en el LMRT. El MAP en listen
debe contener las interfaces de los protocolos "mapeables":
```
TX  21 00 04  01  02 02 01            RF_DISCOVER_MAP: 1 mapping
              |   |  |  +-- rf_interface = 0x01 FRAME
              |   |  +-- mode = 0x02 LISTEN (bit listen)
              |   +-- rf_protocol = 0x02 T2T
              +-- num = 1
RX  41 00 01 00                       RSP status 0x0
```
INFERENCE — para la parte MIFARE ruteada al eSE, la activacion off-host la resuelve el
LMRT + NFCEE Direct; no requiere (y no admite) entrada MIFARE en el MAP. Si el objetivo
es SOLO eSE, el MAP minimo puede incluso omitirse, pero incluir T2T/FRAME-listen no daña
y da al CLF una interfaz valida para NFC-A. UNKNOWN — el set exacto de mappings que este
firmware exige en listen; hay que probar {T2T/FRAME}, y si falla agregar
{ISO-DEP/ISO-DEP}.

### (c) LMRT ruteando NFC-A + MIFARE al eSE (corregido)

El LMRT actual es funcional en formato (status 0x0) pero mezcla host y eSE de forma
incoherente. Para eSE puro, INFERENCE:

```
TX  21 01 0e 00 02                    more=0, num_entries=2
        00 03 83 3b 00                entry1 TECHNOLOGY: nfcee=0x83 power=0x3b NFC-A(0x00)
        01 03 83 3b 80                entry2 PROTOCOL:   nfcee=0x83 power=0x3b MIFARE(0x80)
RX  41 01 01 00                       RSP status 0x0
```
FACT — power_state 0x3b es el que usa el vendor (0111:88, RE del HAL Samsung); incluye
switched-on + switched-off (card answer con pantalla apagada). Mantenerlo.

Quitar del LMRT las entradas al HOST (T2T->0x00, NFC-F tech->0x00, NFC-DEP->0x00) cuando
el objetivo es eSE puro: enturbian el ruteo y arman el peer NFC-DEP parasito. INFERENCE.

### (d) RF_DISCOVER A + F listen

```
TX  21 03 05 02 80 01 82 01           RF_DISCOVER: NFC-A listen(0x80) + NFC-F listen(0x82)
RX  41 03 01 00                       RSP status 0x0
```
FACT — el offering A+F ya es el actual y es correcto para este chip (nfc.md:539: no
enciende el front-end con NFC-A solo). Mantener LF_PROTOCOL_TYPE=0 para matar el peer
NFC-DEP sobre F (ya se hace, status 0x0).

### Orden final consolidado

```
[open_device]  ... CORE_RESET/INIT ... NFCEE_DISCOVER (una vez)
[start listen]
  CORE_SET_CONFIG  TOTAL_DURATION=1000ms          (20 02 05 01 00 02 e8 03)
  CORE_SET_CONFIG  LF_PROTOCOL_TYPE=0             (20 02 04 01 50 01 00)
  NFCEE_DISCOVER (re-leer estado)                 (22 00 00)  [opcional pero robusto]
  NFCEE_MODE_SET(0x83,ENABLE) SOLO si DISABLED    (22 01 02 83 01) + esperar NTF 62 01
  RF_DISCOVER_MAP  (T2T/FRAME/LISTEN)             (21 00 04 01 02 02 01)   <-- HOY FALTA
  RF_SET_LISTEN_MODE_ROUTING (NFC-A tech + MIFARE -> 0x83)  (21 01 0e ...)
  RF_DISCOVER  (A listen + F listen)              (21 03 05 02 80 01 82 01)
```

---

## 4. Experimento de aislamiento minimo (host listen sin eSE)

**Objetivo:** separar "el RF listen NFC-A no enciende" (2c, muro vendor) de "el ruteo al
eSE esta mal" (1 + 2b). Si con el path HOST (patch 0111, con LA_*) un lector VE el UID,
entonces el RF listen SI funciona y el problema es exclusivamente el eSE/ruteo. Si el
host tampoco activa, el muro es el front-end RF (2c) y el eSE es irrelevante hasta
resolverlo.

**Como forzar el path host (patch 0111) — NO tocar el eSE:**

FACT — en el patch 0113 el path host vs eSE se bifurca por `ndev->ese_nfcee_id`
(0113:134,156,183,214,227). Si `ese_nfcee_id != 0` -> path eSE. Para forzar host:

- **Opcion 1 (sin recompilar):** ejecutar el listen **inmediatamente despues de un
  bring-up del adaptador donde NFCEE_DISCOVER devuelva num_nfcee=0 o no haya corrido** —
  no viable de forma fiable. Mejor Opcion 2.
- **Opcion 2 (un byte):** en `nci_nfcee_discover_ntf` (patch 0113, ntf.c) **no** setear
  `ndev->ese_nfcee_id` (comentar el bloque `if (skb->len >= 3){...ndev->ese_nfcee_id=...}`).
  Con `ese_nfcee_id == 0`, todo el patch 0113 cae al path HOST del patch 0111: setea
  LA_BIT_FRAME_SDD/PLATFORM_CONFIG/NFCID1/SEL_INFO, LMRT a HOST (0x00), sin MODE_SET.
  Recompilar `nci.ko`. Esto aisla limpio: **listen NFC-A ruteado al HOST, sin eSE**.

**Comando de prueba:**
```
sudo systemctl stop neard
sudo rhodep-nfc listen 04:35:3d:6a:f7:54:80 0x08
# presentar Flipper Zero / NFC Tools / telefono lector a la antena
```
FACT — SEL_RES 0x08 = MIFARE Classic; 0x00 = NFC-A bare; el reader deberia listar un
tag NFC-A con ese UID.

**Interpretacion del resultado:**
- Lector VE el UID (aunque no lea datos) -> **el RF listen NFC-A activa**. El problema es
  100% eSE/ruteo (arreglar seccion 1 + 3). GRAN avance.
- Lector NO ve nada, y no hay `RF_INTF_ACTIVATED_NTF` -> confirma 2c: el front-end NFC-A
  listen no enciende por perfil RF vendor. FACT — esto coincide con lo ya medido en
  nfc.md:222-234 (con NFC-A solo, nada; con A+F, aparece como FeliCa). 

**Refinamiento clave del experimento (lo que aun NO se probo bien):** en TODAS las
corridas host previas (nfc.md) faltaba el `RF_DISCOVER_MAP` con LISTEN **o** el orden
MAP->LMRT->DISCOVER no era el canonico. El experimento minimo debe incluir **el MAP de
listen ANTES del LMRT** (seccion 3b). Es decir, la variable que nunca se aislo
correctamente es "MAP-listen presente + orden canonico + host". Ese es el experimento
que realmente falta:

```
CORE_SET_CONFIG (LA_BIT_FRAME_SDD, PLATFORM_CONFIG, NFCID1, SEL_INFO, TOTAL_DURATION)
RF_DISCOVER_MAP  T2T/FRAME/LISTEN        (21 00 04 01 02 02 01)   <-- asegurar que va
RF_SET_LISTEN_MODE_ROUTING  NFC-A tech + T2T -> HOST(0x00)
RF_DISCOVER  A + F listen
```
Solo cuando ESTA secuencia (host, MAP-listen, orden canonico) tampoco active, queda
probado el muro RF vendor.

---

## 5. Resumen ejecutivo

- **NFCEE_MODE_SET 0x03 (REJECTED):** INFERENCE alta — el NFCEE 0x83 **ya esta ENABLED**
  (enumerado con status 0x01), y se le manda un ENABLE redundante; el patron intermitente
  0x0(primera vez)->0x3(siguientes) es exactamente el de un NFCEE que quedo enabled entre
  corridas porque el `NFCEE_DISCOVER` solo corre en el bring-up del adaptador, no por
  listen. **Fix:** condicionar el `MODE_SET(ENABLE)` al status del `NFCEE_DISCOVER_NTF`
  (o re-discover antes de cada listen); no re-enable si ya esta 0x01. Falta ademas
  capturar/esperar el `NFCEE_MODE_SET_NTF` (62 01), que NO aparece en el log.

- **Por que no activa el listen:** tres causas concurrentes —
  (a) FACT: **falta `RF_DISCOVER_MAP` con entradas LISTEN** en el flujo (nunca se envia
      `21 00` en la ventana de listen); sin MAP el CLF no tiene interfaz con que activar.
  (b) FACT+INFERENCE: el LMRT rutea **NFC-A tech y MIFARE al NFCEE 0x83, que quedo sin
      habilitar** (MODE_SET rechazado) -> ruteo a NFCEE deshabilitado.
  (c) INFERENCE/UNKNOWN: aun con todo lo anterior correcto, el firmware Samsung podria no
      encender el front-end de listen NFC-A sin un **perfil RF vendor** que el HAL carga y
      el path NCI plano no reproduce (muro ya observado con Flipper).

- **Secuencia correcta:** seccion 3 (byte-a-byte): re-discover -> MODE_SET condicional +
  esperar NTF -> **RF_DISCOVER_MAP T2T/FRAME/LISTEN** -> LMRT (NFC-A tech + MIFARE -> 0x83,
  power 0x3b, sin entradas host espurias) -> RF_DISCOVER A+F.

- **Experimento de aislamiento:** anular `ese_nfcee_id` (un cambio de una linea en el NTF
  handler del 0113) para forzar el path HOST del 0111 con LA_*, **incluyendo el
  RF_DISCOVER_MAP de listen y el orden canonico MAP->LMRT->DISCOVER** (que nunca se probo
  bien junto). Si un lector ve el UID -> el RF activa y el bug es solo eSE/ruteo. Si no ve
  nada -> confirmado el muro RF vendor (2c).

### FACT / INFERENCE / UNKNOWN — indice
- FACT: bytes de rawnfcee.log; LMRT = 5 entradas con NFC-A tech y MIFARE a 0x83; ausencia
  de RF_DISCOVER_MAP en el flujo de listen; MODE_SET RSP 0x03; NFCEE 0x83 enumerado con
  status 0x01; el CLF rechaza MIFARE(0x80) en DISCOVER_MAP; A+F es el offering correcto;
  power 0x3b es el del vendor.
- INFERENCE: causa del 0x03 = ya-enabled/estado persistente; ruteo a NFCEE deshabilitado
  contribuye al no-activate; falta de MAP-listen es bloqueante; secuencia canonica
  propuesta.
- UNKNOWN: si llega/no el NFCEE_MODE_SET_NTF (no capturado); si el firmware soporta
  NFCEE_POWER_AND_LINK_CTRL; el set exacto de mappings de listen que exige el firmware; si
  tras corregir MAP+MODE_SET el front-end RF NFC-A finalmente enciende o queda el muro
  vendor (2c).
