/* =============================================================================
 * pager.js — Orquestador del "Virtual Pager" (motor de render del display).
 * -----------------------------------------------------------------------------
 * Une: theme (manifiesto) + component loader + variables + navigator +
 * renderer + statusbar, dibujando sobre un <canvas id="display"> 480x222.
 *
 * - Carga el tema default (wargames) y arranca en el main_dashboard
 *   (generic_menus.dashboard_path).
 * - Conecta el D-pad / A / B / flechas de la carcasa (los .hwbtn[data-key] del
 *   index.html) + teclado al navigator segun el button_map del componente.
 * - Loop de refresco: reloj de la status bar + variables + re-render.
 *
 * Mapeo de teclas fisicas -> botones logicos del Pager:
 *   Enter/SELECT -> a     Escape/BACK -> b
 *   ArrowUp -> up   ArrowDown -> down   ArrowLeft -> left   ArrowRight -> right
 *
 * Uso:
 *   import { Pager } from './engine/pager.js';
 *   const pager = new Pager({ canvas, api, themeId:'wargames' });
 *   await pager.boot();
 * ========================================================================== */

'use strict';

import { ThemeLoader } from './theme.js';
import { ComponentLoader } from './component.js';
import { Variables } from './variables.js';
import { Navigator } from './navigator.js';
import { renderPage } from './renderer.js';
import { drawStatusBar } from './statusbar.js';
import { drawReconGraph } from './recon_graph.js';
import { PixFont } from './pixfont.js';

const W = 480, H = 222;

export class Pager {
  constructor(opts = {}) {
    this.canvas = opts.canvas;
    this.api = opts.api || null;
    this.themeId = opts.themeId || 'wargames';
    this.baseFor = opts.baseFor || ((id) => `community/themes/${id}/`);
    this.ctx = this.canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = false;

    this.themeLoader = new ThemeLoader({ baseFor: this.baseFor });
    this.theme = null;
    this.loader = null;
    this.vars = new Variables(this.api);
    this.nav = null;
    this._dirty = false;
    this._rendering = false;
    this._statusCache = new Map(); // name -> component json
  }

  async boot() {
    this.theme = await this.themeLoader.load(this.themeId);
    this.loader = new ComponentLoader(this.theme);

    this.nav = new Navigator({
      theme: this.theme,
      loader: this.loader,
      vars: this.vars,
      resolveList: (frame) => this._resolveList(frame),
      onFunction: (name, item, frame) => this._onFunction(name, item, frame),
      onInlineToggle: (variable, item, frame) => this._onInlineToggle(variable, item, frame),
      onDialogAction: (name, item, frame) => this._onDialogAction(name, item, frame),
      onBeforePush: (res, item, frame) => this._onBeforePush(res, item, frame),
      onListPage: (frame, dir) => this._onListPage(frame, dir),
      onUnresolved: (t) => console.warn('[pager] target sin resolver:', t),
      onChange: () => this.requestRender(),
    });

    // primera carga de variables antes del primer frame
    await this.vars.refresh().catch(() => {});
    // pre-carga la lista de temas (para el theme picker: _optionsFor('$_THEME')
    // es sync pero getThemes es async; la cacheamos en el snapshot).
    await this.vars.loadThemes().catch(() => {});

    // arranque en el dashboard (dashboard_path / main_dashboard)
    const dashPath = this.theme.genericMenus.dashboard_path
      || this.theme.manifest.dashboard_path
      || this.theme.genericMenus.main_dashboard;
    if (!dashPath) throw new Error('No dashboard_path en el tema');
    await this.nav.start(dashPath, 'main_dashboard');

    this._startLoops();
    this.requestRender();
  }

  // Cambia de tema en caliente (recarga manifest + loader + reinicia nav).
  async setTheme(id) {
    this.themeId = id;
    this.theme = await this.themeLoader.load(id);
    this.loader.setTheme(this.theme);
    this.nav.env.theme = this.theme;
    this._statusCache.clear();
    const dashPath = this.theme.genericMenus.dashboard_path
      || this.theme.genericMenus.main_dashboard;
    await this.nav.start(dashPath, 'main_dashboard');
    this.requestRender();
  }

  // ---- input de botones ---------------------------------------------------
  // key fisica -> boton logico del Pager
  _logical(key) {
    switch (key) {
      case 'Enter': return 'a';
      case 'Escape': return 'b';
      case 'ArrowUp': return 'up';
      case 'ArrowDown': return 'down';
      case 'ArrowLeft': return 'left';
      case 'ArrowRight': return 'right';
      default: return null;
    }
  }

  press(physicalKey) {
    const btn = this._logical(physicalKey);
    if (!btn || !this.nav) return;
    this.nav.press(btn);
  }

  // ---- render -------------------------------------------------------------
  requestRender() {
    this._dirty = true;
    if (this._rendering) return;
    requestAnimationFrame(() => this._render());
  }

  async _render() {
    if (!this.nav || !this.theme) return;
    this._rendering = true;
    this._dirty = false;
    const top = this.nav.current();
    if (!top) { this._rendering = false; return; }

    // --- overlay stack ----------------------------------------------------
    // Los dialogs/wizards (windowed_canvas:true) se COMPONEN sobre el frame de
    // abajo (el firmware los pinta como ventana flotante, no limpian la pantalla
    // a negro). Buscamos el ultimo frame NO-windowed (la pantalla base) y
    // renderizamos desde ahi hacia arriba. Un windowed sin background_color se
    // dibuja encima preservando el fondo; uno con background_color propio lo usa.
    const stack = this.nav.stack;
    let baseIdx = stack.length - 1;
    while (baseIdx > 0 && this._isWindowed(stack[baseIdx])) baseIdx--;

    try {
      for (let i = baseIdx; i < stack.length; i++) {
        const frame = stack[i];
        const overlay = (i > baseIdx) && this._isWindowed(frame)
          && !(frame.component.background && frame.component.background.background_color);
        const env = {
          theme: this.theme,
          loader: this.loader,
          vars: this.vars,
          pixfont: PixFont,
          page: frame.pages[frame.pageIndex],
          focusIndex: frame.focusIndex,
          overlay,              // no limpiar a negro: compone sobre lo de abajo
        };
        await renderPage(this.ctx, frame.component, env);

        // RENDER ESPECIAL (BUG 3): el recon_dashboard dibuja un bar-graph de PPS
        // que el firmware pinta NATIVO (no esta en el theme JSON como componente).
        // Tras pintar el componente (su fondo), overlay-eamos el grafico.
        if (this._isReconDashboard(frame)) {
          try {
            drawReconGraph(this.ctx, frame.component, {
              vars: this.vars, theme: this.theme, pixfont: PixFont,
            });
          } catch (e) { console.error('[pager] recon graph', e); }
        }
      }
      // la status bar la dibuja el frame TOP (su status_bar propio).
      await this._drawStatusBar(top);
    } catch (e) {
      console.error('[pager] render error', e);
    }

    this._rendering = false;
    if (this._dirty) this.requestRender();
  }

  _isWindowed(frame) {
    return !!(frame && frame.component && frame.component.windowed_canvas);
  }

  // Detecta el recon_dashboard (pantalla "graph"): el firmware pinta un bar-graph
  // nativo de PPS encima de su fondo. Se reconoce por el nombre del frame o por
  // la firma del componente (background_path + target_recon_list, sin menu_items).
  _isReconDashboard(frame) {
    if (!frame || !frame.component) return false;
    const c = frame.component;
    const name = (frame.name || c.screen_name || '').toLowerCase();
    if (/recon_dashboard/.test(name)) return true;
    return !!(c.target_recon_list && c.background_path
      && (!c.menu_items || !c.menu_items.length));
  }

  async _drawStatusBar(frame) {
    const sbName = frame.component.status_bar;
    if (!sbName) return;
    const path = this.theme.statusBarPath(sbName);
    if (!path) return;
    let json = this._statusCache.get(path);
    if (!json) {
      try { json = await this.loader.loadComponent(path); }
      catch (_) { return; }
      this._statusCache.set(path, json);
    }
    await drawStatusBar(this.ctx, json, {
      theme: this.theme, loader: this.loader, vars: this.vars, pixfont: PixFont,
    });
  }

  // ---- listas (sistema generico) ------------------------------------------
  // Devuelve las filas (ya paginadas) de la lista del frame actual. Cada fila
  // es un objeto de variables por-fila; SIEMPRE trae '$_LIST_ITEM' (el texto de
  // la fila) y, para recon, tambien los '$_RECON_RESULT_*'.
  //
  // El tipo de lista se detecta por screen_name/name o por el ctx heredado del
  // item seleccionado (ej: payloads_for_category_list usa la categoria elegida).
  async _resolveList(frame) {
    const kind = this._listKind(frame);
    let all = [];
    try {
      switch (kind) {
        case 'payloads_categories': all = await this._rowsPayloadCategories(); break;
        case 'payloads_for_category': all = await this._rowsPayloadsForCategory(frame); break;
        case 'themes':   all = await this._rowsThemes(); break;
        case 'ssidpool': all = await this._rowsSsidPool(); break;
        case 'recon':    all = await this._rowsRecon(); break;
        case 'options':  all = this._rowsOptions(frame); break;
        default:         all = []; break;
      }
    } catch (e) {
      console.warn('[pager] _resolveList', kind, e);
      all = [];
    }

    // paginacion sobre los datos (no sobre pages[])
    const perPage = this._listSlots(frame);
    const total = all.length;
    const pages = Math.max(1, Math.ceil(total / perPage));
    let page = frame._listPage || 0;
    if (page > pages - 1) page = pages - 1;
    if (page < 0) page = 0;
    frame._listPage = page;
    const slice = all.slice(page * perPage, page * perPage + perPage);

    // estado de la lista -> variables $_LIST_SIZE / paginator / titulo
    this.vars.setListState({
      size: total,
      page: page + 1,
      pages,
      title: this._listTitle(frame, kind),
    });
    // recon tambien expone $_RECON_LIST_* desde su snapshot
    if (kind === 'recon') {
      this.vars.snapshot.recon._page = page + 1;
      this.vars.snapshot.recon._pages = pages;
    }
    return slice;
  }

  // Clasifica la lista por screen_name / name / ctx heredado.
  _listKind(frame) {
    const name = (frame.name || frame.component.screen_name || '').toLowerCase();
    // option dialogs (ui_option_dialog*): screen_name "option_picker" o target
    // "option_dialog*". Son listas de OPCIONES (radio) para elegir un valor.
    if (/option_picker|option_dialog/.test(name)) return 'options';
    const tgt = (frame.ctx && frame.ctx.target || '').toLowerCase();
    if (/option_dialog/.test(tgt)) return 'options';
    if (/recon/.test(name)) return 'recon';
    if (/ssid.*pool|pool.*ssid/.test(name)) return 'ssidpool';
    if (/theme/.test(name)) return 'themes';
    if (/payloads?_for_category|for_category_list/.test(name)) return 'payloads_for_category';
    if (/payload/.test(name)) return 'payloads_categories';
    return 'generic';
  }

  _listTitle(frame, kind) {
    if (frame.ctx && frame.ctx.label) return frame.ctx.label;
    if (kind === 'payloads_categories') return 'Payloads';
    if (kind === 'themes') return 'Themes';
    if (kind === 'ssidpool') return 'SSID Pool';
    if (kind === 'recon') return 'Recon';
    return '';
  }

  // Cantidad de slots por pagina (ids Slot*/Row*). Default 5.
  _listSlots(frame) {
    const items = (frame.pages[frame.pageIndex] || {}).menu_items || [];
    const n = items.filter((it) => /^(Slot|Row)\d+$/i.test(it.id || '')).length;
    return n || 5;
  }

  // ---- generadores de filas por tipo de lista -----------------------------
  async _rowsPayloadCategories() {
    const pl = await this.vars.loadPayloads();
    // categorias top: preferimos las claves del arbol (con contenido real)
    const cats = (pl.categories && pl.categories.length)
      ? pl.categories
      : Object.keys(pl.tree || {});
    return cats.map((c) => ({ '$_LIST_ITEM': c, _payloadCategory: c }));
  }

  async _rowsPayloadsForCategory(frame) {
    const pl = await this.vars.loadPayloads();
    const cat = frame.ctx && frame.ctx.category;
    const node = (pl.tree && cat) ? pl.tree[cat] : null;
    const rows = [];
    if (node) {
      // node = { sub: [entries...] }. Aplanamos: subcarpetas con nombre + payloads.
      for (const sub of Object.keys(node)) {
        const entries = node[sub] || [];
        for (const e of entries) {
          rows.push({
            '$_LIST_ITEM': e.title || e.name,
            _payloadPath: e.path,
            _payload: e,
          });
        }
      }
    } else if (!cat) {
      // sin categoria: lista plana de todos los payloads
      for (const e of (pl.payloads || [])) {
        rows.push({ '$_LIST_ITEM': e.title || e.name, _payloadPath: e.path, _payload: e });
      }
    }
    return rows;
  }

  async _rowsThemes() {
    const themes = await this.vars.loadThemes();
    return themes.map((t) => ({
      '$_LIST_ITEM': t.name || t.id,
      _themeId: t.id,
      _theme: t,
    }));
  }

  async _rowsSsidPool() {
    const pool = await this.vars.loadSsidPool();
    return pool.map((s) => {
      const ssid = (typeof s === 'string') ? s : (s.ssid || s.name || '');
      return { '$_LIST_ITEM': ssid, _ssid: ssid };
    });
  }

  async _rowsRecon() {
    const dl = this.vars.snapshot.recon.devicelist || [];
    return dl.map((d) => this._reconRow(d));
  }

  // Filas de un option dialog (radio). Cada fila lleva $_LIST_ITEM (el label que
  // el renderer dibuja junto al radio) y _selected (si coincide con el valor
  // actual de la variable). Los conjuntos de opciones dependen de la variable en
  // edicion; si el backend no los expone, usamos los conjuntos conocidos del
  // firmware para que el dialog muestre texto (bug: radios sin label).
  _rowsOptions(frame) {
    const variable = (frame.ctx && frame.ctx.editVariable)
      || this.vars.get('$_EDIT_VARIABLE') || '';

    // Theme picker ($_THEME): cada fila es un tema. _selected = el tema activo
    // (this.themeId). Guardamos el id del tema por fila para aplicarlo al elegir.
    if (/THEME/.test(variable)) {
      const themes = this.vars.snapshot.themes || [];
      return themes.map((t) => {
        const id = t.id || t.name;
        return {
          '$_LIST_ITEM': t.name || t.id,
          _optionValue: t.name || t.id,
          _themeId: id,
          _selected: String(id) === String(this.themeId)
            || String(t.name) === String(this.themeId),
        };
      });
    }

    const cur = this.vars.get(variable);
    const opts = this._optionsFor(variable);
    return opts.map((o) => ({
      '$_LIST_ITEM': o,
      _optionValue: o,
      _selected: String(o) === String(cur),
    }));
  }

  // Conjuntos de opciones conocidos por variable. Extensible; el backend podria
  // sobreescribir via snapshot.settings[variable+'_options'].
  _optionsFor(variable) {
    const fromBackend = this.vars.snapshot.settings
      && this.vars.snapshot.settings[variable + '_options'];
    if (Array.isArray(fromBackend) && fromBackend.length) return fromBackend;
    // Theme picker: la variable es $_THEME. Los temas se precargan en el
    // snapshot (loadThemes()) porque _optionsFor es sync y getThemes es async.
    // Devolvemos los NOMBRES de los ~54 temas de /api/themes (el nombre es el
    // $_LIST_ITEM de cada fila del radio dialog).
    if (/THEME/.test(variable)) {
      const themes = this.vars.snapshot.themes || [];
      return themes.map((t) => t.name || t.id).filter(Boolean);
    }
    if (/ENCRYPTION_TYPE/.test(variable)) return ['Open', 'WPA2', 'WPA3', 'WPA2/WPA3'];
    if (/AUDIO_SRC/.test(variable)) return ['Recon', 'System', 'Off'];
    if (/AUDIO_MODE/.test(variable)) return ['Geiger', 'Tones', 'Speech'];
    return [];
  }

  _reconRow(d) {
    const ssid = d.ssid || d.essid || '(hidden)';
    return {
      // tambien seteamos $_LIST_ITEM por si el slot usa la variable generica
      '$_LIST_ITEM': ssid,
      '$_RECON_RESULT_SSID': ssid,
      '$_RECON_RESULT_BSSID': d.bssid || d.mac || '',
      '$_RECON_RESULT_RSSI': d.rssi != null ? d.rssi : (d.signal != null ? d.signal : ''),
      '$_RECON_RESULT_CHANNEL': d.channel != null ? d.channel : '',
      '$_RECON_RESULT_FREQ': d.frequency || d.freq || '',
      '$_RECON_RESULT_CLIENT_COUNT': d.client_count != null ? d.client_count : (d.clients ? d.clients.length : 0),
      '$_RECON_RESULT_ENCRYPTION_TYPE': d.encryption || d.enc || '',
      _device: d,
    };
  }

  // Cambia la pagina de datos de una lista (clamp a [0, pages-1]).
  async _onListPage(frame, dir) {
    const kind = this._listKind(frame);
    let total = 0;
    try {
      switch (kind) {
        case 'payloads_categories': total = (await this._rowsPayloadCategories()).length; break;
        case 'payloads_for_category': total = (await this._rowsPayloadsForCategory(frame)).length; break;
        case 'themes':   total = (await this._rowsThemes()).length; break;
        case 'ssidpool': total = (await this._rowsSsidPool()).length; break;
        case 'recon':    total = (this.vars.snapshot.recon.devicelist || []).length; break;
        case 'options':  total = this._rowsOptions(frame).length; break;
        default: total = 0; break;
      }
    } catch (_) { total = 0; }
    const perPage = this._listSlots(frame);
    const pages = Math.max(1, Math.ceil(total / perPage));
    const cur = frame._listPage || 0;
    frame._listPage = Math.min(pages - 1, Math.max(0, cur + dir));
  }

  // ---- acciones nativas (function_*) --------------------------------------
  async _onFunction(name, item, frame) {
    // No ejecutamos acciones destructivas en el virtual pager por defecto.
    // Hook: el backend podria exponer /api/function/<name>. Aqui solo log.
    console.info('[pager] function:', name, item && item.id);
    if (this.api && this.api.callFunction) {
      try { await this.api.callFunction(name, item); } catch (_) {}
    }
    await this.vars.refresh().catch(() => {});
    this.requestRender();
  }

  // ---- inline toggle ------------------------------------------------------
  // Mapa $_VAR -> clave del backend pineap (PineAP / OpenAP / EvilWPA / filtros).
  // Solo estas variables van por setPineap; el resto se persiste via /api/settings.
  static get PINEAP_TOGGLE_MAP() {
    return {
      '$_PINEAP_IMPERSONATE_NETWORKS': 'impersonate',
      '$_PINEAP_COLLECT_NETWORKS': 'collect',
      '$_PINEAP_ADVERTISE_NETWORKS': 'advertise',
      '$_PINEAP_RANDOMIZE_MAC_ADDRESS': 'randomize_mac',
      '$_PINEAP_COLLECT_HANDSHAKES': 'collect_handshakes',
      '$_OPEN_AP_ENABLED': 'open_ap',
      '$_OPEN_AP_HIDDEN': 'open_ap_hidden',
      '$_EVIL_WPA_ENABLED': 'evil_wpa',
      '$_EVIL_WPA_HIDDEN': 'evil_wpa_hidden',
      '$_FILTER_CLIENT_MODE': 'filter_client_allow',
    };
  }

  // Togglea una variable IN-PLACE: cambia el estado local (override) para que el
  // toggle_template visual refleje el nuevo estado de inmediato, y persiste al
  // backend (setPineap para $_PINEAP_*/openap/evilwpa; POST /api/settings para el
  // resto, ej $_WIFI_CLIENT_MODE_ENABLED, $_VIBRATE, $_BLUETOOTH_ENABLED...).
  async _onInlineToggle(variable, item, frame) {
    if (!variable) return;
    // 1) cambia el estado local -> el renderer lo lee via override (toggle flip)
    const next = this.vars.toggle(variable);
    // 2) sincroniza el snapshot pineap (optimista) para que una recarga posterior
    //    no "pise" el valor hasta que el backend confirme.
    this._applyToggleOptimistic(variable, next);
    // 3) persiste si hay API
    if (this.api) {
      try { await this._persistToggle(variable, next); } catch (_) {}
    }
    this.requestRender();
  }

  _applyToggleOptimistic(variable, val) {
    const k = Pager.PINEAP_TOGGLE_MAP[variable];
    if (k) this.vars.snapshot.pineap[k] = val;
  }

  async _persistToggle(variable, val) {
    const k = Pager.PINEAP_TOGGLE_MAP[variable];
    if (k && this.api.setPineap) {
      await this.api.setPineap({ [k]: val });
      return;
    }
    // toggle generico (settings del sistema / wifi / recon). POST /api/settings
    // con la variable y su nuevo valor. El backend decide como aplicarlo (uci).
    if (this.api.setSettings) {
      await this.api.setSettings({ variable, value: val });
    }
  }

  // ---- dialogs de edicion (edit_string_dialog) + teclado on-screen --------
  // Antes de push-ear un dialog de edicion, siembra $_INPUT con el valor actual
  // de la variable que se va a editar y $_INPUT_NAME con el titulo (el id del
  // item que lo abrio). Asi el dialog aparece con su contenido real (no vacio).
  async _onBeforePush(res, item /*, frame */) {
    const isEditDialog = /edit_(string|ip|mac|number)_dialog|dialog/.test(res.name || '')
      || (item && /^edit_/.test(item.target || ''));
    if (!item) return;
    if (item.variable) {
      // valor actual de la variable a editar (vacio si no existe)
      const cur = this.vars.get(item.variable);
      this.vars.setOverride('$_INPUT', cur);
      this.vars.setOverride('$_EDIT_VARIABLE', item.variable);
    } else {
      this.vars.setOverride('$_INPUT', '');
      this.vars.setOverride('$_EDIT_VARIABLE', '');
    }
    // titulo del dialog = etiqueta del item (ej "Hostname", "Network Name")
    this.vars.setOverride('$_INPUT_NAME', (item.id || '').trim());
  }

  // Acciones in-dialog. Devuelve true si el dialog debe cerrarse (pop).
  //   save   -> persiste $_INPUT en la variable editada y cierra
  //   back   -> (ya lo maneja el navigator) cancelar
  //   keyboard -> abre/edita con un teclado minimo (prompt) y re-renderiza
  //   select / select_menu / select_list -> confirmar opcion (option dialogs)
  async _onDialogAction(name, item, frame) {
    switch (name) {
      case 'save': {
        const variable = this.vars.get('$_EDIT_VARIABLE');
        const value = this.vars.get('$_INPUT');
        if (variable) {
          // override local optimista para que al volver se vea el nuevo valor
          this.vars.setOverride(variable, value);
          if (this.api && this.api.setSettings) {
            try { await this.api.setSettings({ variable, value }); } catch (_) {}
          }
        }
        return true; // cerrar dialog
      }
      case 'keyboard': {
        // Teclado on-screen minimo: en navegador usamos prompt() como entrada
        // (el teclado grid completo es trabajo aparte). Edita $_INPUT in-place.
        const cur = this.vars.get('$_INPUT');
        let next = cur;
        if (typeof window !== 'undefined' && typeof window.prompt === 'function') {
          const r = window.prompt(this.vars.get('$_INPUT_NAME') || 'Edit', cur);
          if (r != null) next = r;
        }
        this.vars.setOverride('$_INPUT', next);
        this.requestRender();
        return false; // no cerrar; el usuario aun puede Save/Cancel
      }
      case 'select':
      case 'select_menu':
      case 'select_list': {
        // option dialog: confirma la fila enfocada. Si el item trae un _row con
        // $_LIST_ITEM, lo aplicamos a la variable en edicion (optimista).
        const variable = this.vars.get('$_EDIT_VARIABLE');
        const row = item && item._row;
        const chosen = (row && row['$_LIST_ITEM'])
          || this.vars.get('$_INPUT');

        // Theme picker: la fila trae _themeId. Aplicar el tema (recarga el
        // manifest + reinicia el nav) y persistir en el backend. setTheme hace
        // nav.start() de nuevo => NO cerramos el dialog manualmente (el stack se
        // reconstruye). Devolvemos false para no pop-ear un stack ya reiniciado.
        if (/THEME/.test(variable || '') && row && row._themeId) {
          const id = row._themeId;
          if (this.api && this.api.setTheme) {
            try { await this.api.setTheme(id); } catch (_) {}
          }
          try { await this.setTheme(id); } catch (e) { console.error('[pager] setTheme', e); }
          return false; // setTheme ya reinicio la navegacion al dashboard
        }

        if (variable && chosen) this.vars.setOverride(variable, chosen);
        return true; // cerrar dialog volviendo al menu
      }
      case 'confirm': {
        // confirmation_dialog: boton "confirmar". Si el pager asocio una accion
        // pendiente al abrir el dialog, la ejecutamos; en cualquier caso cerramos.
        const pending = this._pendingConfirm;
        this._pendingConfirm = null;
        if (typeof pending === 'function') { try { await pending(); } catch (_) {} }
        return true;
      }
      default:
        return false;
    }
  }

  // ---- loops --------------------------------------------------------------
  _startLoops() {
    // reloj: re-render cada 10s para refrescar la hora de la status bar
    this._clockTimer = setInterval(() => this.requestRender(), 10000);
    // variables: refresca desde la API cada ~3s
    this._varTimer = setInterval(async () => {
      await this.vars.refresh().catch(() => {});
      // si estamos en una lista, re-rellena filas
      const frame = this.nav && this.nav.current();
      if (frame && frame.isList) {
        await this.nav._fillListRows(frame).catch(() => {});
      }
      this.requestRender();
    }, 3000);
  }

  stop() {
    clearInterval(this._clockTimer);
    clearInterval(this._varTimer);
  }
}

export default Pager;
