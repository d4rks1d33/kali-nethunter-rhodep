/* =============================================================================
 * renderer.js — EL CORAZON. Renderiza un componente a un canvas 480x222 con el
 * z-order exacto del display fisico (DISPLAY-ENGINE.md seccion "Render").
 * -----------------------------------------------------------------------------
 * z-order (bottom -> top):
 *   1. background.background_color    (rellena 480x222)
 *   2. background.layers[]            (painter order)
 *   3. menu_items[] de la pagina activa  (item enfocado usa selected_layers[])
 *   4. status bar (via statusbar.js, dibujada por el pager por encima)
 *
 * Layer dispatch (una layer es una union):
 *   - image_path (+recolor_palette)                 -> dibuja PNG tintado
 *   - text (+text_color_palette,text_size,...)      -> texto estatico (PixFont)
 *   - variable_name $_XXX + plantilla:
 *       use_template: "<toggle|signal>"   -> template nombrado del tema
 *       toggle_template: {...} inline       -> enabled/disabled por bool
 *       signal: {...} / use_template signal -> por RSSI (thresholds)
 *       string_template: {...} inline       -> texto del valor de la variable
 *   Offsets x/y relativos al item (x/y por defecto 0).
 *
 * Esta funcion es async: resuelve imagenes/plantillas on-demand (cacheadas).
 * No dibuja la status bar (lo hace el pager tras renderPage).
 *
 * renderPage(ctx, component, {page, focusIndex, theme, loader, vars, pixfont})
 * ========================================================================== */

'use strict';

const W = 480, H = 222;

export async function renderPage(ctx, component, env) {
  const { theme, loader, vars, pixfont } = env;
  const bg = component.background || {};

  // 1. background_color
  //    overlay=true (dialog/wizard windowed sin color propio): NO limpiamos a
  //    negro, se compone sobre el frame de abajo (overlay flotante del firmware).
  if (!env.overlay) {
    const bc = bg.background_color || { r: 0, g: 0, b: 0 };
    ctx.fillStyle = `rgb(${bc.r | 0},${bc.g | 0},${bc.b | 0})`;
    ctx.fillRect(0, 0, W, H);
  } else if (bg.background_color) {
    // overlay con color explicito: respeta su color (ventana opaca).
    const bc = bg.background_color;
    ctx.fillStyle = `rgb(${bc.r | 0},${bc.g | 0},${bc.b | 0})`;
    ctx.fillRect(0, 0, W, H);
  }

  // 2a. background_path suelto (pantallas "graph" como recon_dashboard: el
  //     firmware dibuja encima un bar-graph nativo; aqui pintamos el fondo real).
  if (component.background_path) {
    try {
      const img = await loader.loadImage(component.background_path);
      ctx.drawImage(img, 0, 0);
    } catch (_) {}
  }

  // 2b. background.layers (painter order), offset 0,0 (relativo al origen)
  if (Array.isArray(bg.layers)) {
    for (const ly of bg.layers) {
      await drawLayer(ctx, ly, 0, 0, env);
    }
  }

  // 3. menu_items de la pagina activa
  const items = env.page && Array.isArray(env.page.menu_items)
    ? env.page.menu_items
    : (component.menu_items || []);

  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    const focused = (i === env.focusIndex);
    const layers = (focused && Array.isArray(item.selected_layers))
      ? item.selected_layers
      : (item.layers || []);
    const ix = item.x | 0, iy = item.y | 0;
    // contexto de fila (listas recon): si el item trae _row lo fijamos
    if (item._row !== undefined) vars.setRowContext(item._row);
    for (const ly of layers) {
      await drawLayer(ctx, ly, ix, iy, env);
    }
    if (item._row !== undefined) vars.setRowContext(null);
  }
}

// --------------------------------------------------------------------------
// Dibuja una layer con el item-offset (ox,oy).
// --------------------------------------------------------------------------
async function drawLayer(ctx, ly, ox, oy, env) {
  const { theme, loader, pixfont, vars } = env;
  const lx = ox + (ly.x | 0);
  const ly_ = oy + (ly.y | 0);

  // --- variable (toggle / signal / string) ---------------------------------
  if (ly.variable_name) {
    await drawVariableLayer(ctx, ly, lx, ly_, env);
    return;
  }

  // --- imagen --------------------------------------------------------------
  if (ly.image_path) {
    const color = theme.color(ly.recolor_palette);
    let drawable;
    try { drawable = await loader.loadRecolored(ly.image_path, color); }
    catch (_) { return; }
    ctx.drawImage(drawable, lx, ly_);
    return;
  }

  // --- texto estatico ------------------------------------------------------
  if (ly.text != null) {
    drawText(ctx, ly.text, lx, ly_, ly, env);
    return;
  }
}

// Resuelve una layer basada en variable + plantilla.
async function drawVariableLayer(ctx, ly, lx, ly_, env) {
  const { theme, loader, vars, pixfont } = env;
  const name = ly.variable_name;

  // 1) toggle inline
  if (ly.toggle_template) {
    const on = vars.getBool(name);
    const sub = ly.toggle_template[on ? 'enabled' : 'disabled'] || [];
    for (const s of sub) await drawLayer(ctx, s, lx, ly_, env);
    return;
  }

  // 2) string template inline
  if (ly.string_template) {
    const val = vars.get(name);
    drawText(ctx, val, lx, ly_, ly.string_template, env);
    return;
  }

  // 3) signal inline
  if (ly.signal) {
    await drawSignal(ctx, ly.signal, name, lx, ly_, env);
    return;
  }

  // 4) use_template nombrado (toggle | signal | string)
  if (ly.use_template) {
    const tmplName = ly.use_template;
    const tpl = await resolveNamedTemplate(theme, loader, tmplName);
    if (!tpl) return;
    if (tpl.__kind === 'toggle') {
      // Radio (option dialog): el circulo lleno marca la opcion SELECCIONADA.
      // El estado viene del contexto de fila (_selected) y no del valor bool de
      // $_LIST_ITEM (que es el label de texto, no un bool).
      let on;
      if (tpl.__isRadio && vars.row && typeof vars.row._selected === 'boolean') {
        on = vars.row._selected;
      } else {
        on = vars.getBool(name);
      }
      const sub = tpl[on ? 'enabled' : 'disabled'] || [];
      for (const s of sub) await drawLayer(ctx, s, lx, ly_, env);
      return;
    }
    if (tpl.__kind === 'signal') {
      await drawSignal(ctx, tpl, name, lx, ly_, env);
      return;
    }
    if (tpl.__kind === 'string') {
      drawText(ctx, vars.get(name), lx, ly_, tpl, env);
      return;
    }
  }

  // sin plantilla reconocible -> intenta texto crudo
  const raw = vars.get(name);
  if (raw) drawText(ctx, raw, lx, ly_, ly, env);
}

// Dibuja un signal (RSSI) usando thresholds -> minimum/weak/strong/max layers.
async function drawSignal(ctx, tpl, name, lx, ly_, env) {
  const { vars } = env;
  const rssi = Number(vars.get(name)) || -100;
  let bucket;
  if (rssi >= (tpl.strong_threshold ?? -60)) bucket = 'max_signal';
  else if (rssi >= (tpl.weak_threshold ?? -80)) bucket = 'strong_signal';
  else if (rssi >= (tpl.min_threshold ?? -95)) bucket = 'weak_signal';
  else bucket = 'minimum_signal';
  const sub = tpl[bucket] || tpl.minimum_signal || [];
  for (const s of sub) await drawLayer(ctx, s, lx, ly_, env);
}

// Carga + cachea una plantilla nombrada y le etiqueta __kind.
const _tplCache = new Map();
async function resolveNamedTemplate(theme, loader, name) {
  const key = theme.id + '::' + name;
  if (_tplCache.has(key)) return _tplCache.get(key);
  let path = theme.toggleTemplatePath(name);
  let kind = 'toggle';
  let isRadio = false;
  if (!path) { path = theme.signalTemplatePath(name); kind = 'signal'; }
  if (!path) { path = theme.stringTemplatePath(name); kind = 'string'; }
  if (!path) { path = theme.radioTemplatePath(name); kind = 'toggle'; isRadio = true; }
  if (!path) { _tplCache.set(key, null); return null; }
  let tpl;
  try { tpl = await loader.loadComponent(path); }
  catch (_) { _tplCache.set(key, null); return null; }
  tpl = Object.assign({}, tpl, { __kind: kind, __isRadio: isRadio });
  _tplCache.set(key, tpl);
  return tpl;
}

// --------------------------------------------------------------------------
// Texto (PixFont) con atributos de plantilla/layer: text_size,
// text_color_palette, max_chars, wrap_text, max_lines, center_text_within.
// --------------------------------------------------------------------------
function drawText(ctx, value, lx, ly_, attrs, env) {
  const { theme, pixfont } = env;
  let txt = value == null ? '' : String(value);
  if (attrs.max_chars && txt.length > attrs.max_chars) {
    txt = txt.slice(0, attrs.max_chars);
  }
  const size = attrs.text_size || 'medium';
  const color = theme.cssColor(attrs.text_color_palette);

  // wrap_text + max_lines
  if (attrs.wrap_text) {
    txt = wrapText(ctx, txt, attrs, size, pixfont);
  }

  // center_text_within: centra en un bbox
  const cw = attrs.center_text_within;
  if (cw) {
    const m = pixfont.measure(ctx, txt, size);
    const bx = (cw.start_x + cw.end_x) / 2 - m.w / 2;
    const by = (cw.start_y + cw.end_y) / 2 - m.h / 2;
    pixfont.draw(ctx, txt, Math.round(bx), Math.round(by), { size, color });
    return;
  }

  pixfont.draw(ctx, txt, lx, ly_, { size, color });
}

function wrapText(ctx, txt, attrs, size, pixfont) {
  const maxLines = attrs.max_lines || 2;
  // envuelve por ancho aproximado usando max_chars como pista de ancho de linea
  const perLine = attrs.max_chars || 24;
  const words = txt.split(/\s+/);
  const lines = [];
  let cur = '';
  for (const w of words) {
    if ((cur + ' ' + w).trim().length > perLine) {
      lines.push(cur.trim());
      cur = w;
      if (lines.length >= maxLines) break;
    } else {
      cur = (cur + ' ' + w).trim();
    }
  }
  if (cur && lines.length < maxLines) lines.push(cur);
  return lines.slice(0, maxLines).join('\n');
}

export default renderPage;
