# WiFi Pineapple Pager — Motor de render del display fisico (para replicar fiel)

El Pager tiene un display 480x222 RGBA. El binario Go lo dibuja interpretando TEMAS
JSON declarativos (igual que el Pandora tiene su display). En vez de recrear la UI,
REPLICAMOS el display real: un interprete web que lee los mismos theme JSON + assets
PNG + resuelve las variables $_ con datos del backend.

Fuente: RE del binario Go (golang-product-server, pkg pineapplepager + hak5font) +
los temas de comunidad (github.com/hak5/wifipineapplepager-themes), que son el
FORMATO REAL del display.

## Modelo (resumen; detalle completo abajo)

### theme.json (manifiesto del tema)
- author, theme_version, theme_framework_version ("0.7")
- status_bars{}: 5 barras (default/network/recon/min/gps) -> rutas de componentes
- generic_menus{}: registro de PANTALLAS (nombre -> ruta JSON). Los `target` resuelven aca.
- *_path keys: setup_wizard_path, lock_screen_path, payload_log_path, keyboards, dialogs...
- option_dialogs{}, toggle_templates{}, string_templates{}, radio_templates{}, signal_templates{}
- color_palette{}: nombre -> {r,g,b}  (per-tema, NO hardcodear)

### Componente = una pantalla (JSON)
- screen_name, status_bar ("default"...), windowed_canvas, cache_canvases
- button_map: a/b/up/down/left/right -> accion (select/back/previous/next/previous_page/next_page/noop)
- background: { background_color{r,g,b}, layers[] }
- menu_items[]: { id, x, y, layers[], selected_layers[], target, variable $_XXX, unselectable }
- pages[]: { page_index, menu_items[] }  (paginacion)

### Layer (union)
- x, y (offset relativo al item)
- image_path (relativo al tema) [+ recolor_palette para tintar]
- text, text_color_palette, text_size (small/medium/large), max_chars, wrap_text, max_lines, center_text_within
- variable_name $_XXX + string_template / use_template / toggle_template / signal

### Render (z-order bottom->top)
1. background_color (480x222)
2. background.layers[] (painter order)
3. menu_items[] de la pagina actual; el item enfocado usa selected_layers en vez de layers
4. status bar (arriba, y~0-20)
5. overlays (dialogs/spinner/lock/power = windowed components stacked)

### Texto: PixFont (bitmap font hak5font). text_size->px calibrar: small~11-12, medium~16, large~22-24.
   \n = newline; wrap_text + max_lines; max_chars trunca; center_text_within centra en bbox.
   Anaglyph = dibujar 3x con offset -1/+1/0 en red3d/cyan3d/white.

### Recolor: tinta un PNG (blanco/alpha) al color de la paleta preservando alpha (replace-RGB-keep-alpha).

### Navegacion: ScreenManager = STACK de componentes. push target -> render; back -> pop.
   button_map (+ overrides por item). previous/next mueve foco; previous_page/next_page pagina.
   target resuelve a: pantalla (generic_menus) | function_* (codigo) | inline_toggle | fallback_menu.
   unselectable:true -> item decorativo (foco lo saltea).

### Variables $_XXX (registro nombre->getter). LoadVariables al push. Fuentes: pineapd/system/uci.
   Status/system: $_CLOCK_ (hora 15:04), $_BATTERY_INFO (bq27546 % + estado), $_BRIGHTNESS, $_VOLUME,
     $_VIBRATE, $_HOSTNAME, $_UPTIME, $_FIRMWARE_VERSION, $_BLUETOOTH_ENABLED, $_DPAD_LED_COLOR...
   PineAP: $_PINEAP_IMPERSONATE_NETWORKS, _COLLECT_NETWORKS, _ADVERTISE_NETWORKS, _RANDOMIZE_MAC_ADDRESS,
     _COLLECT_HANDSHAKES, $_OPEN_AP_ENABLED/_SSID, $_EVIL_WPA_ENABLED/_SSID/_PASSPHRASE, $_PINEAP_SSID_POOL_LIST...
   Recon: $_RECON_RESULT_SSID/_BSSID/_RSSI/_CHANNEL/_CLIENT_COUNT/_ENCRYPTION_TYPE,
     $_RECON_LIST_SIZE/_CURRENT_PAGE/_TOTAL_PAGES, $_RECON_SELECTED_AP_*, settings $_RECON_HOP_CHANNELS...
   Payloads: $_PAYLOAD_CATEGORIES, $_PAYLOADS_FOR_CATEGORY, $_INSTALLED_PAYLOADS_LIST, $_PAYLOAD_TITLE/_AUTHOR...
   Alerts: $_ALERTS_NEW_CLIENT/_AUTH_CAPTURED/_HANDSHAKE_CAPTURE/_DEAUTH_FLOOD.
   GPS: $_GPS_LAT/_LON. Input: $_INPUT (teclado). $_THEME.

### Status bars (5) - items por x absoluto, estado->asset:
   default: Time(400,7) Battery(346) Volume(317) Brightness(286) Vibrate(251)
   network: default + FreqIndicators(216)
   recon: Time Battery FreqIndicators(315) Brightness(282) PCAP(249) DB(216) GPS(183) Wigle(144)
   min: Time + Battery
   gps: default + GPS(216)

### Pantallas especiales: setup_wizard (multi-page), lock_screen (windowed), power_menu (windowed:
   Sleep/Lock/Lock Buttons/Nerd Stats/Shutdown -> function_*), dialogs/keyboards/spinner/boot_animation.

## Plan de reimplementacion (interprete web fiel)
1. Cargar theme.json -> paletas, registro status-bars, registro pantallas, templates.
2. Loader de componentes (lazy fetch JSON, cache PNG, image_path relativo al tema).
3. Renderer <canvas> 480x222: background_color -> bg.layers -> menu_items (focus=selected_layers) -> status bar.
4. Layer dispatch: image(+recolor) / text(PixFont) / variable(resolver $_ -> template).
5. Font bitmap; text_size->px calibrar vs demo.gif.
6. Registro de variables Map<nombre, valor|lista>; conectar a la API real (/api/*) y mock donde falte.
7. Navegacion: stack + button_map; target (pantalla/function_/inline_toggle); unselectable skip.
8. Status bars con state->asset.

## Prioridad: main_dashboard + status_bar default + recon_dashboard + dashboard_pineap.
