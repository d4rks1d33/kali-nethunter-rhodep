/* =============================================================================
 * recon_graph.js — RENDER ESPECIAL (custom) del grafico de barras "Packets per
 * second" del recon_dashboard.
 * -----------------------------------------------------------------------------
 * El firmware Go pinta este grafico de forma NATIVA (no esta declarado como
 * componente en el theme JSON: dashboard_recon.json solo trae el fondo PNG +
 * parametros de layout del grafico). Por eso lo replicamos aqui como un overlay
 * que el pager dibuja ENCIMA del componente recon_dashboard, igual que el
 * firmware lo compone sobre su fondo.
 *
 * Referencia visual (pantalla real):
 *   - Barras verticales de PPS, coloreadas por intensidad (azul->verde->
 *     amarillo->rojo), alineadas a la base.
 *   - Titulo "Packets per second: <actual>"
 *   - "Total: <total>  Avg: <avg>"
 *   - Eje derecho con max (p.ej 500) y mitad (250)
 *   - "-45s" a la izquierda y "NOW" abajo a la derecha
 *
 * SIN datos reales de PPS (sin Archer/scan) el historial queda en 0 y el grafico
 * se dibuja vacio (solo ejes/labels). NO inventamos trafico.
 *
 * drawReconGraph(ctx, comp, { vars, theme, pixfont })
 *   comp = JSON de dashboard_recon.json (trae graph_width, bar_w, bar_gap,
 *          x_start, y_top, y_bottom, y_label_x/y, title_x/y, *_palette, etc.)
 * ========================================================================== */

'use strict';

// Umbrales de color por fraccion de la altura (intensidad). Fiel a la escala
// azul->verde->amarillo->rojo del display real.
function barColor(frac) {
  if (frac >= 0.85) return 'rgb(250,72,9)';    // rojo
  if (frac >= 0.6)  return 'rgb(231,197,74)';  // amarillo
  if (frac >= 0.3)  return 'rgb(42,180,42)';   // verde
  return 'rgb(106,210,249)';                   // azul
}

// Redondea un maximo a un valor "lindo" para el eje (use_nice_labels).
function niceMax(v) {
  if (v <= 0) return 500; // placeholder cuando no hay datos
  const steps = [10, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000,
                 1500, 2000, 2500, 5000, 10000];
  for (const s of steps) if (v <= s) return s;
  // mas alla de la tabla: siguiente multiplo de 1000
  return Math.ceil(v / 1000) * 1000;
}

function fmtCount(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n | 0);
}

export function drawReconGraph(ctx, comp, env) {
  const { vars, pixfont } = env;
  const summary = vars.ppsSummary ? vars.ppsSummary() : { history: [], current: 0, avg: 0, total: 0 };
  const hist = summary.history || [];

  // ---- geometria (de los params del componente, con defaults fieles) -------
  const xStart  = num(comp.x_start, 23);
  const yTop    = num(comp.y_top, 92);
  const yBottom = num(comp.y_bottom, 178);
  const barW    = num(comp.bar_w, 8);
  const barGap  = num(comp.bar_gap, 1);
  const slots   = num(comp.graph_width, 45);
  const plotH   = Math.max(1, yBottom - yTop);

  const titleX  = num(comp.title_x, 20);
  const titleY  = num(comp.title_y, 45);
  const yLabX   = num(comp.y_label_x, 440);
  const yLabY   = num(comp.y_label_y, 83);
  const yHalfX  = num(comp.y_half_label_x, 440);
  const yHalfY  = num(comp.y_half_label_y, 132);

  const txtColor = cssFromTheme(env.theme, comp.text_color_palette, 'rgb(106,210,249)');

  // ---- escala del eje Y -----------------------------------------------------
  const rawMax = hist.length ? Math.max.apply(null, hist) : 0;
  const yMax = comp.use_nice_labels === false
    ? Math.max(1, rawMax)
    : niceMax(rawMax);

  // ---- barras (alineadas a la derecha: la ultima = NOW) --------------------
  // Mostramos las ultimas `slots` muestras; las mas viejas a la izquierda.
  const visible = hist.slice(-slots);
  const step = barW + barGap;
  // base x del primer bar: ancla la ultima muestra cerca del borde NOW.
  for (let i = 0; i < visible.length; i++) {
    const v = Math.max(0, visible[i]);
    const frac = yMax > 0 ? Math.min(1, v / yMax) : 0;
    const h = Math.round(frac * plotH);
    if (h <= 0) continue; // sin datos: no dibuja barra (grafico vacio honesto)
    const x = xStart + i * step;
    const y = yBottom - h;
    ctx.fillStyle = barColor(frac);
    ctx.fillRect(x, y, barW, h);
  }

  // ---- ejes / guias (linea base + media) -----------------------------------
  ctx.fillStyle = 'rgba(106,210,249,0.25)';
  ctx.fillRect(xStart, yBottom, slots * step, 1);              // base
  ctx.fillRect(xStart, Math.round((yTop + yBottom) / 2), slots * step, 1); // media

  // ---- labels de texto (PixFont) -------------------------------------------
  const small = { size: 'small', color: txtColor };
  // titulo
  pixfont.draw(ctx, `Packets per second: ${summary.current | 0}`, titleX, titleY,
    { size: 'medium', color: txtColor });
  // total / avg
  pixfont.draw(ctx, `Total: ${fmtCount(summary.total)}  Avg: ${summary.avg | 0}`,
    titleX, titleY + 18, small);
  // eje derecho: max y mitad
  pixfont.draw(ctx, String(yMax), yLabX, yLabY, small);
  pixfont.draw(ctx, String(Math.round(yMax / 2)), yHalfX, yHalfY, small);
  // ejes de tiempo
  pixfont.draw(ctx, '-45s', xStart - 6, yBottom + 4, small);
  pixfont.draw(ctx, 'NOW', xStart + slots * step - 28, yBottom + 4, small);
}

function num(v, d) { return (v == null || isNaN(+v)) ? d : +v; }

function cssFromTheme(theme, palette, fallback) {
  if (theme && typeof theme.cssColor === 'function') return theme.cssColor(palette, fallback);
  return fallback;
}

export default drawReconGraph;
