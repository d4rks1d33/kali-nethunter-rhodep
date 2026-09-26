# 0113 Hunk #5 fix — resumen

## Que fallaba (FACT)
`patch -p1 --dry-run` de `0113-nfc-route-mifare-listen-to-ese.patch` sobre el arbol real
(`core.c.with0112`, 1835 lineas) daba:

```
Hunk #1 succeeded at 247 (offset 3 lines).
Hunk #3 succeeded at 928 (offset 34 lines).
Hunk #4 succeeded at 955 (offset 34 lines).
Hunk #5 FAILED at 1019.
1 out of 5 hunks FAILED
```

## Causa raiz (FACT)
El Hunk #5 original (`@@ -990,25 +999,69 @@`) fue escrito contra una base que NO coincide
con lo que 0111 realmente dejo en `nci_start_poll()`:

- 0111 ya reescribio el comentario `EXP-3` a `Canonical NCI listen order ...` **y ya
  agrego la llamada a `nci_rf_discover_map_listen_req`** dentro de `nci_start_poll` (arbol
  real lineas 1043-1051). El Hunk #5 original asumia que ese bloque NO existia todavia y
  que el comentario seguia siendo `EXP-3`, por eso su contexto no matcheaba.
- Ademas 0111 dejo la llamada a `LA_NFCID1` partida en dos lineas de forma distinta a la
  que el Hunk #5 esperaba borrar.

Resultado: las lineas de contexto/eliminadas del Hunk #5 no existian textualmente en
`core.c.with0112` en esa posicion -> FAILED.

## Que se hizo (FACT)
Se regenero SOLO el Hunk #5 de la seccion `net/nfc/nci/core.c` contra el arbol real. Los
otros 4 hunks de core.c y las secciones de nci.h / nci_core.h / ntf.c quedaron intactas
(ya aplicaban). El nuevo Hunk #5 es un unico hunk contiguo:

```
@@ -1024,34 +1024,72 @@
```

Conteo verificado contra `core.c.with0112`:
- contexto (' ') = 23, eliminadas ('-') = 11, agregadas ('+') = 49
- old = ctx + del = 23 + 11 = **34**  == Y del header (34) OK
- new = ctx + add = 23 + 49 = **72**  == B del header (72) OK
- start -X = 1024: la primera linea de contexto
  ` 		 * here corrupts the high ATQA byte). */` existe textualmente en la linea 1024
  de `core.c.with0112` OK

Conteo de los 5 hunks de core.c (todos consistentes):
```
@@ -244,6  +244,26  @@  old=6/6   new=26/26
@@ -301,12 +301,13  @@  old=12/12 new=13/13
@@ -873,12 +874,18  @@  old=12/12 new=18/18
@@ -894,14 +901,16  @@  old=14/14 new=16/16
@@ -1024,34 +1024,72 @@ old=34/34 new=72/72   <- regenerado
```

## Que hace el fix (FACT sobre el codigo resultante)
Aprovecha el `nci_rf_discover_map_listen_req` que 0111 ya llama y solo reordena/inserta
alrededor. Orden final en `nci_start_poll` (path emulacion `nfc_dev->emu_nfcid1_len`):

1. SET_CONFIG LA_* (LA_BIT_FRAME_SDD/PLATFORM_CONFIG/NFCID1/SEL_INFO) — **solo host**,
   envuelto en `if (!nci_emu_ese_id(ndev))` (el eSE es dueno de la identidad NFC-A).
2. SET_CONFIG TOTAL_DURATION (1000 ms) — incondicional.
3. SET_CONFIG LF_PROTOCOL_TYPE=0 — **solo eSE** (`if (nci_emu_ese_id(ndev))`), mata el peer
   NFC-DEP sobre NFC-F. NO se envia LF_T3T_FLAGS=0 (comentado el porque: el S3FWRN5 lo
   rechaza con status 0x9 sin LF_T3T_IDENTIFIERS).
4. RF_DISCOVER_MAP(listen) — `nci_rf_discover_map_listen_req` (T2T 0x02 / LISTEN 0x02 /
   FRAME 0x01), ya provisto por 0111, se conserva en el orden correcto.
5. NFCEE_MODE_SET condicional — **solo eSE y solo si no esta ya enabled**:
   `if (nci_emu_ese_id(ndev) && ndev->ese_enabled != NCI_NFCEE_ENABLE)
        nci_nfcee_mode_set(ndev, nci_emu_ese_id(ndev), NCI_NFCEE_ENABLE);`
   evita el `MODE_SET(ENABLE)` redundante que el firmware contesta 0x03 (REJECTED).
6. LMRT — `nci_rf_set_listen_mode_routing_req` (sin cambios aca; el ruteo NFC-A tech +
   MIFARE al eSE lo hacen los hunks #3/#4 via `nci_emu_ese_id(ndev) ? : NCI_NFCEE_ID_HOST`).
7. RF_DISCOVER — luego del bloque (`nci_rf_discover_req`).

Orden pedido: **SET_CONFIG (LA_*) -> RF_DISCOVER_MAP(listen) -> [NFCEE cond] -> LMRT ->
RF_DISCOVER**. Cumplido.

El module param `emulate_host` y el helper `static inline u8 nci_emu_ese_id()` los agrega
el Hunk #1 de core.c (ya aplicaba OK, no se toco). El resto del path eSE (T3T flag define,
ruteo eSE, ntf.c, nci_core.h fields) tambien intacto.

## Simbolos usados por el Hunk #5 — todos existen en el arbol (FACT)
- `nci_emu_ese_id()`  -> definido por Hunk #1 de core.c (0113)
- `NCI_LF_PROTOCOL_TYPE` = 0x50 -> nci.h (mainline)
- `NCI_NFCEE_ENABLE` = 0x01 -> nci.h (0112)
- `nci_nfcee_mode_set()` -> core.c:753 (0112, EXPORT_SYMBOL)
- `ndev->ese_enabled` -> nci_core.h (Hunk #2 de nci_core.h, 0113)
- `nci_rf_discover_map_listen_req` / `nci_rf_set_listen_mode_routing_req` -> core.c (0111)

## Validacion patch --dry-run (FACT)
Se reconstruyo el arbol base exacto que espera 0113:
- `core.c` = `core.c.with0112` (tal cual).
- `nci.h`, `ntf.c` = extraidos de linux-motorola-rhodep-7.2_rc5.tar.gz + solo los hunks de
  0111 que tocan esos dos archivos (los unicos patches previos que los tocan son 0111;
  0112 no los toca). Aplicaron limpio.
- `nci_core.h` = pristino (ningun patch previo a 0113 lo toca).

`patch -p1 --dry-run < 0113...patch` sobre ese arbol:
```
checking file include/net/nfc/nci.h
checking file include/net/nfc/nci_core.h
checking file net/nfc/nci/ntf.c
checking file net/nfc/nci/core.c
Hunk #1 succeeded at 247 (offset 3 lines).
Hunk #3 succeeded at 928 (offset 34 lines).
Hunk #4 succeeded at 955 (offset 34 lines).
```
TODOS los hunks succeeded, sin FAILED y sin fuzz. (Hunk #2 y #5 de core.c y todos los de
los otros 3 archivos matchean exacto -> patch no imprime linea para ellos.)

Aplicacion REAL (no dry-run): exit 0, sin `.rej`. El `nci_start_poll` resultante quedo
coherente y con el orden canonico descrito arriba.

## Que quedo
- **`/tmp/nfcre3/0113-nfc-route-mifare-listen-to-ese.patch`** = 0113 corregido (sobrescrito).
- Repo git (`/opt/postmarket/nethunter-rhodep-repo/kernel/patches/0113-...patch`) **NO tocado**
  (sigue con la version rota; hay que copiarle la de /tmp cuando se decida). pmbootstrap NO
  tocado. La `.tgz` solo se leyo (extraccion de 3 archivos a /tmp), no se modifico.
- Arbol de validacion en `/tmp/nfcre3/fulltree/` (base con 0112, para re-verificar).

## FACT / INFERENCE / UNKNOWN
- FACT: el failure era Hunk #5 por contexto desactualizado (0111 ya metio
  RF_DISCOVER_MAP-listen y reescribio el comentario EXP-3 en start_poll).
- FACT: el Hunk #5 regenerado (`@@ -1024,34 +1024,72 @@`) tiene conteo exacto y aplica
  limpio; el patch completo pasa dry-run y apply real sin rechazos.
- FACT: todos los simbolos referenciados existen en el arbol resultante.
- INFERENCE: el comportamiento RF (que el CLF finalmente active el listen NFC-A y el eSE
  responda al lector) NO esta probado por esto; el fix solo corrige el flujo NCI y la
  aplicabilidad del patch. El posible "muro RF vendor" (seccion 2c de nfcee_activation.md)
  sigue siendo INFERENCE/UNKNOWN.
- UNKNOWN: no se compilo el kernel (no se corrio build); la validez sintactica se infiere
  de que los simbolos existen, pero no hay confirmacion del compilador. No se capturo el
  NFCEE_MODE_SET_NTF real, asi que el efecto del MODE_SET condicional en hardware es UNKNOWN.
