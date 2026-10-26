/* =============================================================================
 * navigator.js — STATE MACHINE fiel del display (ScreenManager = stack).
 * -----------------------------------------------------------------------------
 * - Mantiene un STACK de "frames" (pantallas). push(target) renderiza encima;
 *   back() hace pop.
 * - button_map del componente (+ overrides por menu_item) mapea a/b/up/down/
 *   left/right -> select | back | previous | next | previous_page | next_page |
 *   noop.
 * - focus sobre menu_items; items unselectable:true se saltean.
 * - target resuelve via theme.resolveTarget():
 *     screen        -> push de la pantalla
 *     function_*     -> callback onFunction(name, item)  (accion en backend)
 *     inline_toggle  -> callback onInlineToggle(variable) (togglea in-place)
 *     fallback_menu  -> back()
 * - pages[]: paginacion (previous_page/next_page cambian de pagina).
 *
 * El navigator NO dibuja: tras cada cambio llama env.onChange() para que el
 * pager re-renderice el frame actual.
 *
 * API:
 *   nav = new Navigator({ theme, loader, vars, resolveList, onFunction,
 *                         onInlineToggle, onChange })
 *   await nav.start(screenPath)          arranca en una pantalla (ruta componente)
 *   nav.press('a'|'b'|'up'|'down'|'left'|'right')
 *   nav.current()                        -> frame actual {component,page,...}
 * ========================================================================== */

'use strict';

// Mapa de botones por defecto del firmware del Pager (ROM default). Se usa
// cuando un componente no define su propio button_map. Botones: a/b/up/down/
// left/right (ya traducidos desde las teclas fisicas por pager.js).
const DEFAULT_BUTTON_MAP = {
  a: 'select',
  b: 'back',
  up: 'previous',
  down: 'next',
  left: 'previous_page',
  right: 'next_page',
};

export class Navigator {
  constructor(env) {
    this.env = env;                 // {theme,loader,vars,resolveList,onFunction,onInlineToggle,onChange}
    this.stack = [];                // array de frames
  }

  current() { return this.stack[this.stack.length - 1] || null; }

  // Desenvuelve componentes { template:{...} } (listas) a la pantalla real.
  _unwrap(comp) {
    if (comp && comp.template && typeof comp.template === 'object') {
      return { ...comp.template, __isList: true };
    }
    return comp;
  }

  // Construye un frame a partir de un componente JSON.
  //   ctx (opcional): contexto de navegacion heredado del item seleccionado
  //   (ej: { selected: <row>, category: '<nombre>' }). El pager lo usa para
  //   saber QUE lista poblar (ej: payloads de la categoria elegida).
  async _buildFrame(component, name, ctx) {
    const comp = this._unwrap(component);
    const pages = Array.isArray(comp.pages) && comp.pages.length
      ? comp.pages
      : [{ page_index: 0, menu_items: comp.menu_items || [] }];
    const frame = {
      name: name || comp.screen_name || '',
      component: comp,
      pages,
      pageIndex: 0,
      focusIndex: 0,
      isList: !!comp.__isList,
      ctx: ctx || null,
      _listPage: 0,
    };
    // listas: inyecta filas resueltas en los slots Slot*/Row* de la pagina
    if (frame.isList) await this._fillListRows(frame);
    frame.focusIndex = this._firstSelectable(frame);
    return frame;
  }

  // Slots de una lista: ids "Slot0..N" (payloads/themes/ssidpool) o "Row0..N"
  // (recon). Es el id que matchea /^(Slot|Row)\d+$/i.
  static isSlotId(id) { return /^(Slot|Row)\d+$/i.test(id || ''); }

  // Para listas: resuelve la lista (filas de datos) y asocia cada fila a su slot
  // Slot*/Row* en orden. Slots sin dato quedan _empty (el foco los saltea).
  async _fillListRows(frame) {
    if (!this.env.resolveList) return;
    const rows = await this.env.resolveList(frame) || [];
    const items = this._pageItems(frame);
    let ri = 0;
    for (const it of items) {
      if (Navigator.isSlotId(it.id)) {
        it._row = rows[ri] || null;
        it._empty = !rows[ri];
        ri++;
      }
    }
    frame._rowCount = rows.length;
  }

  _pageItems(frame) {
    const p = frame.pages[frame.pageIndex] || { menu_items: [] };
    return p.menu_items || [];
  }

  _firstSelectable(frame) {
    const items = this._pageItems(frame);
    for (let i = 0; i < items.length; i++) {
      if (!items[i].unselectable && !items[i]._empty) return i;
    }
    return 0;
  }

  // button_map efectivo: DEFAULT del firmware < componente < item enfocado.
  // Muchos componentes (dashboard_alerts, pager_portal, recon, listas) NO
  // definen button_map propio y dependen del default ROM del Pager. Sin este
  // default, ningun boton hacia nada (no se movia ni volvia para atras).
  _buttonMap(frame) {
    const def = DEFAULT_BUTTON_MAP;
    const base = { ...def, ...(frame.component.button_map || {}) };
    const items = this._pageItems(frame);
    const it = items[frame.focusIndex];
    if (it && it.button_map) return { ...base, ...it.button_map };
    return base;
  }

  // ---- arranque / navegacion de stack -------------------------------------
  async start(screenPath, name) {
    this.stack = [];
    await this.pushPath(screenPath, name);
  }

  async pushPath(screenPath, name, ctx) {
    const comp = await this.env.loader.loadComponent(screenPath);
    const frame = await this._buildFrame(comp, name, ctx);
    this.stack.push(frame);
    this._emit();
  }

  async pushComponent(component, name) {
    const frame = await this._buildFrame(component, name);
    this.stack.push(frame);
    this._emit();
  }

  back() {
    if (this.stack.length > 1) {
      this.stack.pop();
      this._emit();
    }
  }

  _emit() { if (this.env.onChange) this.env.onChange(this.current()); }

  // ---- input --------------------------------------------------------------
  press(btn) {
    const frame = this.current();
    if (!frame) return undefined;
    const map = this._buttonMap(frame);
    const action = map[btn] || 'noop';
    // Devolvemos la promesa del action (select/pagina son async) para que los
    // llamadores que necesiten esperar la navegacion puedan await-earla. El
    // pager normal ignora el retorno (navegacion dirigida por eventos).
    return this._action(action);
  }

  async _action(action) {
    const frame = this.current();
    if (!frame) return;
    // "goto <id>": salto de foco directo a un menu_item por id. Lo usan algunos
    // wizards (ej management_ap_wizard: Save/Clear con down/left/right "goto X").
    // Sin esto, esos botones caian en default y no hacian nada -> el foco se
    // quedaba atascado y no se podia llegar a los campos (ssid/pass) que abren
    // el edit_string_dialog. (BUG 2: el foco "no se movia").
    if (typeof action === 'string' && action.startsWith('goto ')) {
      this._gotoId(action.slice(5).trim());
      return;
    }
    switch (action) {
      case 'previous': await this._moveFocus(-1); break;
      case 'next':     await this._moveFocus(+1); break;
      case 'previous_page': this._movePage(-1); break;
      case 'next_page':     this._movePage(+1); break;
      case 'back':     this.back(); break;
      case 'select':   await this._select(); break;
      case 'noop':
      default: break;
    }
  }

  // Mueve el foco al menu_item con el id dado (si es seleccionable).
  _gotoId(id) {
    const frame = this.current();
    const items = this._pageItems(frame);
    for (let i = 0; i < items.length; i++) {
      if (items[i].id === id && !items[i].unselectable && !items[i]._empty) {
        frame.focusIndex = i;
        this._emit();
        return;
      }
    }
  }

  async _moveFocus(dir) {
    const frame = this.current();
    const items = this._pageItems(frame);
    if (!items.length) return;

    // En listas: buscar el siguiente slot con dato SIN envolver. Si no hay y
    // existen mas paginas de datos, auto-avanzar pagina (como el firmware).
    if (frame.isList) {
      let i = frame.focusIndex + dir;
      while (i >= 0 && i < items.length) {
        if (!items[i].unselectable && !items[i]._empty) {
          frame.focusIndex = i; this._emit(); return;
        }
        i += dir;
      }
      // borde: intentar paginar en la direccion del movimiento
      if (this.env.onListPage) {
        const pdir = dir > 0 ? +1 : -1;
        const moved = await this._tryListPage(frame, pdir);
        if (moved) {
          // al avanzar foco al primero; al retroceder foco al ultimo con dato
          frame.focusIndex = pdir > 0
            ? this._firstSelectable(frame)
            : this._lastSelectable(frame);
          this._emit();
        }
      }
      return;
    }

    // Menus normales multi-pagina (ej dashboard_settings con 2 paginas de
    // secciones): si el foco esta en el borde (ultimo bajando / primero subiendo)
    // y existe una pagina adyacente en pages[], cruzamos de pagina en vez de
    // hacer wrap. Asi down desde "System" (pag 0) pasa a "Clock/GPS/..." (pag 1)
    // y las secciones Bluetooth/GPS/About se vuelven alcanzables. Sin pagina
    // adyacente, wrap dentro de la pagina (comportamiento previo).
    const atFirst = (frame.focusIndex <= this._firstSelectable(frame));
    const atLast  = (frame.focusIndex >= this._lastSelectable(frame));
    const canPrevPage = frame.pageIndex > 0;
    const canNextPage = frame.pageIndex < frame.pages.length - 1;

    if (dir > 0 && atLast && canNextPage) {
      frame.pageIndex += 1;
      frame.focusIndex = this._firstSelectable(frame);
      this._emit();
      return;
    }
    if (dir < 0 && atFirst && canPrevPage) {
      frame.pageIndex -= 1;
      frame.focusIndex = this._lastSelectable(frame);
      this._emit();
      return;
    }

    // Dentro de la pagina: wrap-around saltando no-seleccionables.
    let i = frame.focusIndex;
    for (let n = 0; n < items.length; n++) {
      i = (i + dir + items.length) % items.length;
      if (!items[i].unselectable && !items[i]._empty) { frame.focusIndex = i; break; }
    }
    this._emit();
  }

  _lastSelectable(frame) {
    const items = this._pageItems(frame);
    for (let i = items.length - 1; i >= 0; i--) {
      if (!items[i].unselectable && !items[i]._empty) return i;
    }
    return 0;
  }

  // Cambia la pagina de datos de una lista; devuelve true si efectivamente
  // cambio (habia mas datos en esa direccion).
  async _tryListPage(frame, dir) {
    const before = frame._listPage || 0;
    await this.env.onListPage(frame, dir);
    if ((frame._listPage || 0) === before) return false;
    await this._fillListRows(frame);
    return true;
  }

  async _movePage(dir) {
    const frame = this.current();
    const np = frame.pageIndex + dir;
    if (np < 0 || np >= frame.pages.length) {
      // listas: paginan sobre datos, no sobre pages[]; delega al pager
      if (frame.isList && this.env.onListPage) {
        await this.env.onListPage(frame, dir);
        await this._fillListRows(frame);
        frame.focusIndex = this._firstSelectable(frame);
        this._emit();
      }
      return;
    }
    frame.pageIndex = np;
    frame.focusIndex = this._firstSelectable(frame);
    this._emit();
  }

  async _select() {
    const frame = this.current();
    const items = this._pageItems(frame);
    const it = items[frame.focusIndex];
    // Pantallas "graph" sin menu_items (ej dashboard_recon): no hay item
    // enfocable pero SELECT abre su lista asociada (target_recon_list). El
    // firmware pinta el grafico nativo y A entra a la lista de resultados.
    if (!it) {
      const listTarget = frame.component.target_recon_list
        || frame.component.target_list;
      if (listTarget) {
        const r = this.env.theme.resolveTarget(listTarget);
        if (r.kind === 'screen') { await this.pushPath(r.path, r.name); }
      }
      return;
    }
    const target = it.target;
    const res = this.env.theme.resolveTarget(target);

    switch (res.kind) {
      case 'screen': {
        // Si el item es una fila de lista, propaga la seleccion al nuevo frame
        // para que el pager sepa que sublista poblar (ej: categoria elegida).
        let ctx = null;
        if (it._row) {
          const label = it._row['$_LIST_ITEM'];
          ctx = { selected: it._row, label, category: label };
        }
        // Propaga la variable/el id del item que abrio la pantalla. Lo usan los
        // dialogs de edicion (edit_string_dialog) para saber QUE variable editar
        // y mostrar su valor/ titulo actuales ($_INPUT / $_INPUT_NAME).
        if (it.variable || it.id) {
          ctx = Object.assign({}, ctx, {
            editVariable: it.variable || null,
            editLabel: it.id || '',
            target: target,
          });
        }
        // Aviso previo: el pager puede sembrar variables del dialog antes del
        // primer render (ej: $_INPUT con el valor actual de it.variable).
        if (this.env.onBeforePush) {
          await this.env.onBeforePush(res, it, frame);
        }
        await this.pushPath(res.path, res.name, ctx);
        break;
      }
      case 'inline_toggle':
        if (this.env.onInlineToggle) await this.env.onInlineToggle(it.variable, it, frame);
        this._emit(); // re-render con el nuevo estado
        break;
      case 'function':
        if (this.env.onFunction) await this.env.onFunction(res.name, it, frame);
        break;
      case 'back':
        this.back();
        break;
      case 'action':
        // accion in-dialog (save/keyboard/select/select_menu/select_list). La
        // resuelve el pager (ej: teclado on-screen, confirmar seleccion). Si
        // devuelve true => cerrar el dialog (pop).
        if (this.env.onDialogAction) {
          const close = await this.env.onDialogAction(res.name, it, frame);
          if (close) this.back(); else this._emit();
        } else {
          this._emit();
        }
        break;
      case 'fallback_menu':
        this.back();
        break;
      case 'none':
      case 'unknown':
      default:
        // sin pantalla: no-op (o el pager puede loguear)
        if (this.env.onUnresolved) this.env.onUnresolved(target, it);
        break;
    }
  }
}

export default Navigator;
