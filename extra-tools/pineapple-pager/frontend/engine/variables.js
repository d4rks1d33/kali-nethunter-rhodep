/* =============================================================================
 * variables.js — Registro de variables $_ del display (nombre -> valor).
 * -----------------------------------------------------------------------------
 * El firmware resuelve variables $_XXX contra pineapd/system/uci. Aca las
 * resolvemos contra la API del backend (/api/status, /api/pineap, /api/recon,
 * /api/gps, /api/variables...) y un mock/cache local. Lo que no exista -> "".
 *
 * Tipos de variable:
 *   - escalar: string|number|bool   (ej: $_CLOCK_, $_BRIGHTNESS, toggles bool)
 *   - lista:   array                (ej: recon rows, $_PAYLOAD_CATEGORIES)
 *
 * El renderer de listas (recon_results_list) usa un CONTEXTO DE FILA:
 *   setRowContext(row) fija la fila actual; los $_RECON_RESULT_* leen de ella.
 *
 * API:
 *   vars.set(name, valueOrGetter)   registra (getter: ()=>value)
 *   vars.get(name)                  resuelve escalar (string)
 *   vars.getBool(name)              resuelve bool (para toggles)
 *   vars.getList(name)              resuelve array
 *   vars.setRowContext(obj|null)    contexto de fila para $_RECON_RESULT_*
 *   vars.refresh()                  recarga desde la API (async)
 * ========================================================================== */

'use strict';

export class Variables {
  constructor(api) {
    this.api = api || null;
    this.map = new Map();      // name -> value | getter
    this.overrides = new Map(); // name -> valor local (inline_toggle / edicion)
    this.row = null;           // contexto de fila actual (recon/listas)
    this.listState = {         // estado de la lista actual (generico)
      size: 0, page: 1, pages: 1, title: '',
    };
    this.snapshot = {          // cache de la ultima lectura de la API
      status: {}, pineap: {}, gps: {}, recon: { devicelist: [], stats: {} },
      payloads: { tree: {}, payloads: [], categories: [] },
      themes: [], ssidpool: [],
      settings: {}, alerts: [],
    };
    // Historial de PPS (packets per second) para el grafico de barras del
    // recon_dashboard. El firmware lo pinta nativo con una ventana de ~45s;
    // mantenemos una ventana rodante aqui (una muestra por refresh de recon).
    this.ppsHistory = [];
    this.ppsHistoryMax = 45;
    this.reconTotalPackets = 0;
    this._registerBuiltins();
  }

  // Estado de la lista actual (lo fija el pager en _resolveList).
  // Alimenta $_LIST_SIZE / $_LIST_PAGINATOR_LABEL_SHORT / $_INPUT_NAME.
  setListState(st) {
    this.listState = Object.assign(
      { size: 0, page: 1, pages: 1, title: '' }, st || {});
  }

  set(name, value) { this.map.set(name, value); }

  // escalar -> string (vacio si no existe)
  get(name) {
    const v = this._resolve(name);
    if (v == null) return '';
    return String(v);
  }

  // bool (para toggles / checkboxes / radios)
  getBool(name) {
    const v = this._resolve(name);
    if (typeof v === 'boolean') return v;
    if (typeof v === 'number') return v !== 0;
    if (typeof v === 'string') return /^(1|true|on|enabled|yes)$/i.test(v);
    return false;
  }

  // lista -> array
  getList(name) {
    const v = this._resolve(name);
    return Array.isArray(v) ? v : [];
  }

  setRowContext(row) { this.row = row || null; }

  _resolve(name) {
    // contexto de fila primero (recon/listas)
    if (this.row && name in this.row) return this.row[name];
    // override local (inline_toggle / edicion) tiene prioridad sobre el snapshot:
    // es el estado "optimista" que el usuario acaba de cambiar in-place.
    if (this.overrides.has(name)) return this.overrides.get(name);
    const e = this.map.get(name);
    if (e == null) return null;
    return (typeof e === 'function') ? e() : e;
  }

  // ---- store local de toggles / valores editados --------------------------
  // El inline_toggle y los dialogs de edicion escriben aca; el renderer lee via
  // _resolve (override gana al snapshot). Permite que el toggle visual cambie
  // IN-PLACE sin esperar una recarga de la API.

  // Setea un override local para una variable (cualquier tipo).
  setOverride(name, value) { if (name) this.overrides.set(name, value); }

  // Setea un override booleano y devuelve el nuevo valor.
  setToggle(name, value) {
    const v = !!value;
    this.setOverride(name, v);
    return v;
  }

  // Togglea una variable (lee el estado efectivo actual y lo invierte).
  // Devuelve el nuevo valor booleano.
  toggle(name) {
    const next = !this.getBool(name);
    this.setOverride(name, next);
    return next;
  }

  // Limpia un override (vuelve a leer del snapshot).
  clearOverride(name) { this.overrides.delete(name); }

  // Lee la API real y actualiza getters. Tolerante a fallos (deja lo que haya).
  async refresh() {
    if (!this.api) return;
    const pull = async (fn, key, map) => {
      try { this.snapshot[key] = map ? map(await fn()) : await fn(); } catch (_) {}
    };
    await Promise.all([
      this.api.getStatus    ? pull(() => this.api.getStatus(), 'status') : null,
      this.api.getPineap    ? pull(() => this.api.getPineap(), 'pineap') : null,
      this.api.getGps       ? pull(() => this.api.getGps(), 'gps') : null,
      this.api.getSettings  ? pull(() => this.api.getSettings(), 'settings') : null,
      this.api.getAlerts    ? pull(() => this.api.getAlerts(), 'alerts') : null,
      this.api.getReconDevices
        ? pull(() => this.api.getReconDevices({ sort: 'signal', asc: false }), 'recon')
        : null,
    ].filter(Boolean));

    // Stats de recon (PPS). Endpoint opcional (/api/recon/stats): si no existe,
    // devuelve {} y mantenemos la historia en 0. Alimenta el bar-graph (BUG 3).
    if (this.api.getReconStats) {
      try {
        const st = await this.api.getReconStats();
        if (st && typeof st === 'object') {
          this.snapshot.recon.stats = st;
          this._pushPps(st);
        }
      } catch (_) {}
    }
  }

  // Agrega una muestra de PPS a la ventana rodante. Lee pps de varias claves
  // posibles del backend; si no hay, empuja 0 (grafico se renderiza vacio, no
  // inventamos trafico).
  _pushPps(stats) {
    let pps = 0;
    for (const k of ['pps', 'packets_per_second', 'packetsPerSecond', 'rate']) {
      if (stats[k] != null) { pps = Number(stats[k]) || 0; break; }
    }
    if (stats.total_packets != null) this.reconTotalPackets = Number(stats.total_packets) || this.reconTotalPackets;
    else if (stats.total != null) this.reconTotalPackets = Number(stats.total) || this.reconTotalPackets;
    this.ppsHistory.push(pps);
    if (this.ppsHistory.length > this.ppsHistoryMax) {
      this.ppsHistory.splice(0, this.ppsHistory.length - this.ppsHistoryMax);
    }
  }

  // Resumen para el bar-graph del recon_dashboard: historia + actual + total + avg.
  ppsSummary() {
    const hist = this.ppsHistory.slice();
    const current = hist.length ? hist[hist.length - 1] : 0;
    const sum = hist.reduce((a, b) => a + b, 0);
    const avg = hist.length ? Math.round(sum / hist.length) : 0;
    return { history: hist, current, avg, total: this.reconTotalPackets };
  }

  // Carga perezosa de datasets de listas (payloads/themes/ssidpool). Se llaman
  // al entrar al componente de lista; no se refrescan cada 3s (son pesados y
  // casi estaticos). force=true re-consulta aunque ya haya cache.
  async loadPayloads(force = false) {
    if (!this.api || !this.api.getPayloads) return this.snapshot.payloads;
    if (!force && this.snapshot.payloads.payloads.length) return this.snapshot.payloads;
    try {
      const r = await this.api.getPayloads();
      this.snapshot.payloads = {
        tree: r.tree || {},
        payloads: r.payloads || [],
        categories: r.categories || Object.keys(r.tree || {}),
      };
    } catch (_) {}
    return this.snapshot.payloads;
  }

  async loadThemes(force = false) {
    if (!this.api || !this.api.getThemes) return this.snapshot.themes;
    if (!force && this.snapshot.themes.length) return this.snapshot.themes;
    try {
      const r = await this.api.getThemes();
      this.snapshot.themes = Array.isArray(r) ? r : (r.themes || []);
    } catch (_) {}
    return this.snapshot.themes;
  }

  async loadSsidPool(force = false) {
    if (!this.api || !this.api.getSsidPool) return this.snapshot.ssidpool;
    try {
      const r = await this.api.getSsidPool();
      this.snapshot.ssidpool = Array.isArray(r) ? r : (r.ssids || []);
    } catch (_) {}
    return this.snapshot.ssidpool;
  }

  // --------------------------------------------------------------------------
  // Builtins: getters que leen del snapshot. Nombres segun DISPLAY-ENGINE.md.
  // --------------------------------------------------------------------------
  _registerBuiltins() {
    const S = () => this.snapshot;

    // ---- Status / sistema -------------------------------------------------
    this.set('$_CLOCK_', () => {
      const t = S().status.time;
      if (t) return t;
      const d = new Date();
      let h = d.getHours(); const ap = h >= 12 ? 'PM' : 'AM';
      h = h % 12 || 12;
      return `${String(h).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')} ${ap}`;
    });
    this.set('$_BATTERY_INFO', () => {
      const s = S().status;
      const pct = s.battery != null ? s.battery : s.battery_pct;
      return pct != null ? `${pct}%` : '';
    });
    this.set('$_BRIGHTNESS', () => this._pct(S().status.brightness, S().settings.brightness));
    this.set('$_DIM_BRIGHTNESS', () => this._pct(S().settings.dim_brightness));
    this.set('$_VOLUME', () => this._pct(S().status.volume, S().settings.volume));
    this.set('$_VIBRATE', () => this._b(S().status.vibrate, S().settings.vibrate));
    this.set('$_HOSTNAME', () => S().status.hostname || '');
    this.set('$_UPTIME', () => S().status.uptime || '');
    this.set('$_FIRMWARE_VERSION', () => S().status.firmware || S().status.version || '');
    this.set('$_SCREEN_OFF_TIMEOUT', () => S().settings.screen_timeout || '');
    this.set('$_DIM_TIMEOUT', () => S().settings.dim_timeout || '');
    this.set('$_BLUETOOTH_ENABLED', () => this._b(S().status.bluetooth));
    this.set('$_THEME', () => S().status.theme || '');

    // ---- PineAP -----------------------------------------------------------
    const pineap = (k) => () => this._b(S().pineap[k]);
    this.set('$_PINEAP_IMPERSONATE_NETWORKS', pineap('impersonate'));
    this.set('$_PINEAP_COLLECT_NETWORKS', pineap('collect'));
    this.set('$_PINEAP_ADVERTISE_NETWORKS', pineap('advertise'));
    this.set('$_PINEAP_RANDOMIZE_MAC_ADDRESS', pineap('randomize_mac'));
    this.set('$_PINEAP_COLLECT_HANDSHAKES', pineap('collect_handshakes'));
    this.set('$_OPEN_AP_ENABLED', pineap('open_ap'));
    this.set('$_OPEN_AP_HIDDEN', pineap('open_ap_hidden'));
    this.set('$_OPEN_AP_SSID', () => S().pineap.open_ap_ssid || '');
    this.set('$_EVIL_WPA_ENABLED', pineap('evil_wpa'));
    this.set('$_EVIL_WPA_HIDDEN', pineap('evil_wpa_hidden'));
    this.set('$_EVIL_WPA_SSID', () => S().pineap.evil_wpa_ssid || '');
    this.set('$_FILTER_CLIENT_MODE', pineap('filter_client_allow'));
    this.set('$_PINEAP_SSID_POOL_LIST', () => S().pineap.ssid_pool || []);
    this.set('$_PINEAPPLE_CLIENTS', () => S().pineap.clients || []);

    // ---- Recon ------------------------------------------------------------
    this.set('$_RECON_LIST_SIZE', () => {
      const n = (S().recon.devicelist || []).length;
      return `${n} APs`;
    });
    this.set('$_RECON_LIST_AUTOUPDATING', () => this._b(S().recon.autoupdating));
    this.set('$_RECON_LIST_CURRENT_PAGE', () => S().recon._page || 1);
    this.set('$_RECON_LIST_TOTAL_PAGES', () => S().recon._pages || 1);
    this.set('$_RECON_LIST_PAGINATOR_LABEL_SHORT', () => {
      const p = S().recon._page || 1, t = S().recon._pages || 1;
      return `${p}/${t}`;
    });
    // $_RECON_RESULT_* se resuelven via contexto de fila (setRowContext).

    // ---- Listas genericas (payloads / themes / ssidpool / recon) ----------
    // Alimentadas por listState (lo fija el pager en _resolveList). $_LIST_ITEM
    // es por-fila (lo provee el contexto de fila / el row object del slot).
    this.set('$_LIST_SIZE', () => `${this.listState.size || 0}`);
    this.set('$_LIST_CURRENT_PAGE', () => this.listState.page || 1);
    this.set('$_LIST_TOTAL_PAGES', () => this.listState.pages || 1);
    this.set('$_LIST_PAGINATOR_LABEL_SHORT', () => {
      const p = this.listState.page || 1, t = this.listState.pages || 1;
      return `${p}/${t}`;
    });
    // Titulo de la pantalla de lista (ej: nombre de la categoria elegida).
    this.set('$_INPUT_NAME', () => this.listState.title || '');
    // $_LIST_ITEM solo existe en contexto de fila; vacio fuera de una fila.
    this.set('$_LIST_ITEM', () => (this.row && this.row['$_LIST_ITEM']) || '');

    // ---- GPS --------------------------------------------------------------
    this.set('$_GPS_LAT', () => fmtNum(S().gps.lat));
    this.set('$_GPS_LON', () => fmtNum(S().gps.lon));

    // ---- Payloads / alerts (listas) --------------------------------------
    this.set('$_PAYLOAD_CATEGORIES', () => S().status.payload_categories || []);
    this.set('$_ALERTS_LIST', () => S().alerts || []);

    // ---- Alerts toggles (tipos de alerta habilitados) ---------------------
    // Cada $_ALERTS_* es un bool que indica si ese tipo de alerta esta activo.
    // El backend los expone en /api/alerts (objeto) o /api/settings. Default:
    // activados (el firmware viene con todos on). Los overrides del inline_toggle
    // ganan al snapshot (ver _resolve).
    const alertFlag = (key) => () => {
      const a = S().alerts;
      // alerts puede ser { enabled: {...} } | { new_client:bool,... } | []
      if (a && !Array.isArray(a)) {
        if (a.enabled && key in a.enabled) return a.enabled[key];
        if (key in a) return a[key];
      }
      const st = S().settings || {};
      if (st['$_ALERTS_' + key.toUpperCase()] != null) return st['$_ALERTS_' + key.toUpperCase()];
      return true; // default on
    };
    this.set('$_ALERTS_NEW_CLIENT', alertFlag('new_client'));
    this.set('$_ALERTS_AUTH_CAPTURED', alertFlag('auth_captured'));
    this.set('$_ALERTS_HANDSHAKE_CAPTURE', alertFlag('handshake_capture'));
    this.set('$_ALERTS_DEAUTH_FLOOD', alertFlag('deauth_flood'));

    // ---- Input ------------------------------------------------------------
    this.set('$_INPUT', () => S().status._input || '');
  }

  _pct(...vals) {
    for (const v of vals) if (v != null) return `${v}%`;
    return '';
  }
  _b(...vals) {
    for (const v of vals) if (v != null) return v;
    return false;
  }
}

function fmtNum(n) {
  if (n == null || n === '') return '';
  const f = Number(n);
  return Number.isFinite(f) ? f.toFixed(5) : String(n);
}

export default Variables;
