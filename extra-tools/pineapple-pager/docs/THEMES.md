# Themes de la pantalla del Pager (frontend web)

El WiFi Pineapple Pager real soporta *themes* nativos (p.ej. `wargames`)
renderizados por su motor Go a partir de `theme.json` + assets PNG + componentes.
Ese motor **no** se portea aca. En su lugar, este port implementa un sistema de
temas liviano y equivalente para el **frontend web**: cambia el look de la
pantalla (colores, acento, grilla, glow) mediante **variables CSS**, sin tocar el
layout ni la logica.

## Como funciona

- `frontend/themes/<id>.json` define un tema.
- `frontend/themes.js` lo carga y escribe las variables CSS en `:root`.
- `frontend/style.css` usa esas variables en todos los colores de la pantalla.
- La eleccion del usuario se guarda en `localStorage` (`pager.theme`).

### Variables CSS tematizables

| Campo JSON (`colors.*`) | Variable CSS        | Uso                                   |
|-------------------------|---------------------|---------------------------------------|
| `bg`                    | `--scr-bg`          | fondo de la pantalla y del terminal   |
| `fg`                    | `--scr-green`       | fosforo / texto principal             |
| `accent`                | `--scr-cyan`        | cabeceras, acento                     |
| `amber`                 | `--scr-amber`       | seleccion / highlight (color solido)  |
| `grid`                  | `--scr-grid`        | grilla, bordes y glow (triplete RGB)  |
| `danger`                | `--scr-danger`      | errores / nivel critico               |

> `grid` puede escribirse como `"r,g,b"` (triplete) **o** como `"#rrggbb"`:
> `themes.js` convierte el hex a triplete automaticamente, porque se usa dentro de
> `rgba(var(--scr-grid), <alpha>)`. El highlight de seleccion deriva de `amber`
> hacia `--scr-amber-rgb` del mismo modo.

## Formato de un tema

`frontend/themes/wargames.json`:

```json
{
  "id": "wargames",
  "name": "WarGames (default)",
  "author": "Hak5 / port",
  "description": "El look clasico de la pantalla del Pager: fosforo verde + cyan.",
  "colors": {
    "bg":     "#020402",
    "fg":     "#00ef35",
    "accent": "#35f0e0",
    "amber":  "#ffd23a",
    "grid":   "0,239,53",
    "danger": "#ff2b2b"
  }
}
```

Temas incluidos:

- **wargames** — default verde/cyan "wargames".
- **amber** — monitor monocromo ambar (VT220).
- **ice** — azul hielo (ejemplo de tema de comunidad).

## Como agregar un tema de comunidad

1. Crea `frontend/themes/mitheme.json` con el formato de arriba
   (si esta instalado: `/opt/pineapple-pager/frontend/themes/mitheme.json`).
2. Elegi un `id` unico (igual al nombre de archivo sin `.json`).
3. Recarga la UI. El tema aparece solo en **Settings → Theme**.

El descubrimiento es automatico:

- Si el backend corre, `GET /api/themes` **escanea el directorio** `themes/` y
  devuelve el catalogo (id, name, author, description). Asi, dropear el `.json`
  basta: no hay que editar codigo ni listas.
- Sin backend (modo mock), el fallback es `frontend/themes/index.json`. Si usas
  ese modo, agrega tu `id` al array `themes` de ese archivo.

## Como se prueba

1. Arranca el stack: `systemctl start pager.target` (o corre `pager-server.py`).
2. Abri la UI y navega a **Settings** (dashboard → engranaje).
3. Selecciona la fila **Theme** y presiona SELECT: cicla entre los temas.
   Los colores de la pantalla cambian al instante; el layout no se rompe.

Validacion rapida del endpoint:

```sh
curl -s http://127.0.0.1:1471/api/themes | python3 -m json.tool
```
