# wcnss_filter y la activación FM del WCN3990 — qué envía realmente el blob

Fecha: 2026-09-17
Objetivo: determinar la secuencia EXACTA de bytes/comandos que el binario
propietario `wcnss_filter` (a.k.a. `wcnss_service` / capa de `libbt-vendor`)
envía para ACTIVAR el canal FM del WCN3990, para replicarla en mainline sin blob.

Fuentes clonadas y verificadas en /tmp/opencode/fm-research/:
- hw-qcom-bt   = github.com/LineageOS/android_hardware_qcom_bt @ lineage-23.2-caf
- clo-bt       = git.codelinaro.org/clo/la/platform/hardware/qcom/bt (CAF upstream)
- fm-commonsys = github.com/LineageOS/android_vendor_qcom_opensource_fm-commonsys @ lineage-23.2
- clo-fm       = git.codelinaro.org/clo/la/platform/vendor/qcom-opensource/fm
- hci_qca_moto = LineageOS/android_kernel_motorola_sm6375 @ lineage-22.1 (¡tu familia SoC!)
- hci_qca_mainline / btqca_mainline = torvalds/linux @ v6.12

---

## 0. TL;DR — la respuesta corta y dura

1. **El código fuente de `wcnss_filter` NO es público.** Ni LineageOS
   (`android_hardware_qcom_bt`) ni CodeLinaro (`platform/hardware/qcom/bt`)
   contienen un subdirectorio `wcnss_filter`/`filter`. Ambos árboles sólo traen
   `libbt-vendor`. Verificado: `find` sobre los dos repos = 0 archivos de filtro.
   El binario vive en `/vendor/bin/wcnss_filter` en el sistema Android, cerrado.

2. **Pero la parte de `libbt-vendor` que dispara FM SÍ es pública, y la leí byte
   a byte: NO manda ningún comando HCI de "activar FM".** Para el SoC CHEROKEE
   (=WCN3990), el flujo FM es literalmente:
   ```
   start_hci_filter()   -> property_set("...wc_transport.start_hci","true")  // lanza el blob
   connect_to_local_socket("fm_sock")                                        // abre socket
   ```
   Cero bytes al UART, cero opcode 0xFC**, cero "USER_OP_FM_ON". Ver
   bt_vendor_qcom.c:1052-1110. **No hay una "secuencia mágica" en el open source.**

3. **`fm_hci.cpp` (el HAL FM) tampoco antepone nada:** manda exactamente
   `[opcode_lo][opcode_hi][len][payload]` (3+len bytes) al servicio HIDL/AIDL
   `vendor.qti.hardware.fm.IFmHci`. El byte `0x11` y TODO el ruteo/activación
   ocurren DENTRO de ese HAL propietario + `wcnss_filter`. Ver fm_hci.cpp:736-756.

4. **HALLAZGO NUEVO Y DECISIVO (corrige la hipótesis previa):** el kernel
   downstream de TU familia (Motorola sm6375) **tampoco tiene driver de FM del
   WCN3990**. Su `drivers/media/radio/` NO trae `radio-iris` ni `radio-helium`
   (sólo tuners legacy + `rtc6226`, que es OTRO chip). Y su `hci_qca.c` tiene la
   MISMA tabla `qca_recv_pkts[]` que mainline: sólo ACL/SCO/EVENT + IBS, **sin
   0x14**. Es decir: el FM del WCN3990 NO pasa por el kernel ni en downstream.
   Todo el plano de control FM va por el **daemon userspace `wcnss_filter`** que
   posee el tty crudo. El kernel `hci_qca` ni se entera del FM.

5. **Conclusión de viabilidad:** la "activación" NO es una secuencia de bytes que
   te falte enviar; es que en Android el dueño del UART es `wcnss_filter`, y ese
   proceso es el que (a) abrió la sesión de controlador, (b) mantiene el ruteo, y
   probablemente (c) hace un handshake propietario con el firmware al abrir
   `fm_sock`. Ese handshake NO está en ningún fuente abierto. Nadie lo portó a
   mainline (ni en pmOS, ni en sdm845-mainline). Replicarlo requiere o bien
   ejecutar el blob, o hacer ingeniería inversa dinámica (captura de UART con el
   stock booteado) porque estáticamente el open source no lo revela.

---

## 1. Pregunta 1 — Arquitectura de wcnss_filter (qué hace, dónde está)

### 1.1 El fuente NO existe en open source (verificado)
- `hw-qcom-bt` (LineageOS lineage-23.2-caf): sólo `libbt-vendor/`. Sin filtro.
- `clo-bt` (CAF `platform/hardware/qcom/bt`, HEAD 4d4e152): dirs = `libbt-vendor`,
  `msm8909`, `msm8960`, `msm8992`, `msm8996`, `msm8998`, … **ninguno `filter`**.
- `grep -rl "hci_filter|wcnss_filter|fm_sock"` en clo-bt → sólo aparece dentro de
  los distintos `bt_vendor_qcom.c` (el *cliente* que lanza el filtro), nunca el
  servidor. **El daemon es blob.**

Por tanto todo lo que sabemos de su comportamiento es indirecto (por su cliente
`libbt-vendor` y por el HAL FM). Lo que hace, según ese contrato:
- Init: reacciona a la property `vendor.wc_transport.start_hci=true` (lanzado por
  init.rc), abre el tty del WCN3990 (p.ej. `/dev/ttyHS0`), hace power/handshake,
  descarga/valida firmware si hace falta, y levanta 3 sockets Unix locales:
  `bt_sock`, `ant_sock`, `fm_sock`.
- Runtime: multiplexa esos 3 sockets sobre el único UART, anteponiendo/quitando
  el byte de packet-type en el borde del UART (`0x11`/`0x14` para FM).
- Es el ÚNICO dueño del fd del UART. En el modelo Android, `hci_qca` del kernel
  **no se usa para BT tampoco**: el BT también sale por `bt_sock`→wcnss_filter.
  (Por eso el downstream `hci_qca.c` puede no manejar FM: en Android el ldisc
  kernel apenas participa; el transporte real es userspace.)

### 1.2 Dónde encaja respecto a fm_userial_open / fm_power
- `libbt-vendor` expone al stack FM dos ops: `FM_VND_OP_POWER_CTRL` y
  `BT_VND_OP_FM_USERIAL_OPEN`. Ambas son **el cliente** que le pide al blob que
  haga su trabajo; no contienen la lógica de activación (ver §3).

---

## 2. Pregunta 2 — La secuencia de activación FM concreta (bytes)

**No existe en el open source ningún "enable FM transport" / "set FM mode" en
bytes.** Lo verifiqué en las 3 capas:

### 2.1 Capa libbt-vendor (bt_vendor_qcom.c) — CHEROKEE/WCN3990
`bt_vendor_qcom.c:1052-1110` (caso `BT_SOC_CHEROKEE` dentro de `userial_open`):
```c
case BT_SOC_CHEROKEE:
    property_get("ro.vendor.bluetooth.emb_wp_mode", emb_wp_mode, "false");
    retval = start_hci_filter();                 // (A) lanza/espera wcnss_filter
    if (retval < 0) { ...error... } else {
#ifdef ENABLE_ANT
        if (is_ant_req) q->ant_fd = connect_to_local_socket("ant_sock");
        else
#endif
#ifdef FM_OVER_UART
        if (is_fm_req && soc>=ROME && soc<RESERVED)
            q->fm_fd = fd_filter = connect_to_local_socket("fm_sock");  // (B) abre FM
        else
#endif
            vnd_userial.fd = fd_filter = connect_to_local_socket("bt_sock");
        ...
    }
```
**Sólo (A) + (B). Ni un byte al UART.** Nótese además que en la rama CHEROKEE
NO se llama a `enable_controller_log()` (eso es sólo la rama ROME, línea 1025).
Así que para WCN3990 libbt-vendor manda LITERALMENTE 0 comandos HCI para FM.

`start_hci_filter()` (bt_vendor_qcom.c:338-373) confirma que sólo maneja
properties y espera `wc_transport.hci_filter_status==1`; no toca el UART.

### 2.2 Capa fm_hci (fm_hci.cpp) — tampoco antepone nada
`fm_hci.cpp:736-756` `hci_transmit()`:
```c
data.setToExternal((uint8_t *)hdr, 3 + hdr->len);   // opcode(2) + len(1) + payload
fmHci->sendHciCommand(data);                          // -> HAL IFmHci (blob)
```
El frame que sale de fm_hci hacia el HAL es `[op_lo][op_hi][len][payload]`, SIN
`0x11`. El `0x11` lo pone el HAL/`wcnss_filter` en el borde del UART. En árboles
nuevos (lineage-23.2) el transporte es un **servicio HIDL/AIDL
`vendor.qti.hardware.fm.IFmHci`** (fm_hci.cpp:53-84,158) — otra vez, blob.

### 2.3 Capa helium (radio_helium_hal_cmds.c) — comandos FM "normales"
`send_fm_cmd_pkt(opcode,len,param)` (línea 43) arma el frame de cada comando FM.
El primer comando de la secuencia de encendido RX es:
```c
int hci_fm_enable_recv_req()  // línea 77
  opcode = hci_recv_ctrl_cmd_op_pack(HCI_OCF_FM_ENABLE_RECV_REQ);  // OGF13/OCF01
  return send_fm_cmd_pkt(opcode, 0, NULL);
```
Es EXACTAMENTE tu `11 01 4C 00`. No hay ningún comando previo de "abrir
transporte" a este nivel — se asume que el transporte (wcnss_filter) YA abrió el
canal FM cuando `libfm` hizo `FM_VND_OP_POWER_CTRL`+`BT_VND_OP_FM_USERIAL_OPEN`.

### 2.4 ¿Y ENABLE_SLIMBUS? (0x15/0x0E) — es audio, no activación de control
`radio_helium_hal.c:445-450` `hci_cc_enable_slimbus_rsp` y
`android_hardware_fm.cpp:894` `enableSlimbusNative` → `V4L2_CID_PRV_ENABLE_SLIMBUS`
(0x00980940). Este comando existe y es específico de WCN3990, PERO enciende el
**plano de audio SLIMbus**, no abre el canal de control FM. Se manda DESPUÉS del
ENABLE_RECV, no antes, y no es lo que te falta para recibir el 0x14.

### 2.5 Veredicto pregunta 2
La "secuencia de activación FM" que buscás **no está en bytes en el open source**.
En Android la activación es: `property start_hci` → el blob `wcnss_filter` abre el
tty, hace su handshake propietario con el firmware, y crea `fm_sock`. El único
"comando" observable open-source es el propio `FM_ENABLE_RECV` (11 01 4C 00), que
vos ya mandás y que el firmware ignora porque el canal FM del controlador nunca
fue abierto por el blob.

---

## 3. Pregunta 3 — bt_vendor_qcom.c: FM_VND_OP_POWER_CTRL y userial_open

### 3.1 FM_VND_OP_POWER_CTRL (bt_vendor_qcom.c:764-772)
```c
#ifdef FM_OVER_UART
case FM_VND_OP_POWER_CTRL:
    is_fm_req = true;
    if (is_soc_initialized()) {
        // add any FM specific actions if needed in future   <-- ¡VACÍO!
        break;                     // si BT ya está prendido, NO hace nada más
    }
#endif
    // si el SoC NO estaba init, cae (fall-through) a BT_VND_OP_POWER_CTRL:
case BT_VND_OP_POWER_CTRL:
    ...
    case BT_SOC_CHEROKEE:
        retval = bt_powerup(nState);   // power del SoC vía /dev/btpower (ioctl)
```
Operaciones de bajo nivel reales de FM_VND_OP_POWER_CTRL en CHEROKEE:
- Si el WCN3990 ya está inicializado (BT ya levantado): **NO hace NADA** (el
  comentario "add any FM specific actions if needed in future" está vacío).
- Si no lo estaba: hace `bt_powerup()` → `ioctl(BT_CMD_PWR_CTRL)` sobre el
  device node de `btpower` (kernel `btpower.c`). Eso es GPIO/regulador de power
  del chip, **no** un byte de FM.

### 3.2 BT_VND_OP_FM_USERIAL_OPEN (bt_vendor_qcom.c:851-854)
```c
#ifdef FM_OVER_UART
case BT_VND_OP_FM_USERIAL_OPEN:
    is_fm_req = true;
    goto userial_open;         // reusa el open genérico -> caso CHEROKEE (§2.1)
#endif
```
Es decir: marca `is_fm_req` y salta a `userial_open`, donde el caso CHEROKEE hace
`start_hci_filter()` + `connect_to_local_socket("fm_sock")` (ya visto en §2.1).

### 3.3 Flujo completo desde FM_VND_OP_POWER_CTRL hasta "listo para FM-HCI"
```
1. op(FM_VND_OP_POWER_CTRL, ON)
     - is_fm_req=true
     - si SoC no init -> bt_powerup() -> ioctl(/dev/btpower, BT_CMD_PWR_CTRL, ON)
     - si SoC ya init -> nada
2. op(BT_VND_OP_FM_USERIAL_OPEN)
     - is_fm_req=true ; goto userial_open ; caso CHEROKEE:
     - start_hci_filter()  -> property_set(start_hci=true) ; espera filter_status==1
          => arranca /vendor/bin/wcnss_filter (BLOB) que abre el tty y el firmware
     - connect_to_local_socket("fm_sock") -> fd
3. (desde aquí) libfm-hci/helium hacen write(fd, [0x11][op][len][payload]) y
     read(fd, [0x14]...). El 0x11/0x14 lo pone/quita wcnss_filter en el UART.
```
**Todo el "quedar listo para FM-HCI" depende del paso 2 dentro del blob.** Las
únicas operaciones de bajo nivel del lado abierto son: un `ioctl` de power a
`/dev/btpower`, un `property_set`, y un `connect()` a un socket Unix. **Ningún
ioctl ni write al UART para FM.**

---

## 4. Pregunta 4 — driver kernel (btfm_slim, hci_qca downstream vs mainline)

### 4.1 btfm_slim = AUDIO, no control
`drivers/bluetooth/btfm_slim*.c` (presente en downstream Motorola) maneja los
puertos SLIMbus TX de FM (audio). No abre ni activa el canal de control FM. No
sirve para que el firmware conteste el 0x14.

### 4.2 HALLAZGO CLAVE: el hci_qca downstream NO tiene FM (igual que mainline)
Comparación directa `qca_recv_pkts[]`:

Downstream Motorola sm6375 (hci_qca_moto.c:927-932):
```c
static const struct h4_recv_pkt qca_recv_pkts[] = {
    { H4_RECV_ACL,             .recv = qca_recv_acl_data },
    { H4_RECV_SCO,             .recv = hci_recv_frame    },
    { H4_RECV_EVENT,           .recv = qca_recv_event    },
    { QCA_IBS_WAKE_IND_EVENT,  .recv = qca_ibs_wake_ind  },
    { QCA_IBS_WAKE_ACK_EVENT,  .recv = qca_ibs_wake_ack  },
    { QCA_IBS_SLEEP_IND_EVENT, .recv = qca_ibs_sleep_ind },
};
```
Mainline v6.12 (hci_qca_mainline.c:1250-1253): idéntico (ACL/SCO/EVENT; el IBS
está por debajo). **En AMBOS: no hay entrada para 0x14 (FM event) ni 0x11.**
`grep -i fm` en el hci_qca downstream = 0 resultados.

Conclusión: el downstream NO tiene "código FM/0x11 que el mainline no tenga" en
`hci_qca`. La diferencia downstream↔mainline NO está en el kernel BT. La FM del
WCN3990 nunca fue código de kernel BT; siempre fue el daemon userspace.

### 4.3 Y el downstream tampoco trae radio-iris/helium para WCN3990
`drivers/media/radio/Makefile` del kernel Motorola sm6375:
- Sólo tuners legacy (aztech, tea575x, si470x, si4713, wl1273/wl128x…) +
  `CONFIG_I2C_RTC6226_QCA` → `rtc6226/`.
- **NO hay `radio-iris.o`, NO hay `radio-helium.o`.**
El `rtc6226` es un **tuner FM I²C discreto de Richwave** que Qualcomm ofrece como
alternativa; NO es el FM interno del WCN3990. (Si tu G82 usara rtc6226 sería otro
mundo: un chip aparte en I²C. No es tu caso: vos hablás por UART al WCN3990.)

Esto corrige la suposición del research previo (que asumía un `radio-iris` de
kernel). En los árboles modernos (SDM6xx/bengal/khaje), el control FM del
WCN3990 migró 100% a userspace (HAL `IFmHci` + `wcnss_filter`); el V4L2
`radio-iris`/`radio-helium` de kernel es de la era antigua (riva/pronto por SMD)
y NO se usa para WCN3990 en estos SoC.

### 4.4 btpower.c
El único kernel driver relevante que el flujo FM toca es `btpower.c`
(`MSM_BT_POWER`), vía `ioctl(BT_CMD_PWR_CTRL)`. Es power/reset del chip, común a
BT y FM. No abre canal FM.

---

## 5. Pregunta 5 — ¿firmware/NVM o secuencia? (a/b/c)

### 5.1 ¿Firmware FM aparte? NO
No existe `fm_wcn3990.bin`. El DSP FM vive dentro de `crbtfw21.tlv`+`crnv21.bin`
(los mismos de BT). libbt-vendor no descarga ningún blob extra para FM.

### 5.2 ¿La activación es (a) runtime, (b) flag NVM, o (c) ambas?
La evidencia apunta a **(c), con el peso principal en un handshake runtime dentro
del blob, más un posible gating estático en NVM**:

- (a) runtime: sí hay algo runtime, pero **no en bytes open-source**. Es lo que
  hace `wcnss_filter` al abrir `fm_sock` (handshake propietario chip↔daemon). Sin
  eso, el firmware descarta el 0x11 (tu síntoma exacto: TX ok, RX=0).

- (b) NVM estático: el NVM QCA es un TLV de tags. El btqca mainline
  (`btqca.c:347+`, `case TLV_TYPE_NVM`) sólo reescribe/parachea 3 tags conocidos:
  `EDL_TAG_ID_BD_ADDR`, `EDL_TAG_ID_HCI` (baud), `EDL_TAG_ID_DEEP_SLEEP`. **No hay
  ningún tag "FM enable" documentado en el open source.** El resto del NVM pasa
  verbatim. Si Motorola compiló el `crnv21.bin` del G82 con FM deshabilitado
  (porque no expone radio FM en el producto), el firmware **jamás** ruteará el
  0x11 al DSP-FM, hagas lo que hagas desde el host. Es un candidato REAL y no
  falsable desde el open source (el significado de los tags es privado de QCA).

- Cómo distinguir (a) de (b) en la práctica: si conseguís un `crnv21.bin` de un
  device con el MISMO WCN3990 que SÍ tenga FM de fábrica y, tras flashearlo, el
  chip empieza a contestar 0x14 a tu ENABLE_RECV → era (b) NVM. Si sigue sin
  contestar aún con NVM "bueno" → es (a), el handshake de wcnss_filter.

---

## 6. Pregunta 6 — ¿alguien lo hizo en mainline? (lo más importante)

**No. Nadie ha portado el FM del WCN3990 (ni chips análogos) a Linux mainline,
en ningún proyecto.** Búsquedas y verificaciones:

- **sdm845-mainline** (gitlab.com/sdm845-mainline/linux): todo el trabajo WCN3990
  es WiFi (ath10k_snoc), BT (hci_qca), calibración/board files y regdomain. Cero
  FM. (MRs visibles: ath10k calibration variant, board files; ninguno FM.)
- **postmarketOS**: paquetes `wcnss-*` son sólo WLAN/firmware-loader; wiki no
  lista FM del WCN3990 en ningún device. `M0Rf30/wcn3990-regdomain` = sólo WiFi
  regdomain. No hay port FM.
- **linux-media / linux-arm-msm**: hilos WCN3990 son board files ath10k; no hay
  submission de radio-iris/helium para WCN3990.
- **mainline `drivers/media/radio/`** (v6.12): sin iris/helium/qcom/wcn (ya
  confirmado en el research previo y revalidado aquí).
- **Nota curiosa**: existe `rtc6226` en mainline (`drivers/media/radio/rtc6226`,
  `CONFIG_I2C_RTC6226_QCA`) — pero es el tuner I²C DISCRETO de Richwave, no el FM
  del WCN3990. Si algún día quisieras FM "fácil" en mainline, ese driver YA está
  upstream… pero requiere el chip rtc6226 físico, que el G82 no tiene por UART.

Es decir: **no hay approach ajeno para copiar.** Serías el primero.

---

## 7. Respuestas concretas a las preguntas finales del usuario

### ¿Cuál es la secuencia exacta de activación FM (bytes/comandos)?
En el open source: **no existe como secuencia de bytes.** Lo único open-source es
la secuencia de comandos FM *posterior* (ENABLE_RECV `11 01 4C 00` → SET_RECV_CONF
→ SET_ANTENNA → [ENABLE_SLIMBUS para audio] → TUNE). La ACTIVACIÓN del canal
(lo que hace que el firmware acepte el 0x11) es un handshake propietario dentro de
`wcnss_filter` al abrir `fm_sock`, no documentado en bytes.

### ¿Está en código open-source o es puramente el blob?
**Puramente el blob.** Confirmado: el fuente de `wcnss_filter` no está en
LineageOS ni CodeLinaro. `libbt-vendor` (open) sólo lo *lanza* (property) y abre
un socket; no contiene la lógica de activación. El HAL FM (open) sólo serializa
comandos FM y se los pasa al servicio `IFmHci` (blob).

### ¿Alguien lo hizo en mainline?
**No, nadie.** Ni pmOS, ni sdm845-mainline, ni linux-media. No hay precedente.

### ¿Es viable replicarlo en un módulo kernel, o necesito el blob?
Tres caminos, en orden de realismo:

- **Camino 1 (recomendado para descubrir la secuencia): ingeniería inversa
  DINÁMICA.** El open source NO te va a dar los bytes de activación porque están
  en el blob. La única forma de obtenerlos es **capturar el tráfico UART del
  WCN3990 con el sistema STOCK (Android) corriendo y FM encendido**: pinchar el
  UART (o usar el btsnoop/hci logging del propio wcnss_filter, o un LD_PRELOAD que
  loguee los write() al fd de `fm_sock`), abrir la radio FM, y ver EXACTAMENTE qué
  bytes preceden al primer `11 01 4C 00`. Si aparece algún frame `0x01 0xFC..`
  (vendor BT) o un `0x11` de setup antes del ENABLE_RECV, ESE es el que te falta.
  Sin esta captura, replicarlo es adivinar.

- **Camino 2: replicar el rol de wcnss_filter en un módulo/daemon propio.** Es
  posible en principio: tu módulo ya es dueño del UART y ya inyecta 0x11. Lo que
  falta es (a) descubrir el handshake de activación (→ Camino 1), y (b) posible
  fix de NVM (→ §5). No necesitás bionic/Android para esto: `wcnss_filter` no hace
  nada mágico de Android más allá de properties/sockets; su trabajo real (power +
  abrir tty + handshake firmware + mux) es replicable en C plano/kernel. El
  bloqueo NO es dependencia de bionic, es que no conocés el handshake.

- **Camino 3: ejecutar el blob.** `wcnss_filter` es un ELF Android (bionic, libc
  Android, propiedades). Correrlo en pmOS/glibc es inviable sin un shim de
  properties + libhardware + el tty en el lugar esperado; y además espera hablar
  con `btpower`/init.rc. No lo recomiendo; es más trabajo que el Camino 1+2.

### Riesgo NVM (no ignorar)
Aun resolviendo el handshake, si el `crnv21.bin` del G82 trae FM deshabilitado
(muy plausible: Motorola no expone radio FM en el G82), NINGUNA secuencia de host
lo activará. Antes de invertir en el handshake, vale la pena **descartar (b)**:
comparar/probar un NVM de un WCN3990 con FM de fábrica (§5.2).

---

## 8. Citas exactas (repo : archivo : línea)

Activación FM libbt-vendor (open) — NO manda bytes FM:
- hw-qcom-bt : libbt-vendor/src/bt_vendor_qcom.c : 764-772  (FM_VND_OP_POWER_CTRL; acción "future" vacía)
- hw-qcom-bt : libbt-vendor/src/bt_vendor_qcom.c : 851-854  (BT_VND_OP_FM_USERIAL_OPEN -> goto userial_open)
- hw-qcom-bt : libbt-vendor/src/bt_vendor_qcom.c : 1052-1110 (caso CHEROKEE: start_hci_filter + connect fm_sock; SIN enable_controller_log, SIN write UART)
- hw-qcom-bt : libbt-vendor/src/bt_vendor_qcom.c : 338-373   (start_hci_filter = sólo properties)

Fuente de wcnss_filter — AUSENTE (blob):
- clo-bt (CAF platform/hardware/qcom/bt) : dirs = libbt-vendor + msm89xx; sin `filter`
- hw-qcom-bt (LineageOS) : sólo libbt-vendor; sin `filter`

HAL FM (open) — no antepone 0x11, va a servicio HIDL/AIDL blob:
- fm-commonsys : fm_hci/fm_hci.cpp : 736-756 (hci_transmit: manda 3+len bytes a IFmHci)
- fm-commonsys : fm_hci/fm_hci.cpp : 53-84,158 (vendor.qti.hardware.fm.IFmHci = blob)
- fm-commonsys : helium/radio_helium_hal_cmds.c : 43,77-83 (send_fm_cmd_pkt; hci_fm_enable_recv_req = OGF13/OCF01)
- fm-commonsys : jni/android_hardware_fm.cpp : 135,894 (ENABLE_SLIMBUS = audio, no control)

Kernel — el FM del WCN3990 NO pasa por hci_qca (downstream ni mainline):
- kernel Motorola sm6375 (lineage-22.1) : drivers/bluetooth/hci_qca.c : 927-932 (qca_recv_pkts sin 0x14/FM)
- kernel Motorola sm6375 : drivers/media/radio/Makefile (sin radio-iris/helium; sólo rtc6226 discreto)
- torvalds/linux v6.12 : drivers/bluetooth/hci_qca.c : 1250-1253 (idéntico; sin FM)
- torvalds/linux v6.12 : drivers/bluetooth/btqca.c : 347+ (NVM TLV: sólo BD_ADDR/HCI/DEEP_SLEEP; sin tag FM)

Mainline FM WCN3990 — inexistente:
- sdm845-mainline, postmarketOS, linux-media: 0 ports FM WCN3990 (sólo WiFi/BT/regdomain)

---

## 9. Recomendación operativa (qué hacer ahora)

1. **Descartar NVM (barato, primero):** conseguí `crnv21.bin` de un device con
   WCN3990 y FM de fábrica; probá con él. Si el chip pasa a contestar 0x14 → era
   NVM; problema resuelto sin tocar wcnss_filter.

2. **Si NVM no es (o no alcanza): captura dinámica del stock.** Booteá Android
   stock del G82, activá FM, y capturá los write() a `fm_sock` (LD_PRELOAD sobre
   libfm-hci) o el UART crudo. Buscá cualquier byte que preceda al primer
   `11 01 4C 00`. Ese es el handshake que te falta. **Esta es la única fuente de
   la verdad; el open source ya te lo di entero y no lo contiene.**

3. **Con esos bytes en mano:** replicá el handshake en tu módulo (Camino 2). No
   necesitás el blob ni bionic; sólo el conocimiento de la secuencia + el fix de
   NVM. Ahí sí tu inyector de 0x11 empezará a recibir 0x14.
