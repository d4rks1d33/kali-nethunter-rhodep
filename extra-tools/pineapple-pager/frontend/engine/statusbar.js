/* =============================================================================
 * statusbar.js — Render de las 5 status bars del display (default/network/
 * recon/min/gps). Cada barra es un componente JSON con status_bar_items{}.
 * -----------------------------------------------------------------------------
 * Cada item (Time/Battery/Volume/Brightness/Vibrate/WifiClient/Freq/PCAP/DB/
 * GPS/Wigle) tiene:
 *   x,y absolutos (en el canvas 480x222, arriba)
 *   layers: { <estado>: [ {image_path, x?, y?, recolor_palette?} ] }
 *   recolor_palette opcional a nivel item (para Time: texto del reloj).
 *
 * El estado concreto de cada item lo deriva deriveState() desde las variables.
 * Time no tiene asset: se dibuja el texto del reloj ($_CLOCK_) en x,y.
 *
 * drawStatusBar(ctx, barJson, {theme, loader, vars, pixfont}) -> Promise
 * ========================================================================== */

'use strict';

// Deriva el "estado" (clave de layers) de un item segun las variables actuales.
function deriveState(name, item, vars) {
  const s = vars.snapshot;
  switch (name) {
    case 'Battery': {
      const st = s.status || {};
      if (st.charging) {
        const p = st.battery != null ? st.battery : 0;
        if (p >= 100) return 'charged';
        if (p >= 75) return 'charging_75';
        if (p >= 50) return 'charging_50';
        if (p >= 25) return 'charging_25';
        return 'charging_25';
      }
      return 'discharging';
    }
    case 'Volume': {
      const st = s.status || {};
      if (st.mute) return 'mute';
      const v = st.volume != null ? st.volume : 50;
      if (v <= 0) return 'mute';
      if (v < 34) return 'low';
      if (v < 67) return 'medium';
      return 'high';
    }
    case 'Brightness': {
      const b = (s.status && s.status.brightness != null) ? s.status.brightness : 75;
      const keys = Object.keys(item.layers || {}).map(Number).sort((a, c) => a - c);
      // elige el umbral mas cercano hacia abajo
      let best = keys[0];
      for (const k of keys) if (b >= k) best = k;
      return String(best);
    }
    case 'Vibrate':
      return vars.getBool('$_VIBRATE') ? 'on' : 'off';
    case 'WifiClient': {
      const st = s.status || {};
      if (st.wifi_client === 'connected' || st.wifi_connected) return 'connected';
      if (st.wifi_client === 'disabled' || st.wifi_client_enabled === false) return 'disabled';
      return 'disconnected';
    }
    case 'FrequencyIndicators': {
      const band = (s.recon && s.recon.band) || (s.status && s.status.band);
      return item.layers[band] ? String(band) : 'off';
    }
    case 'PCAP': return (s.recon && s.recon.pcap) ? 'on' : 'off';
    case 'DB': return (s.recon && s.recon.db) ? 'on' : 'off';
    case 'GPS': return (s.gps && s.gps.fix) ? 'on' : 'off';
    case 'Wigle': return (s.recon && s.recon.wigle) ? 'on' : 'off';
    default:
      return 'default';
  }
}

export async function drawStatusBar(ctx, barJson, env) {
  if (!barJson || !barJson.status_bar_items) return;
  const { theme, loader, vars, pixfont } = env;
  const items = barJson.status_bar_items;

  for (const name of Object.keys(items)) {
    const item = items[name];
    const ix = item.x | 0, iy = item.y | 0;

    // Time (y afines): sin asset -> dibuja texto del reloj.
    const state = deriveState(name, item, vars);
    const layers = (item.layers && item.layers[state]) || [];

    if (name === 'Time') {
      const txt = vars.get('$_CLOCK_');
      const color = theme.cssColor(item.recolor_palette || 'blue');
      pixfont.draw(ctx, txt, ix, iy, { size: 'small', color });
      continue;
    }

    for (const ly of layers) {
      if (!ly.image_path) {
        // layer de texto dentro de la status bar (raro pero posible)
        if (ly.text != null) {
          pixfont.draw(ctx, ly.text, ix + (ly.x | 0), iy + (ly.y | 0), {
            size: ly.text_size || 'small',
            color: theme.cssColor(ly.text_color_palette),
          });
        }
        continue;
      }
      const color = theme.color(ly.recolor_palette);
      let drawable;
      try { drawable = await loader.loadRecolored(ly.image_path, color); }
      catch (_) { continue; }
      ctx.drawImage(drawable, ix + (ly.x | 0), iy + (ly.y | 0));
    }
  }
}

export default drawStatusBar;
