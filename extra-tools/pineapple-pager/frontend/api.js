/* =============================================================================
 * api.js — capa REST del Virtual Pager hacia el backend (pager-server.py).
 * -----------------------------------------------------------------------------
 * El motor de render (engine/) consume estos metodos para alimentar las
 * variables $_ y disparar acciones. Todos devuelven objetos JS ya parseados.
 * Base = mismo origen que sirve el frontend.
 * ========================================================================== */
'use strict';

const BASE = '';

async function _get(path) {
  try {
    const r = await fetch(BASE + path, { headers: { Accept: 'application/json' } });
    if (!r.ok) return {};
    return await r.json();
  } catch (e) {
    return {};
  }
}

async function _post(path, body) {
  try {
    const r = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) return { error: 1 };
    return await r.json();
  } catch (e) {
    return { error: 1, message: String(e) };
  }
}

export const api = {
  // estado / status bar
  getStatus:       ()        => _get('/api/status'),
  getVariables:    ()        => _get('/api/variables'),
  // recon
  getReconDevices: (q = {})  => _get('/api/recon/devices?' + new URLSearchParams(q)),
  getReconStats:   ()        => _get('/api/recon/stats'),
  startRecon:      ()        => _post('/api/recon/start'),
  stopRecon:       ()        => _post('/api/recon/stop'),
  // pineap / karma
  getPineap:       ()        => _get('/api/pineap'),
  setPineap:       (p)       => _post('/api/pineap', p),
  // ssid pool
  getSsidPool:     ()        => _get('/api/ssidpool'),
  // gps / alerts / settings
  getGps:          ()        => _get('/api/gps'),
  getAlerts:       ()        => _get('/api/alerts'),
  getSettings:     ()        => _get('/api/settings'),
  setSettings:     (p)       => _post('/api/settings', p),
  // temas
  getThemes:       ()        => _get('/api/themes'),
  getTheme:        ()        => _get('/api/theme'),
  setTheme:        (id)      => _post('/api/theme', { id }),
  // ringtones / payloads
  getRingtones:    ()        => _get('/api/ringtones'),
  playRingtone:    (name)    => _post('/api/ringtone/play', { name }),
  getPayloads:     ()        => _get('/api/payloads'),
  runPayload:      (path)    => _post('/api/payload/run', { path }),
  // function_* targets del firmware (best-effort)
  callFunction:    (name, item) => _post('/api/function/' + encodeURIComponent(name), { item: item && item.id }),
};

export default api;
