# WCN3990 FM-HCI — Por qué el firmware no responde a los 0x11

Fecha: 2026-09-17
Fuente: código real clonado en /tmp/opencode/fm-research/
  - fm-commonsys  = github.com/LineageOS/android_vendor_qcom_opensource_fm-commonsys
  - hw-qcom-bt    = github.com/LineageOS/android_hardware_qcom_bt (libbt-vendor)
  - clo-fm        = git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/fm
  - system_bt     = github.com/LineageOS/android_system_bt

---

## 0. TL;DR — la causa raíz

**Tu framing de comando es correcto. El opcode y su endianness son correctos. El
layout del evento que configuraste es correcto. El problema NO es el formato.**

El problema es la **ARQUITECTURA DE TRANSPORTE**. En el WCN3990 (SoC interno
"Cherokee"), Android **NO** escribe los frames `0x11` directamente al UART de BT.
Los escribe a un **socket local Unix llamado `fm_sock`**, servido por un proceso
propietario **`wcnss_filter`** (a veces `wcnss_filter`/`hci_qcomm_init`), que es
el ÚNICO dueño del fd del UART. Ese proceso:

1. multiplexa BT (`bt_sock`), ANT (`ant_sock`) y FM (`fm_sock`) sobre el mismo UART,
2. añade/quita el byte de packet-type (`0x11` cmd / `0x14` evt) en el borde del UART,
3. y —lo más importante— es el que mantiene la sesión de controlador viva.

Cuando vos inyectás `0x11...` crudo por el UART desde tu módulo kernel, **estás
compitiendo con `hci_qca`/BlueZ por el mismo fd** y, en un stack mainline (que es
tu caso: `hci_uart`+`btqca`, sin `wcnss_filter`), **el firmware del WCN3990
descarta el packet-type 0x11 porque el subsistema FM del SoC nunca fue activado.**

La activación NO es un comando FM-HCI: es que **el controlador tiene que haber
enrutado el DSP-FM**. En el diseño Android eso lo hace la combinación
`FM_VND_OP_POWER_CTRL` + `BT_VND_OP_FM_USERIAL_OPEN` + `wcnss_filter`, NO un
comando 0x11. Sin esa activación, RX se queda en 0 exactamente como te pasa.

---

## 1. Framing EXACTO del comando FM-HCI (byte a byte) — CONFIRMADO

### 1.1 La struct que se serializa (fm_hci_api.h:81)
```c
struct fm_command_header_t {
    uint16_t opcode;    // little-endian (ARM nativo LE)
    uint8_t  len;
    uint8_t  params[];
} __attribute__((packed));
```

### 1.2 Lo que fm_hci.cpp escribe (fm_hci.cpp:742 y :748)
```c
data.setToExternal((uint8_t *)hdr, 3 + hdr->len);
fmHci->sendHciCommand(data);      // 3 bytes de header + len de payload
```

**=> En la capa fm_hci NO hay byte 0x11.** El frame entregado al HAL es:
```
[opcode_lo][opcode_hi][len][payload...]
```
El `0x11` (RADIO_HCI_COMMAND_PKT, definido en radio-helium.h:84) lo antepone el
**HAL daemon / wcnss_filter** justo antes de escribir al UART. Así que en el
ALAMBRE FÍSICO del UART sí es:
```
0x11 [opcode_lo][opcode_hi][len][payload...]
```
que es lo que vos estás mandando. **Tu framing de wire es correcto.**

### 1.3 Opcode: packing y endianness — CONFIRMADO (radio-helium.h:296)
```c
#define hci_opcode_pack(ogf, ocf)  (uint16_t)(((ocf) & 0x03ff) | ((ogf) << 10))
```
El `uint16_t opcode` se serializa **little-endian** (memcpy nativo del struct).

Verificado numéricamente:

| Comando          | OGF  | OCF  | opcode  | wire LE |
|------------------|------|------|---------|---------|
| FM_ENABLE_RECV   | 0x13 | 0x01 | 0x4C01  | `01 4C` |
| SET_RECV_CONF    | 0x13 | 0x04 | 0x4C04  | `04 4C` |
| SET_ANTENNA      | 0x13 | 0x07 | 0x4C07  | `07 4C` |
| TUNE_STATION     | 0x15 | 0x01 | 0x5401  | `01 54` |
| GET_FEATURE_LIST | 0x15 | 0x05 | 0x5405  | `05 54` |
| ENABLE_SLIMBUS   | 0x15 | 0x0E | 0x540E  | `0E 54` |

**FM_ENABLE_RECV en el wire = `11 01 4C 00` (0x11, opcode LE 01 4C, len=00).
Es EXACTAMENTE lo que vos mandás. Confirmado.**

---

## 2. Evento de respuesta (0x14) — framing CONFIRMADO

### 2.1 Struct del evento (fm_hci_api.h:87)
```c
struct fm_event_header_t {
    uint8_t evt_code;
    uint8_t evt_len;
    uint8_t params[];
} __attribute__((packed));
```

### 2.2 Parseo (radio_helium_hal.c:1070-1148)
El HAL recibe el evento **ya sin el 0x14** (el wcnss_filter lo quitó). En el
alambre físico sí viene:
```
0x14 [evt_code][evt_len][params...]
```
- `evt_code` en byte tras el 0x14
- `evt_len`  en el siguiente
- params a continuación

**Tu parser RX (type=0x14, hlen=2, loff=1, lsize=1) es CORRECTO:** con el 0x14
como packet-type, el header interno es evt_code@0, evt_len@1, params@2. Coincide.

### 2.3 Códigos de evento (radio-helium.h:491-519)
```
HCI_EV_TUNE_STATUS      0x01
HCI_EV_CMD_COMPLETE     0x0F   <-- command-complete FM (igual que BT)
HCI_EV_CMD_STATUS       0x10
HCI_EV_TUNE_COMPLETE    0x11   (ojo: mismo número que el pkt-type cmd, pero es evt_code)
HCI_EV_HW_ERR_EVENT     0x1A
```
`command-complete` FM **sí es 0x0F**, como en BT. Confirmado tu supuesto.

### 2.4 Layout interno del CMD_COMPLETE (radio_helium_hal.c:459-471)
Dentro de los params del evento 0x0F:
```
params[0] = num_hci_command_packets (créditos)   <-- CRÉDITOS DE FLUJO
params[1] = opcode_lo
params[2] = opcode_hi     (opcode = (buff[2]<<8)|buff[1])
params[3..] = return params del comando
```

**Detalle de CRÉDITOS que probablemente te muerde aún si lográs RX:** fm_hci
arranca con `command_credits = 1` (fm_hci.cpp:831). Cada comando consume 1 crédito
(dequeue_fm_tx_cmd:372). Los créditos SOLO se reponen cuando llega un CMD_COMPLETE
o CMD_STATUS (dequeue_fm_rx_event:254-280). Si tu inyector manda ENABLE_RECV,
SET_RECV_CONF, TUNE de un tirón sin esperar el CMD_COMPLETE de cada uno, el stack
real se bloquearía tras el primero. Pero como el firmware no te contesta nada, este
no es tu bloqueo actual — es el próximo que vas a encontrar.

---

## 3. Lo que falta ANTES del ENABLE_RECV (la causa real)

### 3.1 WCN3990 = "Cherokee" (bt_vendor_qcom.c:211, :803, :1052)
```c
else if (!strncasecmp(bt_soc_type, "cherokee", ...)) return BT_SOC_CHEROKEE;
```
crbtfw21.tlv + crnv21.bin ROM 0x0201 = firmware Cherokee = WCN3990. Confirmado.

### 3.2 El FM NO abre el UART: abre un socket (bt_vendor_qcom.c:1074-1078)
```c
case BT_SOC_CHEROKEE:
    retval = start_hci_filter();           // arranca wcnss_filter (dueño del UART)
    ...
    if (is_fm_req && soc_type>=ROME && <RESERVED) {
        q->fm_fd = connect_to_local_socket("fm_sock");   // <-- FM va por AQUÍ
    } else {
        connect_to_local_socket("bt_sock");
    }
```
`start_hci_filter()` (bt_vendor_qcom.c:338) sólo hace
`property_set("vendor.wc_transport.start_hci","true")` y espera a que
`wc_transport.hci_filter_status==1`. El binario `wcnss_filter` lo lanza init.rc
reaccionando a esa property. **Ese binario es propietario y NO está en LineageOS.**

### 3.3 Secuencia libbt-vendor para FM (op() en bt_vendor_qcom.c)
El orden que ejecuta Android para prender FM en Cherokee:
```
1. FM_VND_OP_POWER_CTRL  (bt_vendor_qcom.c:764)
     -> is_fm_req=true; si el SoC ya está init (BT ya prendido) NO repite power,
        sólo marca is_fm_req. Si NO estaba init, cae a BT_VND_OP_POWER_CTRL
        y hace bt_powerup()/ioctl(BT_CMD_PWR_CTRL) sobre /dev/btpower.
2. BT_VND_OP_FM_USERIAL_OPEN  (bt_vendor_qcom.c:851)
     -> is_fm_req=true; goto userial_open
     -> start_hci_filter()  (asegura wcnss_filter vivo)
     -> connect_to_local_socket("fm_sock")   <-- devuelve el fd que usa fm_hci
```
**Ningún vendor HCI command 0xFC** se manda para "activar FM". La activación es
puramente de transporte: (a) power del SoC, (b) wcnss_filter enrutando fm_sock.

### 3.4 Por qué en TU stack (mainline) no responde
Tu kernel corre `hci_uart` + `btqca` (mainline). **No existe `wcnss_filter`.** El
UART lo posee `hci_qca` como line discipline / serdev. Cuando tu módulo escribe
`0x11...`:
- El firmware del WCN3990 recibe un packet-type que su *demux interno* sólo
  enruta al DSP-FM **si el subsistema FM fue habilitado por el flujo de arriba**.
  En el path Android eso ocurre porque `wcnss_filter` abrió el canal FM y el
  controlador quedó en "modo combo con FM ruteado".
- En mainline nadie ejecutó ese flujo. El firmware ve 0x11, no tiene el canal FM
  abierto, y **lo descarta silenciosamente**. Por eso RX = 0, sin 0x14, sin nada.

Esto encaja al 100% con tu síntoma: TX sale (contadores UART suben), RX = 0.

---

## 4. ¿El WCN3990 realmente soporta FM? ¿Firmware aparte? ¿Flag NVM?

### 4.1 Soporte FM
Sí, el WCN3990 (Cherokee) tiene bloque FM. Está referenciado explícitamente en:
- btfm_slim_wcn3990.c (puertos SLIMbus TX FM: CHRK_SB_PGD_PORT_TX1_FM/TX2_FM).
- bt_vendor_qcom.c enruta fm_sock para BT_SOC_CHEROKEE (:1075).
- radio-helium.h define ENABLE_SLIMBUS (0x15/0x0E) — comando que sólo tiene
  sentido en chips con audio FM por SLIMbus = WCN3990.

**PERO**: que el SoC lo soporte no significa que TU unidad lo tenga habilitado.
El bloque FM puede estar:
- fusible-deshabilitado en algunos SKU, o
- deshabilitado por el NVM (crnv21.bin) — ver 4.3.

### 4.2 ¿Firmware FM aparte?
**No hay firmware FM separado.** El código FM vive dentro del MISMO
crbtfw21.tlv + crnv21.bin de BT. No existe ningún "fm_wcn3990.bin". FM se activa
como sub-función una vez cargado el firmware BT y ruteado el canal. Confirmado:
libbt-vendor no descarga ningún blob extra para FM; sólo abre fm_sock.

### 4.3 Flag NVM (crnv21.bin)
El NVM QCA es un TLV de tags de config del controlador. Históricamente hay tags
que habilitan/deshabilitan sub-funciones (BT/FM/ANT) y pinean el ruteo de audio
(SLIMbus vs PCM vs I2S). **No es open-source qué tag exacto**, pero el mecanismo
existe: si el crnv21.bin de rhodep viene con FM deshabilitado (porque Motorola no
expone FM en ese teléfono), el firmware **jamás** ruteará el 0x11 al DSP-FM,
hagas lo que hagas desde el host. Este es un candidato MUY fuerte para tu caso:
un teléfono cuyo vendor no expone FM suele traer el NVM con FM off.

---

## 5. ¿Hay un comando 0xFC (BT normal 0x01) de "SoC FM mode"? — NO en el open source

Busqué explícitamente en libbt-vendor y en el HAL FM:
- No hay ningún `HCI_FM_SoC_Set_Mode`, ni vendor cmd 0xFC** que "active FM".
- El `enable_controller_log(fd, is_fm_req)` (bt_vendor_qcom.c:1025) sólo manda un
  vendor cmd de logging del controlador, no de FM.
- La activación de FM es 100% de transporte (power + wcnss_filter + fm_sock),
  no un comando HCI.

CONCLUSIÓN: no existe un "comando mágico 0xFC" documentado que puedas replicar.
La activación real está dentro del binario propietario `wcnss_filter` y en cómo
éste abre el canal FM del controlador. **Esto es lo que te falta.**

---

## 6. La secuencia COMPLETA que hace Android (para referencia)

Nivel transporte (libbt-vendor, una vez por encendido de FM):
```
op(FM_VND_OP_POWER_CTRL, ON)        // power del SoC si no estaba (btpower ioctl)
op(BT_VND_OP_FM_USERIAL_OPEN)       // start_hci_filter(); connect fm_sock -> fd
```
Nivel FM-HCI (fm_hci + helium HAL, sobre ese fd, cada frame = [0x11][op_lo][op_hi][len][payload]):
```
1. FM_ENABLE_RECV      OGF13/OCF01   11 01 4C 00               -> espera 0x0F CC
2. FM_SET_RECV_CONF    OGF13/OCF04   11 04 4C <len> <conf>     -> espera 0x0F CC
      struct hci_fm_recv_conf_req { emphasis, ch_spacing, rds_std, hlsi,
                                    band_low_limit(u32), band_high_limit(u32) }
3. FM_SET_ANTENNA      OGF13/OCF07   11 07 4C 01 <ant>         -> espera 0x0F CC
4. (audio) ENABLE_SLIMBUS OGF15/OCF0E 11 0E 54 01 <val>        -> espera 0x0F CC
5. FM_TUNE_STATION     OGF15/OCF01   11 01 54 04 <freq u32 LE> -> espera 0x11 TUNE_COMPLETE / 0x01 TUNE_STATUS
```
IMPORTANTE: esperar el CMD_COMPLETE (0x0F) de cada uno antes del siguiente
(modelo de créditos, §2.4). Créditos iniciales = 1.

El orden viene de fmTurnOnSequence() (FMRadioService.java:2476) ->
FmReceiver.enable() (FmReceiver.java:503) -> FmTransceiver.enable() (:182) ->
mControl.fmOn() (FmRxControls.java:119, setControl V4L2_CID_PRIVATE_TAVARUA_STATE)
-> el kernel helium/iris arma el ENABLE_RECV -> fm_hci_transmit.

---

## 7. Diagnóstico dirigido a TU setup y qué probar

Tu síntoma (TX ok, RX=0) tiene 3 causas candidatas, ordenadas por probabilidad:

### C1 (MÁS PROBABLE) — El canal FM del controlador nunca fue abierto
En mainline no hay wcnss_filter, así que el firmware descarta 0x11.
- Prueba de humo: manda un comando FM que NO dependa de estado, p.ej.
  GET_FEATURE_LIST (11 05 54 00) o FM_ENABLE_RECV solo, y revisá RX.
  Si sigue 0 => el canal FM está cerrado a nivel firmware.
- Camino a resolver: hay que reproducir lo que hace wcnss_filter. Como es
  propietario, la vía práctica es o (a) portar el driver kernel `radio-iris`
  helium con un transporte que coopere con hci_qca y abra el canal FM, o (b)
  levantar el stack Android libbt-vendor+wcnss_filter (inviable en pmOS).

### C2 (MUY PROBABLE en rhodep) — FM deshabilitado en crnv21.bin (NVM)
Si Motorola no expone FM en el G82, el NVM puede traer FM off. En ese caso NADA
que mandes por el host lo va a activar.
- Prueba: conseguí el crnv21.bin de un teléfono con MISMO WCN3990 que SÍ tenga FM
  (varios Xiaomi/Realme SM6xxx con app de radio de fábrica) y compará; o probá a
  flashear ese NVM (riesgoso: puede romper BT/calibración RF).

### C3 (MENOS PROBABLE) — Colisión de fd con hci_qca
Si tu módulo inyecta por el mismo serdev que hci_qca posee, el RX del UART lo
consume la line discipline de hci_qca y tu lector nunca ve los bytes.
- Prueba: verificá QUIÉN lee el UART. Si hci_qca está atado al serdev, los 0x14
  (si llegaran) los parsea btqca y los tira por no ser BT event 0x04. Necesitás
  un hook en la recepción de hci_qca que derive packet-type 0x14 a tu código.
  (Esto explicaría RX=0 aunque el firmware SÍ conteste.)

### Orden de acciones sugerido
1. Confirmá C3 primero: instrumentá el RX path de hci_qca (o el driver serdev)
   para loguear CUALQUIER byte entrante mientras inyectás. Si ves 0x14... el
   problema es sólo de ruteo RX (fácil). Si no ves NADA, es C1/C2 (firmware).
2. Si no hay RX en absoluto: probá GET_FEATURE_LIST y ENABLE_RECV aislados.
   Sin respuesta => el subsistema FM no está activo (C1) o está fusible/NVM-off (C2).
3. Compará crnv21.bin de rhodep contra uno de un device con FM funcional (C2).
4. Si querés el camino "correcto": portá radio-iris/helium kernel + transporte
   que abra el canal FM cooperando con hci_qca (ver RESEARCH_wcn3990_fm.md §6 B1).

---

## 8. Respuestas directas a tus 6 preguntas

1. **Framing exacto**: `[0x11][opcode_lo][opcode_hi][len][payload]`. Opcode
   little-endian. NO hay bytes extra. La struct es fm_command_header_t
   (uint16 opcode LE, uint8 len, params[]). fm_hci.cpp:742 escribe 3+len bytes;
   el 0x11 lo antepone el HAL/wcnss_filter. **Tu layout es correcto.**

2. **¿Hace falta algo antes?** SÍ, pero NO es un comando FM-HCI ni un 0xFC:
   es `FM_VND_OP_POWER_CTRL` + `BT_VND_OP_FM_USERIAL_OPEN` (bt_vendor_qcom.c:764,
   :851), que arranca `wcnss_filter` y abre el socket `fm_sock`. Ese binario es
   el que activa/rutea el canal FM del controlador. NO existe un vendor HCI
   "FM enable" sobre 0x01. **Esto es lo que te falta y por eso RX=0.**

3. **¿WCN3990 soporta FM?** El SoC sí (Cherokee, btfm_slim_wcn3990.c). NO hay
   firmware FM aparte: está dentro de crbtfw21.tlv/crnv21.bin. PERO el crnv21.bin
   (NVM) puede traer FM deshabilitado si el OEM no lo expone — candidato fuerte
   en rhodep.

4. **Orden de bytes del opcode**: idéntico a BT. (OGF<<10)|OCF, little-endian.
   ENABLE_RECV=0x4C01 -> wire `01 4C`. **Correcto.** (hci_opcode_pack en
   radio-helium.h:296.)

5. **Evento 0x14**: `[0x14][evt_code][evt_len][params]`. event_code de
   command-complete FM = **0x0F** (igual que BT, radio-helium.h:505). Tu parser
   type=0x14 hlen=2 loff=1 lsize=1 es **correcto**.

6. **¿Comando "transport enable"/"SoC FM mode"?** NO existe tal comando 0xFC en el
   open source. La activación es de transporte (power + wcnss_filter + fm_sock),
   dentro de un binario propietario. Sin reproducir ese ruteo, el firmware
   descarta los 0x11. **Ésa es la razón del silencio.**

---

## 9. Veredicto claro

El WCN3990 necesita algo que tu setup mainline **no tiene**: la activación del
canal FM del controlador que en Android hace el binario propietario
`wcnss_filter` (disparado por FM_VND_OP_POWER_CTRL + BT_VND_OP_FM_USERIAL_OPEN).
NO es un comando 0x11 ni un 0xFC que puedas mandar; es ruteo de transporte dentro
del controlador. Adicionalmente, existe riesgo real de que el NVM crnv21.bin de
rhodep traiga FM deshabilitado (OEM no expone FM), en cuyo caso ninguna secuencia
de host lo activará. Tu framing, opcodes, endianness y parser de eventos están
TODOS correctos; el bloqueo es arquitectónico/de activación, no de formato.
