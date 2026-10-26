/* =============================================================================
 * app.js — VIEWER del WiFi Pineapple Pager (firmware REAL bajo qemu)
 * -----------------------------------------------------------------------------
 * Pinta el display del firmware real dentro de la carcasa amarilla y navega el
 * firmware con los botones tactiles. Protocolo (RE confirmado, ver
 * ../VIRTUAL_PAGER_REPORT.md):
 *
 *   Login:  POST  {BASE}/api/login {"username":"root","password":PW}
 *                 -> {"token": "<b64url(json).b64sig>"}
 *           ServerId = base64url(token.split('.')[0]).ServerId
 *           cookie  = AUTH_<ServerId>=<token>   (path=/, 12h)
 *           GET   {BASE}/api/api_ping  valida (200/401).
 *   Screen: ws  {WSBASE}/api/pager/display/screen.ws  (cookie AUTH)
 *                 -> frames binarios RGBA raw (Go image.RGBA Pix)
 *                    480x222x4 = 426240 bytes/frame -> putImageData.
 *   Input:  ws  {WSBASE}/api/pager/input/keys.ws  (cookie AUTH)
 *                 <- TEXTO plano JS KeyboardEvent.key:
 *                    "ArrowUp"/"ArrowDown"/"ArrowLeft"/"ArrowRight"
 *                    "Enter" (=A) / "Escape" (=B)
 *
 * ---------------------------------------------------------------------------
 * CORS / COOKIES (lo importante):
 *   El navegador NO permite setear headers en el handshake de un WebSocket.
 *   Para que screen.ws / keys.ws lleven la cookie AUTH, esta tiene que ser una
 *   cookie REAL del navegador que se mande automaticamente -> eso solo pasa si
 *   el WS es del MISMO ORIGEN que la cookie. Por eso:
 *
 *   MODO A (recomendado, "same-origin"): servir esta app DESDE EL FIRMWARE o
 *     desde un proxy en el MISMO origen que 1471. Entonces:
 *       - login via fetch(credentials:'include') setea AUTH_<sid> same-origin,
 *       - el ws://MISMO_ORIGEN/... manda esa cookie solo.
 *     BASE = '' (same-origin). Es lo que hace proxy.py.
 *
 *   MODO B (cross-origin, p.ej. python -m http.server en :8000 apuntando a un
 *     firmware en 1471): el navegador NO mandara la cookie en el WS cross-site
 *     y ademas el fetch de login choca con CORS/SameSite. NO es fiable desde el
 *     navegador. Solucion: usar proxy.py (MODO A) o servir la app desde 1471.
 *
 *   Config del host: ?host=IP:PUERTO en la URL, o la constante DEFAULT_HOST.
 *     - Sin ?host  -> same-origin (BASE='') : usar con proxy.py o embebido.
 *     - Con  ?host=127.0.0.1:1471 -> apunta directo (solo funciona si ese
 *       origen coincide con el de la pagina, si no, ver MODO B).
 * ========================================================================== */

'use strict';

/* --------------------------------------------------------------------------
 * Config
 * ------------------------------------------------------------------------ */
// Host por defecto cuando se abre la app sin ?host=. Vacio => same-origin
// (la app se sirve desde el firmware o desde proxy.py en el mismo puerto).
const DEFAULT_HOST = '';          // '' = same-origin ; o p.ej '127.0.0.1:1471'
const DEFAULT_PASSWORD = 'pineapplepager';

const SCREEN_W = 480, SCREEN_H = 222;
const FRAME_BYTES = SCREEN_W * SCREEN_H * 4;   // 426240

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* Resuelve el host/origen a usar. */
function resolveBases() {
  const params = new URLSearchParams(location.search);
  const host = params.get('host') || DEFAULT_HOST;
  if (!host) {
    // same-origin: usa el origen de la pagina (proxy.py o firmware embebido)
    const httpBase = location.origin;                      // http(s)://host:port
    const wsProto  = location.protocol === 'https:' ? 'wss' : 'ws';
    const wsBase   = `${wsProto}://${location.host}`;
    return { httpBase, wsBase, label: location.host + ' (same-origin)' };
  }
  // host explicito: http://host  + ws://host (mismo esquema de puerto)
  const httpBase = `http://${host}`;
  const wsBase   = `ws://${host}`;
  return { httpBase, wsBase, label: host };
}

const BASES = resolveBases();

/* --------------------------------------------------------------------------
 * UI: overlay, toast
 * ------------------------------------------------------------------------ */
const overlay = $('#overlay');
const ovMsg   = $('#ov-msg');
const ovForm  = $('#ov-form');
const ovPass  = $('#ov-pass');
const ovHost  = $('#ov-host');
ovHost.textContent = BASES.label;

function showOverlay(msg, { error = false, form = false } = {}) {
  overlay.classList.remove('hidden');
  ovMsg.textContent = msg;
  ovMsg.classList.toggle('err', error);
  ovForm.hidden = !form;
  if (form) setTimeout(() => ovPass.focus(), 50);
}
function hideOverlay() { overlay.classList.add('hidden'); }

let _toastT = 0;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(_toastT);
  _toastT = setTimeout(() => t.classList.remove('show'), 1600);
}

/* --------------------------------------------------------------------------
 * Buzzer (Web Audio) + LED flash — feedback como el frontend viejo
 * ------------------------------------------------------------------------ */
const Buzzer = (() => {
  let ctx = null, enabled = true;
  const ensure = () => (ctx || (ctx = new (window.AudioContext || window.webkitAudioContext)()));
  function beep(freq = 880, dur = 0.04, type = 'square', vol = 0.06) {
    if (!enabled) return;
    try {
      const ac = ensure();
      if (ac.state === 'suspended') ac.resume();
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = type; o.frequency.value = freq; g.gain.value = vol;
      o.connect(g); g.connect(ac.destination);
      const t = ac.currentTime;
      o.start(t);
      g.gain.setValueAtTime(vol, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.stop(t + dur);
    } catch (_) {}
  }
  return { nav: () => beep(760, 0.03), select: () => beep(1180, 0.06), back: () => beep(440, 0.05) };
})();

function flashLed(btnId) {
  const el = document.getElementById(btnId);
  if (!el) return;
  el.classList.add('lit', 'press');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('lit', 'press'), 160);
}

/* --------------------------------------------------------------------------
 * Escalado de la carcasa — LLENA el landscape del telefono (~2340x1080).
 * -----------------------------------------------------------------------------
 * La carcasa es 1040x480 (ratio 2.1667), que coincide con 2340x1080 (2.1667):
 * el escalado min(vw/natW, vh/natH) llena casi sin letterbox. Usamos FILL=1
 * (sin margen) para eliminar las franjas negras; el borde negro visible queda
 * solo por el border-radius de la carcasa (marco fino uniforme, no franjas).
 * NAT_* DEBE coincidir con el width/height reales del #pager en el CSS.
 * ------------------------------------------------------------------------ */
const NAT_W = 1040, NAT_H = 480, FILL = 1.0;
function fitPager() {
  const pager = $('#pager');
  if (!pager) return;
  const iw = window.innerWidth  || document.documentElement.clientWidth  || 1;
  const ih = window.innerHeight || document.documentElement.clientHeight || 1;
  const availW = Math.max(iw, ih) * FILL;   // lado largo (landscape)
  const availH = Math.min(iw, ih) * FILL;   // lado corto
  // max() => OVERSCAN: la carcasa cubre TODO el viewport aunque el ratio del
  // panel no sea exactamente el de la carcasa. Asi no quedan franjas negras;
  // a lo sumo se recorta un pelin de las esquinas redondeadas (imperceptible).
  const sc = Math.max(0.1, Math.min(Math.max(availW / NAT_W, availH / NAT_H), 4));
  pager.style.setProperty('--sc', sc.toFixed(4));
}
let _fitRaf = 0;
function fitThrottled() {
  if (_fitRaf) return;
  _fitRaf = requestAnimationFrame(() => { _fitRaf = 0; fitPager(); });
}

/* --------------------------------------------------------------------------
 * Auth: login + cookie
 * ------------------------------------------------------------------------ */
function b64urlDecode(s) {
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  s += '='.repeat((4 - (s.length % 4)) % 4);
  return atob(s);
}

/* ServerId a partir del token ("<b64url(json)>.<b64sig>"). */
function serverIdFromToken(token) {
  const hdr = token.split('.')[0];
  const json = JSON.parse(b64urlDecode(hdr));
  return json.ServerId;
}

/* Setea la cookie AUTH_<sid> same-origin (12h, path=/). */
function setAuthCookie(sid, token) {
  const maxAge = 12 * 3600;
  const secure = location.protocol === 'https:' ? '; Secure' : '';
  document.cookie = `AUTH_${sid}=${token}; Path=/; Max-Age=${maxAge}; SameSite=Lax${secure}`;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* POST /api/login -> {token}. Devuelve {token, sid}.
 * Reintenta ante cortes transitorios: errores de red (firmware/proxy reiniciando
 * la conexion) y respuestas 502/503/504 (el proxy no pudo reenviar porque el
 * firmware estaba ocupado/levantando). Un 401/403 (credencial mala) NO se
 * reintenta: se propaga de una. Asi el viewer se recupera solo de un 502
 * pasajero en vez de mostrar "login HTTP 502" al toque. */
async function login(password, { retries = 4, backoff = 300 } = {}) {
  let delay = backoff;
  let lastErr = null;
  for (let attempt = 0; attempt < retries; attempt++) {
    let r;
    try {
      r = await fetch(`${BASES.httpBase}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: 'root', password }),
        credentials: 'include',
      });
    } catch (netErr) {
      // fallo de red: transitorio -> reintentar
      lastErr = netErr;
      if (attempt < retries - 1) { await sleep(delay); delay *= 2; continue; }
      throw new Error(`login sin red: ${netErr.message}`);
    }
    if (r.ok) {
      const data = await r.json();
      if (!data || !data.token) throw new Error('login: sin token en la respuesta');
      const sid = serverIdFromToken(data.token);
      return { token: data.token, sid };
    }
    // 502/503/504 = el firmware estaba ocupado/levantando -> reintentar
    if ((r.status === 502 || r.status === 503 || r.status === 504) && attempt < retries - 1) {
      lastErr = new Error(`login HTTP ${r.status}`);
      await sleep(delay); delay *= 2; continue;
    }
    // cualquier otro (401/403/...) o reintentos agotados: fallar
    throw new Error(`login HTTP ${r.status}`);
  }
  throw lastErr || new Error('login fallo');
}

/* Valida la sesion. */
async function apiPing() {
  try {
    const r = await fetch(`${BASES.httpBase}/api/api_ping`, { credentials: 'include' });
    return r.ok;
  } catch (_) { return false; }
}

/* --------------------------------------------------------------------------
 * Screen WS: frames RGBA -> canvas
 * ------------------------------------------------------------------------ */
const canvas = $('#display');
const cctx = canvas.getContext('2d', { alpha: false });
const imageData = cctx.createImageData(SCREEN_W, SCREEN_H);
const statusLed = $('#status-led');

let screenWS = null;
let framesSeen = 0;
let _liveBlinkT = 0;

function markLive() {
  statusLed.classList.add('active');
  clearTimeout(_liveBlinkT);
  _liveBlinkT = setTimeout(() => statusLed.classList.remove('active'), 220);
}

function drawFrame(buf) {
  // buf: ArrayBuffer de 426240 bytes RGBA. Se copia tal cual al ImageData.
  const u8 = buf.byteLength === FRAME_BYTES ? new Uint8ClampedArray(buf)
                                            : new Uint8ClampedArray(buf, 0, FRAME_BYTES);
  imageData.data.set(u8);
  cctx.putImageData(imageData, 0, 0);
  framesSeen++;
  markLive();
}

function openScreen() {
  const url = `${BASES.wsBase}/api/pager/display/screen.ws`;
  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  screenWS = ws;

  ws.addEventListener('open', () => {
    console.log('[screen.ws] abierto', url);
    _reconnectAttempt = 0;   // conexion viva: reset del backoff de reconexion
    hideOverlay();
    toast('display conectado');
  });
  ws.addEventListener('message', (ev) => {
    if (typeof ev.data === 'string') {
      console.log('[screen.ws] text:', ev.data.slice(0, 120));
      return;
    }
    if (ev.data.byteLength >= FRAME_BYTES) {
      drawFrame(ev.data);
    } else {
      console.warn('[screen.ws] frame chico', ev.data.byteLength);
    }
  });
  ws.addEventListener('close', (ev) => {
    console.warn('[screen.ws] cerrado', ev.code, ev.reason);
    statusLed.classList.remove('active');
    showOverlay('display desconectado — reconectando…');
    scheduleReconnect();
  });
  ws.addEventListener('error', (e) => {
    console.error('[screen.ws] error', e);
  });
}

/* --------------------------------------------------------------------------
 * Keys WS: envia KeyboardEvent.key en texto plano
 * ------------------------------------------------------------------------ */
let keysWS = null;
let keysReady = false;

function openKeys() {
  const url = `${BASES.wsBase}/api/pager/input/keys.ws`;
  const ws = new WebSocket(url);
  keysWS = ws;
  ws.addEventListener('open', () => { keysReady = true; console.log('[keys.ws] abierto', url); });
  ws.addEventListener('close', () => { keysReady = false; console.warn('[keys.ws] cerrado'); });
  ws.addEventListener('error', (e) => { console.error('[keys.ws] error', e); });
}

function sendKey(key) {
  if (keysWS && keysReady && keysWS.readyState === WebSocket.OPEN) {
    keysWS.send(key);
    return true;
  }
  return false;
}

/* --------------------------------------------------------------------------
 * Reconexion
 * ------------------------------------------------------------------------ */
let _reconnectT = 0;
let _reconnectAttempt = 0;
const _RECONNECT_MAX_DELAY = 10000;  // tope del backoff (ms)

/* Reconexion con backoff exponencial. Un corte transitorio (firmware ocupado,
 * reinicio interno, token recien expirado, 502 pasajero del proxy) ya NO tira
 * el error de una: reintentamos indefinidamente con espera creciente y solo
 * mostramos un overlay "reconectando" informativo. El usuario no ve el 502. */
function scheduleReconnect() {
  clearTimeout(_reconnectT);
  const delay = Math.min(1200 * Math.pow(2, _reconnectAttempt), _RECONNECT_MAX_DELAY);
  _reconnectT = setTimeout(async () => {
    try {
      // revalida sesion; si expiro, re-login con el pass guardado (login ya reintenta)
      const ok = await apiPing();
      if (!ok) {
        await ensureAuth(lastPassword);
      }
      _reconnectAttempt = 0;        // exito: reset del backoff
      connectStreams();
    } catch (e) {
      _reconnectAttempt++;
      const nextS = Math.round(Math.min(1200 * Math.pow(2, _reconnectAttempt), _RECONNECT_MAX_DELAY) / 1000);
      // overlay informativo, SIN form de error: seguimos intentando solos
      showOverlay(`reconectando… (intento ${_reconnectAttempt}, reintento en ${nextS}s)`);
      scheduleReconnect();          // reintentar, no rendirse
    }
  }, delay);
}

/* --------------------------------------------------------------------------
 * Botones / teclado
 * ------------------------------------------------------------------------ */
const KEY_LED = {
  ArrowLeft: 'btn-left', ArrowRight: 'btn-right',
  ArrowUp: 'btn-up', ArrowDown: 'btn-down',
  Enter: 'btn-select', Escape: 'btn-back',
};

function handleKey(key) {
  const led = KEY_LED[key];
  if (!led) return;
  flashLed(led);
  if (key === 'Enter') Buzzer.select();
  else if (key === 'Escape') Buzzer.back();
  else Buzzer.nav();
  const sent = sendKey(key);
  if (!sent) toast('sin conexion de input');
}

function bindButtons() {
  $$('.hwbtn[data-key]').forEach((btn) => {
    const key = btn.dataset.key;
    btn.addEventListener('click', (e) => { e.preventDefault(); handleKey(key); });
    btn.addEventListener('touchstart', () => btn.classList.add('press'), { passive: true });
    btn.addEventListener('touchend', () => btn.classList.remove('press'), { passive: true });
  });
  window.addEventListener('keydown', (e) => {
    if (KEY_LED[e.key]) { e.preventDefault(); handleKey(e.key); }
  });
  // desbloqueo del AudioContext al primer gesto
  const unlock = () => { Buzzer.nav(); window.removeEventListener('pointerdown', unlock); };
  window.addEventListener('pointerdown', unlock, { once: true });
}

/* --------------------------------------------------------------------------
 * LED bridge — refleja el estado REAL de los LEDs que controla el firmware.
 * -----------------------------------------------------------------------------
 * El firmware del Pager escribe el color del D-PAD como brillo por-canal en
 * sysfs (/sys/class/leds/<pos>-led-<red|green>/brightness), que el qemu
 * parcheado redirige al rootfs. El proxy expone ese estado REAL en GET /leds.
 * Aqui lo consultamos periodicamente y pintamos el color en cada LED del
 * viewer, de forma que el color que el usuario elige en Settings ("D-PAD LED
 * Color") se refleja fielmente en la carcasa.
 *
 * Hardware real: el D-PAD solo tiene LEDs ROJO+VERDE (sin azul), y los botones
 * A/SELECT (verde) y B/BACK (rojo) tienen un LED propio. El firmware controla:
 *   - up/down/left/right : color RGB (r,g; b=0) -> lo pintamos tal cual.
 *   - a-button-led / b-button-led : on/off (select/back) -> encendido fijo.
 * El flash local al presionar (flashLed) se mantiene como feedback encima del
 * color base que manda el firmware.
 * ------------------------------------------------------------------------ */
const LED_POLL_MS = 400;
let _ledPollT = 0;
let _ledLastJSON = '';

/* Pinta un .led-dot (dpad/flechas) con el color real del firmware.
 * El firmware del Pager maneja cada canal del D-pad como ON/OFF (escribe
 * brightness 0 o 1, no un gradiente), asi que un canal "encendido" (r/g/b > 0)
 * se pinta a plena intensidad (255). El D-pad fisico solo tiene ROJO+VERDE, por
 * eso el canal azul NUNCA se enciende (proxy.py fuerza b=0): un color con azul
 * (blue/cyan/magenta/white) se muestra con su componente R+G, que es justo lo
 * que un D-pad rojo+verde puede representar. Si esta apagado, vuelve al reposo. */
function paintDot(el, c) {
  if (!el) return;
  if (c && c.on) {
    // on/off por canal -> intensidad plena (el hw es rojo/verde a tope o nada)
    const r = c.r > 0 ? 255 : 0;
    const g = c.g > 0 ? 255 : 0;
    const b = c.b > 0 ? 255 : 0;
    const rgb = `${r},${g},${b}`;
    el.style.background = `rgb(${rgb})`;
    el.style.boxShadow = `0 0 8px rgba(${rgb},.95), inset 0 0 2px #fff`;
    el.classList.add('fw-lit');
  } else {
    el.style.background = '';
    el.style.boxShadow = '';
    el.classList.remove('fw-lit');
  }
}

/* Aplica el JSON de /leds a los LEDs del viewer. */
function applyLeds(data) {
  if (!data || !data.dpad) return;
  const d = data.dpad;
  // dpad / flechas: el .led-dot dentro de cada boton
  const map = {
    up: '#btn-up', down: '#btn-down', left: '#btn-left', right: '#btn-right',
  };
  for (const pos of Object.keys(map)) {
    const btn = document.querySelector(map[pos]);
    if (!btn) continue;
    paintDot(btn.querySelector('.led-dot'), d[pos]);
  }
  // botones A/SELECT (verde) y B/BACK (rojo): on/off del firmware
  const btns = data.buttons || {};
  const sel = document.getElementById('btn-select');
  const back = document.getElementById('btn-back');
  if (sel)  sel.classList.toggle('fw-on', !!(btns.a && btns.a.on));
  if (back) back.classList.toggle('fw-on', !!(btns.b && btns.b.on));
}

async function pollLeds() {
  try {
    const r = await fetch(`${BASES.httpBase}/leds`, { cache: 'no-store' });
    if (r.ok) {
      const txt = await r.text();
      if (txt !== _ledLastJSON) {          // solo repintar si cambio
        _ledLastJSON = txt;
        applyLeds(JSON.parse(txt));
      }
    }
  } catch (_) { /* proxy caido / sin /leds: dejar los LEDs como esten */ }
}

function startLedPoller() {
  clearInterval(_ledPollT);
  pollLeds();
  _ledPollT = setInterval(pollLeds, LED_POLL_MS);
}

/* --------------------------------------------------------------------------
 * Boton de POWER — enciende/apaga el stack del Pager (menos este viewer).
 * Pega a /power/{on,off,status} en el proxy (NO al firmware).
 * ------------------------------------------------------------------------ */
let powerState = null;   // 'on' | 'off' | null (desconocido)

async function powerQuery(action) {
  const r = await fetch(`${BASES.httpBase}/power/${action}`, {
    method: 'POST', cache: 'no-store',
  });
  const j = await r.json().catch(() => ({ ok: false, state: '' }));
  return j;
}

function reflectPower(state) {
  powerState = state;
  const btn = document.getElementById('btn-power');
  if (!btn) return;
  btn.classList.remove('busy');
  btn.classList.toggle('on', state === 'on');
  btn.classList.toggle('off', state === 'off');
  btn.title = state === 'on'
    ? 'Pager encendido — tocar para apagar y liberar el hardware'
    : 'Pager apagado — tocar para encender';
}

async function refreshPowerState() {
  try { const j = await powerQuery('status'); reflectPower(j.state || 'off'); }
  catch (_) { /* proxy caido? dejar como esta */ }
}

function bindPowerButton() {
  const btn = document.getElementById('btn-power');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    if (btn.classList.contains('busy')) return;
    const goingOff = (powerState !== 'off');  // si no sabemos, asumimos apagar
    btn.classList.add('busy');
    Buzzer.select();
    try {
      if (goingOff) {
        showOverlay('apagando el Pager… (liberando radios y recursos)');
        try { screenWS && screenWS.close(); } catch (_) {}
        try { keysWS && keysWS.close(); } catch (_) {}
        const j = await powerQuery('off');
        reflectPower(j.state || 'off');
        showOverlay('Pager apagado. Tocá el botón de power para encender.');
      } else {
        showOverlay('encendiendo el Pager…');
        const j = await powerQuery('on');
        reflectPower(j.state || 'on');
        // el firmware tarda unos segundos en levantar; el flujo de reconexion
        // (login + streams con backoff) se encarga de volver a mostrar la UI.
        setTimeout(() => connectFlow(lastPassword), 1500);
      }
    } catch (e) {
      toast('power error: ' + (e.message || e));
      btn.classList.remove('busy');
    }
  });
  refreshPowerState();
}

/* --------------------------------------------------------------------------
 * Flujo principal
 * ------------------------------------------------------------------------ */
let lastPassword = DEFAULT_PASSWORD;

async function ensureAuth(password) {
  // si ya hay sesion valida (cookie viva), no re-login
  if (await apiPing()) return;
  const { token, sid } = await login(password);
  setAuthCookie(sid, token);
  lastPassword = password;
  // pequena espera para que la cookie este disponible al abrir el WS
  await new Promise((r) => setTimeout(r, 50));
  if (!(await apiPing())) {
    // En cross-origin la cookie podria NO viajar: avisar claramente.
    throw new Error('auth no valida tras login (posible CORS/cookie cross-origin: usar proxy.py)');
  }
}

function connectStreams() {
  // cierra previos
  try { screenWS && screenWS.close(); } catch (_) {}
  try { keysWS && keysWS.close(); } catch (_) {}
  openScreen();
  openKeys();
}

async function connectFlow(password) {
  showOverlay('autenticando…');
  try {
    await ensureAuth(password);
  } catch (e) {
    console.error('[auth]', e);
    showOverlay('login fallo: ' + e.message, { error: true, form: true });
    return;
  }
  showOverlay('abriendo display…');
  connectStreams();
}

function bindLoginForm() {
  ovForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const pw = ovPass.value || DEFAULT_PASSWORD;
    connectFlow(pw);
  });
}

async function init() {
  window.addEventListener('resize', fitThrottled);
  window.addEventListener('orientationchange', () => setTimeout(fitPager, 200));
  bindButtons();
  bindLoginForm();
  bindPowerButton();
  startLedPoller();            // espejo de los LEDs reales del firmware
  fitPager();
  setTimeout(fitPager, 100);

  // pinta un fondo negro inicial en el canvas
  cctx.fillStyle = '#000'; cctx.fillRect(0, 0, SCREEN_W, SCREEN_H);

  // debug en consola
  window.__pager = {
    sendKey, connectStreams, connectFlow,
    get frames() { return framesSeen; },
    bases: BASES,
  };

  // arranca con el pass por defecto; si falla, el overlay pide password
  connectFlow(DEFAULT_PASSWORD);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
