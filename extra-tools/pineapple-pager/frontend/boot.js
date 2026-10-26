/* =============================================================================
 * boot.js — Arranque del Virtual Pager + glue con la carcasa (chassis).
 * -----------------------------------------------------------------------------
 * - Instancia el motor (engine/pager.js) sobre <canvas id="display">.
 * - Conecta los botones fisicos de la carcasa (.hwbtn[data-key]) + teclado al
 *   navigator, con flash de LED y buzzer (feedback, como el Pager real).
 * - Mantiene fitPager (escalado de la carcasa amarilla + landscape) intacto.
 *
 * Mapeo de teclas fisicas -> el engine las traduce a a/b/up/down/left/right
 * segun el button_map del componente activo.
 * ========================================================================== */

'use strict';

import { api } from './api.js';
import { Pager } from './engine/pager.js';

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* ---- Buzzer (Web Audio): beeps de navegacion/seleccion/back -------------- */
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

/* ---- LED flash en el boton pulsado --------------------------------------- */
function flashLed(btnId) {
  const el = document.getElementById(btnId);
  if (!el) return;
  el.classList.add('lit', 'press');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('lit', 'press'), 160);
}

/* ---- Escalado de la carcasa -------------------------------------------------
 * ESTRATEGIA (ver tambien style.css):
 *   - La carcasa #pager tiene tamano NATURAL fijo (NAT_W x NAT_H px, ratio
 *     apaisado ~1.92:1) y se escala ENTERA con transform: scale(--sc). Esto
 *     hace el calculo trivial y estable: no depende del reflujo del contenido.
 *   - LANDSCAPE SIEMPRE: si el viewport esta en portrait, el CSS rota el #stage
 *     90deg dandole width=100vh/height=100vw. Las dimensiones VISUALES
 *     disponibles en landscape son entonces:
 *        ancho_visual = max(innerW, innerH)   (el lado largo)
 *        alto_visual  = min(innerW, innerH)   (el lado corto)
 *     Calculamos la escala con esas, independientemente de la orientacion
 *     fisica. Asi en el G82 (1080x2400 portrait) -> landscape 2400x1080.
 *   - FILL=0.98 deja un margen minimo. La carcasa queda GRANDE y centrada.
 * -------------------------------------------------------------------------- */
const NAT_W = 960, NAT_H = 500;   // deben coincidir con --nat-w/--nat-h del CSS
const FILL  = 0.98;               // porcentaje del area a llenar

function fitPager() {
  const pager = $('#pager');
  if (!pager) return;

  // Dimensiones VISUALES en landscape (lado largo = ancho, lado corto = alto),
  // sin importar si el dispositivo esta fisicamente en portrait o landscape.
  const iw = window.innerWidth  || document.documentElement.clientWidth  || 1;
  const ih = window.innerHeight || document.documentElement.clientHeight || 1;
  const availW = Math.max(iw, ih) * FILL;   // lado largo
  const availH = Math.min(iw, ih) * FILL;   // lado corto

  // escala que llena manteniendo aspect ratio; limite superior generoso (3x)
  // para poder agrandar en pantallas grandes, inferior para no colapsar.
  const sc = Math.max(0.1, Math.min(availW / NAT_W, availH / NAT_H, 3));
  pager.style.setProperty('--sc', sc.toFixed(4));
}
let _fitRaf = 0;
function fitThrottled() {
  if (_fitRaf) return;
  _fitRaf = requestAnimationFrame(() => { _fitRaf = 0; fitPager(); });
}

/* ---- key fisica -> id del LED + sonido ----------------------------------- */
const KEY_LED = {
  ArrowLeft: 'btn-left', ArrowRight: 'btn-right',
  ArrowUp: 'btn-up', ArrowDown: 'btn-down',
  Enter: 'btn-select', Escape: 'btn-back',
};

let pager = null;

function handleKey(key) {
  const led = KEY_LED[key];
  if (!led) return;
  flashLed(led);
  if (key === 'Enter') Buzzer.select();
  else if (key === 'Escape') Buzzer.back();
  else Buzzer.nav();
  if (pager) pager.press(key);
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
  const unlock = () => { Buzzer.nav(); window.removeEventListener('pointerdown', unlock); };
  window.addEventListener('pointerdown', unlock, { once: true });
}

async function init() {
  window.addEventListener('resize', fitThrottled);
  window.addEventListener('orientationchange', () => setTimeout(fitPager, 200));
  bindButtons();
  fitPager();
  setTimeout(fitPager, 100);

  const canvas = $('#display');
  pager = new Pager({
    canvas,
    api,
    themeId: 'wargames',
    baseFor: (id) => `community/themes/${id}/`,
  });
  window.__pager = pager; // debug en consola

  try {
    await pager.boot();
  } catch (e) {
    console.error('[boot] no se pudo arrancar el Virtual Pager:', e);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, 480, 222);
    ctx.fillStyle = '#f33'; ctx.font = '12px monospace';
    ctx.fillText('engine boot error: ' + e.message, 8, 20);
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
