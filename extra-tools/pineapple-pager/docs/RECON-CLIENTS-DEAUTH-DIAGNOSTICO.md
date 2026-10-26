# Recon: clientes por AP + deauth — diagnóstico (pendiente de fix)

## Estado (lo que YA funciona)
- Recon real por la Archer (wlan1mon): PPS, gráfico, lista de APs con nombres, highlight, scroll sin crash, client_count.
- Ve todas las redes incl. WiFi Mateo 2.4G (ch11, bssid 8a:c2:27:a1:19:cc) y 5G (ch161).
- DEAUTH inyecta frames REALES: handler h_send_deauth (cmd 400). Lee bssid/sta como BIN6 (msg_get_mac6, agregado a
  msg.c/msg.h) + channel string. Responde "sent N frames" e inyecta por wlan1mon en el canal. CONFIRMADO funcionando.

## BUG 1 (pendiente): la lista de clientes de un AP muestra TODOS los clientes (75), no los del AP
Causa (diagnosticada por captura + RE de strings):
- Al entrar a un AP y "ver clientes", el firmware NO manda ningun comando con el bssid del AP por el socket.
  Solo pollea cmd=10/208/314. La lista de clientes la pide con cmd=217 (rstable) en el dashboard, y el daemon
  responde con TODOS los clientes (67-75) — el request 217 trae devicelist:null SIEMPRE (nunca el bssid del AP).
- El firmware FILTRA los clientes EL MISMO, internamente, por el AP seleccionado: strings del binario:
  "CurrentlySelectedAPReconItem record", "failed to get recon stable clients list from pineap",
  "pineapclientslist | RELOADING/RELOADED Variable", "UIReconAPClientsList.Render", "new ap clients list selected".
- Muestra los 75 => el filtro interno cliente.bssid == selectedAP.bssid NO matchea. Hipotesis fuerte: mismatch de
  FORMATO del campo bssid que mandamos en cada cliente (cli_row_cb en handlers.c manda mac BIN6 y bssid BIN6). El
  firmware compara contra el bssid del AP (que en la lista de APs mandamos como "bssid" string + "mac" BIN6). 
  Probable: el firmware compara el bssid del cliente (string?) con el del AP (string?) y nuestro cliente lo manda
  como BIN6 (o al reves) => nunca matchea => no filtra => muestra todos.
- CONSECUENCIA en deauth: el usuario elige un "cliente" de la lista sin filtrar que en realidad es un BSSID de AP
  vecino (ej 8a:c2:27:a1:19:cf = "Personal Wifi Zone"), y el deauth (que SI funciona) va a la MAC equivocada.

## Clientes REALES de Mateo 2.4G (para validar el fix): 
bssid 8a:c2:27:a1:19:cc -> clientes 50:bb:b5:7b:0c:fc, 96:2f:ef:12:f8:f6, b8:ab:62:6f:fd:a7 (uno es el Motorola RZR).
La DB (recon.db) asocia bien: "SELECT mac FROM clients WHERE bssid='8a:c2:27:a1:19:cc'" da esos 3. El problema NO es
la captura/asociacion del daemon, es el FORMATO en el frame 217 vs lo que el firmware espera para filtrar.

## PROXIMO FIX (a hacer):
1. RE fino del filtro del firmware (UIReconAPClientsList / CurrentlySelectedAPReconItem): ver el FORMATO/campo EXACTO
   del bssid que compara en cada cliente (string "aa:bb:.." vs BIN6; nombre del campo). handlers.c cli_row_cb manda:
   mac(BIN6), bssid(BIN6), signal(0), packets(0), ts. Ajustar para que matchee el filtro interno (probablemente
   mandar bssid como STRING, o un campo extra). Comparar con como la lista de APs manda el bssid (dev_cb: mac BIN6 +
   bssid string + ssidmap[].ssid + hash).
2. Subir el count de deauth: h_send_deauth default count=1 => 2 frames (1 deauth + 1 disassoc). Insuficiente. Subir a
   ~32-64 por burst para que desconecte de verdad (dispositivos modernos ignoran deauths sueltos).
3. signal/packets de clientes van en 0 (cli_row_cb); exponer reales del recon_mon si el firmware los muestra.

## Como capturar (metodo que funciono: a dos manos, el asistente pone logs y el usuario navega):
- truncate -s 0 /tmp/pineap_bridge.log ; el usuario navega ; decodificar con python3+msgpack (OJO escape comillas en
  SSH anidado: escribir el .py a un archivo en el G82 y ejecutarlo, no inline).
- El bridge loguea FW->DMN/DMN->FW cmd=N body=<hex> completo. cmd util: 216=lista APs, 217=lista clientes, 400=deauth.
- vp_screencap.py --password pineapplepager --out /tmp/x.png para ver la pantalla del firmware.
