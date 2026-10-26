/* =============================================================================
 * component.js — Carga componentes (pantallas) + assets PNG del tema (cache).
 * -----------------------------------------------------------------------------
 *   loadComponent(relPath)        -> Promise<obj>  (JSON de la pantalla, cacheado)
 *                                    Si viene como { template:{...} } (listas) se
 *                                    devuelve tal cual; el navigator lo desenvuelve.
 *   loadImage(relPath)            -> Promise<Image> (cacheado por URL)
 *   loadRecolored(relPath, color) -> Promise<Image|canvas> (tintado, cacheado)
 *
 * Todas las rutas son relativas al root del tema y se resuelven con theme.url().
 * ========================================================================== */

'use strict';

import { recolorImage } from './recolor.js';

export class ComponentLoader {
  constructor(theme) {
    this.theme = theme;
    this._json = new Map();     // relPath -> Promise<obj>
    this._img = new Map();      // url -> Promise<Image>
    this._recolor = new Map();  // url#r,g,b -> canvas
  }

  setTheme(theme) {
    this.theme = theme;
    this._json.clear();
    this._img.clear();
    this._recolor.clear();
  }

  loadComponent(relPath) {
    if (this._json.has(relPath)) return this._json.get(relPath);
    const url = this.theme.url(relPath);
    const p = (async () => {
      const res = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`component ${relPath} -> ${res.status}`);
      return res.json();
    })();
    this._json.set(relPath, p);
    return p;
  }

  loadImage(relPath) {
    const url = this.theme.url(relPath);
    if (this._img.has(url)) return this._img.get(url);
    const p = new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(`image ${relPath}`));
      img.src = url;
    });
    this._img.set(url, p);
    return p;
  }

  // Devuelve imagen tintada (o la original si color=null). Cacheada.
  async loadRecolored(relPath, color) {
    const img = await this.loadImage(relPath);
    if (!color) return img;
    const key = this.theme.url(relPath) + `#${color.r},${color.g},${color.b}`;
    if (this._recolor.has(key)) return this._recolor.get(key);
    const out = recolorImage(img, color);
    this._recolor.set(key, out);
    return out;
  }
}

export default ComponentLoader;
