/* =============================================================================
 * pixfont.js — Fuente bitmap/monospace para el display del Pager
 * -----------------------------------------------------------------------------
 * El binario Go usa la fuente propietaria "hak5font" (PixFont) — un bitmap font
 * que NO esta incluido en los temas de comunidad (solo los assets PNG + JSON).
 * Como no tenemos el glyph atlas real, aproximamos con una fuente monospace web.
 *
 * CALIBRACION (ver ENTREGABLE):
 *   text_size -> px de alto de celda:  small~11  medium~16  large~22
 *   El PixFont real es ~proporcional-estrecho; usamos un monospace con
 *   letter-spacing negativo leve para acercar el ancho de linea.
 *
 * API:
 *   PixFont.sizePx(textSize)                      -> px
 *   PixFont.measure(ctx, str, textSize)           -> {w,h}
 *   PixFont.draw(ctx, str, x, y, {size,color,anaglyph})
 *     - x,y = esquina sup-izq de la primera celda (igual que el engine Go, que
 *       dibuja glyphs top-left). Soporta \n.
 *     - anaglyph: dibuja 3x con offset -1/+1/0 (red3d/cyan3d/white) estilo 3D.
 * ========================================================================== */

'use strict';

// Alturas de celda calibradas contra los offsets de los temas (dashboard usa
// "large" para los labels PAGER PORTAL/RECON/etc; recon list usa small/medium).
const SIZE_PX = { small: 11, medium: 16, large: 22 };

// Familia monospace: el bitmap real es pixelado y de ancho fijo. Preferimos una
// mono con buen soporte y desactivamos el antialias a nivel de canvas aparte.
const FONT_STACK = '"DejaVu Sans Mono","Liberation Mono","Courier New",monospace';

// Factor de line-height: el PixFont apila lineas bastante juntas.
const LINE_FACTOR = 1.18;

// Colores fijos para el modo anaglyph (3D) cuando el tema no da paleta.
const ANAGLYPH = {
  red3d:  'rgb(255,0,0)',
  cyan3d: 'rgb(0,255,255)',
  white:  'rgb(230,230,220)',
};

function sizePx(textSize) {
  return SIZE_PX[textSize] || SIZE_PX.medium;
}

// fuente CSS para un tamanio dado
function fontFor(px) {
  return `${px}px ${FONT_STACK}`;
}

// Mide una cadena (soporta \n). Devuelve ancho max de linea + alto total.
function measure(ctx, str, textSize) {
  const px = sizePx(textSize);
  ctx.save();
  ctx.font = fontFor(px);
  const lines = String(str == null ? '' : str).split('\n');
  let w = 0;
  for (const ln of lines) w = Math.max(w, ctx.measureText(ln).width);
  ctx.restore();
  const lh = Math.round(px * LINE_FACTOR);
  return { w: Math.ceil(w), h: lh * lines.length, lineHeight: lh, px };
}

/* Dibuja texto estilo bitmap.
 * opts = { size:'small'|'medium'|'large', color:'rgb(...)', anaglyph:bool,
 *          align:'left', lineHeight }
 * (x,y) es TOP-LEFT de la celda. Internamente convertimos a baseline.
 */
function draw(ctx, str, x, y, opts = {}) {
  const textSize = opts.size || 'medium';
  const px = sizePx(textSize);
  const lh = opts.lineHeight || Math.round(px * LINE_FACTOR);
  const lines = String(str == null ? '' : str).split('\n');

  ctx.save();
  ctx.font = fontFor(px);
  ctx.textAlign = opts.align || 'left';
  ctx.textBaseline = 'top';
  // el bitmap font no tiene antialias; lo dejamos nitido
  ctx.imageSmoothingEnabled = false;

  lines.forEach((ln, i) => {
    const ly = y + i * lh;
    if (opts.anaglyph) {
      ctx.fillStyle = ANAGLYPH.red3d;
      ctx.fillText(ln, x - 1, ly);
      ctx.fillStyle = ANAGLYPH.cyan3d;
      ctx.fillText(ln, x + 1, ly);
      ctx.fillStyle = opts.color || ANAGLYPH.white;
      ctx.fillText(ln, x, ly);
    } else {
      ctx.fillStyle = opts.color || 'rgb(230,230,220)';
      ctx.fillText(ln, x, ly);
    }
  });

  ctx.restore();
}

export const PixFont = { sizePx, measure, draw, SIZE_PX, ANAGLYPH };
export default PixFont;
