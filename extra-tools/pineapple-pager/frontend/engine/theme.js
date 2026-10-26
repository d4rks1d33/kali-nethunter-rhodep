/* =============================================================================
 * theme.js — Carga y resuelve el theme.json REAL de un tema Hak5 Pager.
 * -----------------------------------------------------------------------------
 * Un tema del WiFi Pineapple Pager es una carpeta:
 *   theme.json            manifiesto (paletas, status_bars, generic_menus,
 *                         templates toggle/string/radio/signal, *_path sueltos)
 *   components/*.json      pantallas (componentes)
 *   assets/*.png           imagenes
 *
 * Este modulo NO inventa UI: interpreta el MISMO manifiesto que el firmware Go.
 *
 * Expone:
 *   theme.url(relPath)              -> URL absoluta (relativa al root del tema)
 *   theme.color(name)              -> {r,g,b} | null   (desde color_palette)
 *   theme.cssColor(name,fallback)  -> "rgb(r,g,b)"
 *   theme.statusBarPath(name)      -> ruta componente de la status bar
 *   theme.resolveTarget(target)    -> {kind, path?, name?}  (para navigator)
 *   theme.toggleTemplatePath(name) / stringTemplatePath / radioTemplatePath /
 *   theme.signalTemplatePath(name)
 *
 * Base URL por defecto: /community/themes/<id>/  (el frontend lo expone por
 * symlink). Se puede apuntar a /api/themes/<id>/ si el backend sirve los temas.
 * ========================================================================== */

'use strict';

export class Theme {
  constructor(id, baseUrl, manifest) {
    this.id = id;
    this.baseUrl = baseUrl.replace(/\/+$/, '') + '/';
    this.manifest = manifest || {};
    this.palette = this.manifest.color_palette || {};
    this.statusBars = this.manifest.status_bars || {};
    this.genericMenus = this.manifest.generic_menus || {};
    this.optionDialogs = this.manifest.option_dialogs || {};
    this.toggleTemplates = this.manifest.toggle_templates || {};
    this.stringTemplates = this.manifest.string_templates || {};
    this.radioTemplates = this.manifest.radio_templates || {};
    this.signalTemplates = this.manifest.signal_templates || {};
  }

  // Normaliza y resuelve una ruta relativa del tema (assets/..., components/...)
  // a una URL absoluta bajo el root del tema.
  url(relPath) {
    if (!relPath) return null;
    if (/^https?:\/\//.test(relPath) || relPath.startsWith('/')) return relPath;
    const clean = String(relPath).replace(/\\/g, '/').replace(/^\.\//, '');
    return this.baseUrl + clean;
  }

  // {r,g,b} para un nombre de paleta, o null.
  color(name) {
    if (!name) return null;
    const c = this.palette[name];
    if (!c) return null;
    return { r: c.r | 0, g: c.g | 0, b: c.b | 0 };
  }

  cssColor(name, fallback = 'rgb(230,230,220)') {
    const c = this.color(name);
    return c ? `rgb(${c.r},${c.g},${c.b})` : fallback;
  }

  // ---- resolucion de rutas de componentes --------------------------------
  statusBarPath(name) {
    return this.statusBars[name] || this.statusBars.default || null;
  }

  // Resuelve un `target` de menu_item.  Devuelve:
  //   { kind:'screen',   path, name }   pantalla a push-ear (fetch JSON)
  //   { kind:'function', name }         accion nativa (function_*)
  //   { kind:'inline_toggle' }          toggle in-place de la variable del item
  //   { kind:'fallback_menu' }          volver al menu previo
  //   { kind:'none' }                   sin target (item decorativo)
  //   { kind:'unknown',  name }         no resuelto
  resolveTarget(target) {
    if (!target) return { kind: 'none' };
    if (target === 'inline_toggle') return { kind: 'inline_toggle' };
    if (target === 'fallback_menu') return { kind: 'fallback_menu' };
    // targets de accion dentro de dialogs/wizards (edit_string_dialog,
    // ui_option_dialog): back cierra el overlay; save/keyboard/select/select_*
    // son acciones in-dialog que maneja el navigator (no son pantallas).
    if (target === 'back') return { kind: 'back' };
    if (target === 'save' || target === 'select' || target === 'keyboard'
        || target === 'select_menu' || target === 'select_list'
        || target === 'confirm') {
      return { kind: 'action', name: target };
    }
    if (target.startsWith('function_')) return { kind: 'function', name: target };

    // pantalla registrada en generic_menus
    if (this.genericMenus[target]) {
      return { kind: 'screen', path: this.genericMenus[target], name: target };
    }
    // option_dialogs (narrow/recon/...)
    if (this.optionDialogs[target]) {
      return { kind: 'screen', path: this.optionDialogs[target], name: target };
    }
    // *_path sueltos del manifiesto (ej: recon_dashboard -> recon_dashboard_path)
    const pathKey = target.endsWith('_path') ? target : target + '_path';
    if (typeof this.manifest[pathKey] === 'string') {
      return { kind: 'screen', path: this.manifest[pathKey], name: target };
    }
    // clave top-level directa string (ej: recon_dashboard definido como *_path)
    if (typeof this.manifest[target] === 'string') {
      return { kind: 'screen', path: this.manifest[target], name: target };
    }
    // recon_lists mapping
    if (this.manifest.recon_lists && this.manifest.recon_lists[target]) {
      return { kind: 'screen', path: this.manifest.recon_lists[target], name: target };
    }
    return { kind: 'unknown', name: target };
  }

  toggleTemplatePath(name) { return this.toggleTemplates[name] || null; }
  stringTemplatePath(name) { return this.stringTemplates[name] || null; }
  radioTemplatePath(name)  { return this.radioTemplates[name]  || null; }
  signalTemplatePath(name) { return this.signalTemplates[name] || null; }
}

export class ThemeLoader {
  // baseFor(id) => URL base del tema. Default: /community/themes/<id>/.
  constructor(opts = {}) {
    this.baseFor = opts.baseFor || ((id) => `community/themes/${id}/`);
    this._cache = new Map(); // id -> Promise<Theme>
  }

  load(id) {
    if (this._cache.has(id)) return this._cache.get(id);
    const base = this.baseFor(id);
    const p = (async () => {
      const res = await fetch(base.replace(/\/+$/, '') + '/theme.json', {
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) throw new Error(`theme.json ${id} -> ${res.status}`);
      const manifest = await res.json();
      return new Theme(id, base, manifest);
    })();
    this._cache.set(id, p);
    return p;
  }

  clearCache() { this._cache.clear(); }
}

export default ThemeLoader;
