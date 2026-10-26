# Payloads del WiFi Pineapple Pager bajo qemu-user — ejecución real (bridge)

> **Estado: FUNCIONA.** Los payloads del Pager (DuckyScript + bash) se ejecutan
> de verdad bajo el firmware MIPSel emulado con `qemu-user-mipsel` en el
> Motorola G82 (Kali/NetHunter aarch64). Validado end-to-end: un payload simple
> (solo `LOG`) y un payload de **red real** (ping/curl saliendo al hardware).
>
> Filosofía: **el firmware del Pager manda la lógica**; nosotros solo ponemos un
> ADAPTADOR del lado del host para que lo que el payload intenta hacer se
> ejecute sobre el OS/hardware/red real del teléfono. No se reimplementa
> DuckyScript.

## 1. Cómo ejecuta un payload el firmware del Pager (confirmado por strace)

El firmware Go (`pineapple/pineapple`, función `RunPagerPayload`) lanza un
payload así:

1. Escribe un script temporal **`/tmp/payload-XXXX.sh`**. Ruta ABSOLUTA →
   bajo qemu-user (sin chroot) cae en el **`/tmp` del HOST**.
2. Antepone un **header** que fija el entorno Hak5:
   ```sh
   #!/bin/bash

   PATH="$PATH:/mmc/bin:/mmc/sbin:/mmc/usr/bin:/mmc/usr/sbin"
   . /lib/hak5/commands.sh
   . /lib/hak5/pineapple.sh
   . /lib/pineapple/payloads.sh
   ```
   …y a continuación el `payload.sh` del usuario.
3. Lo ejecuta con el **`/bin/bash` del HOST (aarch64)** — NO con un bash MIPS.
   - `env` entregado al payload = **solo** `PAYLOAD_HOME` y `_PAYLOAD_HOME`
     (NO hereda `PATH`; bash usa su PATH compilado por defecto
     `/usr/local/bin:/bin:/usr/bin` y el header le añade `/mmc/*`).
   - `stdin`/`stdout`/`stderr` → `/dev/null` (la salida del payload **no** va a
     la UI; la UI se alimenta por la API local, ver §2).
4. Al terminar: `unlink` del temp y marca el payload como completado/erróneo
   (`[PAYLOAD] Payload completed` / `exited with N`).

### El canal real DuckyScript → pantalla/hardware

Los comandos DuckyScript del Pager (LED, VIBRATE, UI_TAKEOVER, WIFI_*, …) son
**scripts bash en `/usr/bin`** que hacen `HAK5_API_POST` (de
`/lib/hak5/commands.sh`) → `curl --unix-socket /tmp/api.sock` contra la **API
HTTP local del firmware**. Ejemplos del rootfs:

| Comando     | Endpoint (`/api/...`)            |
|-------------|----------------------------------|
| `LED`       | `system/led`                     |
| `VIBRATE`   | `pager/vibrate/play`             |
| `UI_TAKEOVER`| `payload/interact/takeover/{display,input}` |

La familia de UI/flow usa `/payload/interact/*`:
`log, alert, error, confirmation, prompt, prompt_qr, menu, wait_for_input,`
`string_picker, number_picker, ip_picker, mac_picker, list_picker,`
`spinner/start, spinner/stop/{id}, takeover, ...`

El firmware **sirve `/tmp/api.sock` también bajo qemu** (es un socket del host),
así que este mecanismo funciona SIN cambios en el host.

## 2. Por qué fallaba (diagnóstico strace, confirmado)

Al lanzar el payload de ejemplo `LOG`:

```
openat("/tmp/payload-XXXX.sh", O_RDWR|O_CREAT|O_EXCL)      = 35   # host /tmp
execve("/bin/bash", ["/bin/bash","/tmp/payload-XXXX.sh"], [PAYLOAD_HOME,...])
openat("/lib/hak5/commands.sh")   = 3    # OK (ya symlinked)
openat("/lib/hak5/pineapple.sh")  = 3    # OK
openat("/lib/pineapple/payloads.sh") = -1 ENOENT   # (a) FALTA en el host
...: line 14: LOG: command not found                # (b) comando inexistente
+++ exited with 127 +++
[CRITICAL] [PAYLOAD] Error ... : exited with 127
```

Causas exactas:
- **(a)** `/lib/pineapple/payloads.sh` no existía en el host (el firmware la
  sourcea; `/lib/hak5` ya estaba enlazada por un paso previo).
- **(b)** Los comandos DuckyScript **no estaban en el PATH del host**. El rootfs
  trae ~67 de ellos (LED, VIBRATE, WIFI_*, …) pero **le faltan los más usados**
  (`LOG` aparece 5874 veces en los payloads, `ALERT`, `ERROR_DIALOG`, `PROMPT`,
  `WAIT_FOR_BUTTON_PRESS`, los `*_PICKER`, `SPINNER`…). En hardware real
  resuelven en `/usr/bin`; aquí daban `command not found` → exit 127.
- **(c)** `jshn` (puente JSON↔shell de libubox que usa `commands.sh`) es un
  binario **MIPS**; el bash del host no lo ejecuta directamente.

## 3. La solución (100% lado host, fiel al Pager)

Todo vive en el host y respeta el mecanismo del Pager (POST a `/tmp/api.sock`).

### `scripts/payload_env.sh` (idempotente; lo llaman `fw_run.sh` y `prepare.sh`)

1. **`/lib/pineapple` → `rootfs/lib/pineapple`** (y reasegura `/lib/hak5`,
   `/usr/share/libubox/jshn.sh`). Resuelve (a).
2. Construye **`qemu/hak5bin/`** con:
   - **symlinks a los scripts DuckyScript del rootfs** (los de Hak5, sin tocar);
   - **los comandos que faltaban**, enviados en `qemu-firmware/hak5-ducky-commands/`.
3. Los **espeja en `/mmc/usr/bin` y `/mmc/bin`** — los directorios que el header
   del firmware realmente busca (el PATH del payload es `…:/mmc/*`). Resuelve (b).

### `hak5-ducky-commands/` (los comandos que faltaban)

Cada uno replica el patrón de LED/VIBRATE (sourcea `commands.sh`/`pineapple.sh`
y hace `HAK5_API_POST "payload/interact/<cmd>"`). Incluidos:

- Salida/UI: `LOG`, `ALERT`, `ERROR_DIALOG`, `PROMPT`, `PROMPT_QR`.
- Flujo bloqueante: `WAIT_FOR_INPUT`, `WAIT_FOR_BUTTON_PRESS`,
  `CONFIRMATION_DIALOG`.
- Entrada (devuelven valor por stdout, como en el Pager):
  `IP_PICKER`, `MAC_PICKER`, `NUMBER_PICKER`, `STRING_PICKER`, `TEXT_PICKER`,
  `LIST_PICKER`.
- Spinner: `START_SPINNER`, `STOP_SPINNER`, `SPINNER`.
- **`jshn`**: shim que ejecuta el **binario MIPS original** bajo
  `qemu-mipsel -L rootfs` (fiel; mismas semánticas de parseo JSON). Resuelve (c).

### `scripts/fw_run.sh`

- Llama `payload_env.sh` en cada arranque.
- Pasa a qemu un `PATH` que incluye `hak5bin` + los sbin del host (para que los
  binarios de red nativos — `curl`, `ping`, `ip`, `iw`, `nmap`, … — se resuelvan
  y **salgan al hardware/red real**).

## 4. Qué comandos quedan soportados / limitaciones honestas

**Funcionan y están verificados end-to-end:**
- `LOG` (color incluido) → aparece en el *payload log* de la UI. Validado:
  payload `examples/LOG` → `[PAYLOAD] Payload completed`.
- `jshn` (parseo JSON dentro de payloads). Validado: parsea
  `{"ip":"10.0.0.9","port":1337}` → `ip=10.0.0.9 port=1337`.
- **Comandos de red nativos** (`ping`, `curl`, …) salen a la red real.
  Validado: payload que hace `ping -c2 8.8.8.8` → respuesta real (0% loss,
  ~27 ms sobre `wlan0`) y `[PAYLOAD] Payload completed`.
- Los ~67 comandos stock del rootfs (LED, VIBRATE, UI_TAKEOVER, WIFI_*, RINGTONE,
  …) quedan en el PATH y usan `/tmp/api.sock` igual que en el dispositivo.
- `ALERT`, `ERROR_DIALOG`, `START_SPINNER` responden 200 al endpoint (probado
  aislado).

**Implementados pero con verificación parcial (honestidad):**
- Los diálogos **interactivos** (`CONFIRMATION_DIALOG`, `*_PICKER`,
  `WAIT_FOR_*`, `LIST_PICKER`) son **bloqueantes**: el endpoint
  `/payload/interact/*` no responde hasta que el usuario actúa en la pantalla
  (igual que en el Pager real). El POST y el envío de opciones están probados;
  el **nombre exacto del campo de la respuesta** (p.ej. `value` vs `selected`
  vs `selected_index`) se infirió de los símbolos del binario
  (`json:"value"`, `json:"selected"`, `json:"selected_index"`) y de los eventos
  `PayloadPicker*Event`. Cada picker intenta varios campos conocidos, pero la
  correspondencia 1:1 no fue confirmada con interacción manual para todos por la
  fragilidad de teclear el Virtual Pager por websocket. Si algún payload de
  picker no mapea bien el resultado, ajustar el campo en el script
  correspondiente de `hak5-ducky-commands/`.

**No soportado / límites reales:**
- `/usr/bin/_pineap` y `hak5cmd` son binarios **MIPS**. Los comandos
  `PINEAP_SSID_*` de `pineapple.sh` los invocan. No se tradujeron a
  `pineapd-pager` en esta fase; un payload que dependa de `_pineap` fallará esa
  parte (el back-end WiFi real existe vía `pineapd-pager`+bridge, pero el
  cliente `_pineap` del payload no está puenteado todavía). Opción futura:
  un shim `_pineap` que hable el socket de `pineapd-pager`, o ejecutarlo bajo
  `qemu-mipsel -L rootfs` como hicimos con `jshn`.
- Payloads que dependan de binarios del sistema **que no existan en el host**
  aarch64 (p.ej. utilidades OpenWrt específicas) fallarán esa llamada concreta.
- `WAIT_FOR_BUTTON_PRESS` se mapea al endpoint bloqueante `wait_for_input`
  (semántica de "espera acción del usuario"); si el firmware distingue botón
  físico específico, podría requerir ajuste.

## 5. Añadir/ejecutar payloads

Los payloads viven en el rootfs del firmware, por categoría:
`rootfs/root/payloads/{user,recon,alerts}/<categoria>/<nombre>/payload.sh`.
El poblado de comunidad lo hace `prepare.sh` desde `community/`. Para ejecutar:
UI del Pager → **Payloads** → categoría → payload → **Launch**. El firmware
escribe el temp, lo corre con el entorno de §3 y refleja `LOG`/diálogos en
pantalla.

## 6. Archivos del port implicados

- `qemu-firmware/scripts/payload_env.sh` — arma el entorno DuckyScript (NUEVO).
- `qemu-firmware/scripts/fw_run.sh` — llama a payload_env + PATH de qemu (editado).
- `qemu-firmware/scripts/prepare.sh` — llama a payload_env al preparar (editado).
- `qemu-firmware/hak5-ducky-commands/` — comandos que faltaban + `jshn` (NUEVO).
- `qemu-firmware/payload-validation/` — payloads de validación + evidencia strace.
