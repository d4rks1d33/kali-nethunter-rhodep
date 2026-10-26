/* =============================================================================
 * recolor.js — "Recolor" de un PNG al color de una paleta (keep-alpha).
 * -----------------------------------------------------------------------------
 * El firmware Go tinta los assets blancos/alpha reemplazando el canal RGB por
 * el color de la paleta y PRESERVANDO el alpha original (replace-RGB-keep-alpha).
 * Asi un mismo glyph PNG sirve para cualquier color del tema.
 *
 * recolorImage(img, {r,g,b}) -> <canvas> tintado (cacheable por el caller).
 * Si getImageData falla (CORS, imagen cross-origin sin crossOrigin), devuelve
 * la imagen original para no romper el render.
 * ========================================================================== */

'use strict';

export function recolorImage(img, color) {
  if (!color) return img;
  const w = img.naturalWidth || img.width;
  const h = img.naturalHeight || img.height;
  if (!w || !h) return img;

  const cv = document.createElement('canvas');
  cv.width = w;
  cv.height = h;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  cx.imageSmoothingEnabled = false;
  cx.drawImage(img, 0, 0);

  try {
    const data = cx.getImageData(0, 0, w, h);
    const px = data.data;
    const r = color.r | 0, g = color.g | 0, b = color.b | 0;
    for (let i = 0; i < px.length; i += 4) {
      if (px[i + 3] !== 0) {   // solo pixeles no transparentes
        px[i] = r;
        px[i + 1] = g;
        px[i + 2] = b;
        // alpha intacto
      }
    }
    cx.putImageData(data, 0, 0);
  } catch (_) {
    return img; // CORS u otro: fallback a la imagen tal cual
  }
  return cv;
}

export default recolorImage;
