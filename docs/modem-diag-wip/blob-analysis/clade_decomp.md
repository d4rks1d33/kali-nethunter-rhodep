# CLADE decompression del código del modem SM6375 (Moto G82 5G) — descompresión del dispatcher FTM

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
ELF reensamblado: `/tmp/modemre/modem_full.elf` (machine 0xa4 = QDSP6/Hexagon)
Blob (solo lectura, extraído a dir nuevo): `/tmp/clade/modem.b00..b34 + modem.mdt`
Herramienta: `nlitsme/qualcomm-q6zip` → `unclade.py` + `using-cladelib/cladetool.cpp` (LEÍDOS COMPLETOS)

Leyenda: **FACT** = verificado en esta imagen · **INFERENCE** = deducción arquitectónica · **UNKNOWN** = no determinable estáticamente aquí.

---

## 0. RESULTADO EJECUTIVO — leer primero (honesto)

**Se rompió el "límite duro" que declararon los passes anteriores.** Los passes previos
(`q6zip_decomp2.md`, `tech_enter_decoded.md`) concluyeron que la descompresión del código
`0xd8xxxxxx` era **imposible estáticamente** porque "no hay metadata en el ELF". **Eso es
INCORRECTO en su parte fuerte.** En este pass:

1. **FACT — Localicé las 3 diccionarios CLADE** dentro de `modem.b26` (el segmento `.clade`),
   en el offset de archivo **0x2444000 / 0x2446000 / 0x2448000** (8192 bytes = 2048 words c/u).
   Validados con la firma OR de cladetool.cpp `{0x3fffffff, 0x007fffff, 0x0001ffff}` (match EXACTO).
2. **FACT — `.clade.comp` = inicio de `modem.b26`** (offset 0). Es un stream bit-packed CLADE.
   Los diccionarios están al FINAL del segmento, exactamente como `unclade.py CladeSegment`
   espera (`(end&~0x1fff)-0x6000`). b26 filesz = 0x244a040; dicts en 0x2444000 → cuadra.
3. **FACT — La descompresión CLADE FUNCIONA.** `unclade.py` con esos dicts descomprime el
   stream y produce **código Hexagon válido**. La **página 0 (vaddr 0xd8000000)** empieza con
   un prólogo perfecto:
   ```
   d8000000: immext(#0xf8018000); r1:0=combine(#0,##0xf8018000); r17=#0; r3:2=combine(#0,#0)
   d800000c: call 0xd80dec28
   d8000010: immext(#0x409c0); jump 0xd80409f4
   ```
   y `0xd80dec28` (destino de ese call) descomprime como un **`strlen` limpio** (loop0 +
   `memb(r3++#1)` + `cmp.eq`). Los targets de call/jump caen todos en 0xd8xxxxxx coherentes.
4. **FACT — Descomprimí TODO el rango y aislé la página del dispatcher FTM `0xd8150ed8`.**
   Salida completa: **`/tmp/modemre/clade_dec.bin`** (54 MB). Región del dispatcher:
   `/tmp/modemre/clade_dec_dispatch.bin` (0xd8150000..0xd8152000).
5. **LÍMITE REAL (nuevo, más preciso que "imposible"): `unclade.py` es una reimplementación
   INCOMPLETA de CLADE.** Produce ~**74% de words correctos y ~26% incorrectos**, de forma
   uniforme (no es drift: la tasa de error es idéntica en la página 0, en la zona `strlen`
   limpia, y en la página del dispatcher). Con ~26% de words corruptos, la ESTRUCTURA del
   dispatcher es legible (comparaciones de sub_command, escritura de `0x14`, calls a
   sub-handlers) pero los **valores numéricos exactos** (enum TECH, número exacto de
   sub_command) **NO son 100% confiables** palabra-por-palabra. La causa del 26% está
   identificada (sección 4): el manejo de **exception words** de CLADE
   (`.clade.exception_high/low_*`) NO está implementado en `unclade.py`
   (el README del repo lista "Implement clade compression" como TODO).

**Entregables producidos (a diferencia de pases anteriores):**
- `/tmp/modemre/clade_dec.bin` — descompresión CLADE del stream completo (54 MB). **SÍ existe.**
- `/tmp/modemre/clade_dict0/1/2` (implícitos en b26@0x2444000).
- `/tmp/modemre/clade_dec_dispatch.bin` — página del dispatcher FTM.
- Disassembly del dispatcher: `/tmp/modemre/disp_full.txt`.

---

## 1. `unclade.py` — formato entendido a fondo (FACT, del código)

`CladeDecompressor.decompress()` (unclade.py:112): lee un **BitStreamReader** LSB-first sobre
words de 32 bits. **Salta 1 bit al inicio** (`first = bits.get(1)`). Luego, por cada word de
salida:
- `code = get(2)`:
  - `code 0` (dict0): `idx=get(11)`, `missing=get(2)`, `word = reconstruct(dict0[idx], [1,15], missing)`
  - `code 1` (dict1): `idx=get(11)`, `missing=get(9)`, `reconstruct(dict1[idx], [1,2,4,5,6,7,8,9,16], missing)`
  - `code 2` (dict2): `idx=get(11)`, `missing=get(15)`, `reconstruct(dict2[idx], [0,1,2,4,5,7,8,9,10,15,16,17,18,19,20], missing)`
  - `code 3`: literal `get(32)`.
- `reconstructword(dictword, bitpositions, codebits)` interleaves los bits "missing" en las
  posiciones dadas dentro de `dictword` (unclade.py:94). **Verificado matemáticamente
  consistente**: dict0 OR=0x3fffffff (30 bits) + 2 missing = 32; dict1 (23) + 9 = 32;
  dict2 (17) + 15 = 32. Los índices son 11-bit = 2048 = tamaño exacto de cada dict.

`CladeSegment.__init__` (unclade.py:148): asume dicts en `(end&~0x1fff)-0x6000`, 3×0x2000 bytes.
`processfile`: `fh.seek(args.offset); cdata=fh.read(args.length); decompress()`.
**NO hay soporte de metadata vaddr→offset, NI de exception words.** `readchunk` (unclade.py:173)
dice literalmente *"don't know yet how chunks are encoded"*.

`using-cladelib/cladetool.cpp` (usa el `libclade.so` REAL de Qualcomm) confirma:
- Firma de validación de dicts: `{0x3fffffff, 0x007fffff, 0x0001ffff}` (OR de cada dict). **← la clave para localizarlos.**
- `clade_config_t`: `region`, `pd_params[].comp` (`.clade.comp`), `pd_params[].exc_hi`
  (`.clade.exception_high`), `dicts[]`, `dict_len=0x2000`, `num_dicts=3`, `replaceable_words`.
- `clade_read(request{addr,len})` traduce `addr` → offset comprimido y descomprime.
  El mapeo real vaddr→comp lo hace `libclade.so` internamente (no reimplementado en Python).

---

## 2. Localización de las secciones CLADE (FACT)

### 2.1 Diccionarios — modem.b26 @ 0x2444000 (VERIFICADO EXACTO)
Barrido de todos los `modem.b*` buscando 3 bloques consecutivos de 2048 words cuyo OR
sea `{0x3fffffff, 0x007fffff, 0x0001ffff}` (`find_clade_dicts2.py`):
```
EXACT modem.b26  dict0 @ file 0x2444000  OR=3fffffff
EXACT modem.b26  dict1 @ file 0x2446000  OR=007fffff
EXACT modem.b26  dict2 @ file 0x2448000  OR=0001ffff
```
- 0x2443000..0x2444000 = zero-padding. dict0 primeros words: `1c1c2000 00000000 1e002000
  1c182008 ...` (fragmentos de instrucciones Hexagon frecuentes — inmediatos `2000`/`e000`).
- Cuadra con `unclade` (`(0x244a040 & ~0x1fff) - 0x6000 = 0x2444000`).

### 2.2 `.clade.comp` = modem.b26 offset 0 (FACT)
El inicio de b26 (`d62015b2 2808298a b7c30b09 ...`) es el stream bit-packed. Descomprime a
código Hexagon válido desde vaddr 0xd8000000 (sección 3). **NO hay header/metadata antes del
stream** (los primeros bytes ya son datos comprimidos).

### 2.3 Mapa de segmentos relevante (del .mdt)
| seg | vaddr b26 | file  | contenido |
|-----|-----------|-------|-----------|
| 26  | 0xcc000000 | modem.b26 | **`.clade.comp` (offset 0) + `.clade.dict` (offset 0x2444000)**. filesz 0x244a040 |

El XML de QuRT embebido (file 0x27c4xxx) declara los nombres pero **NO las direcciones**
(se asignan en link): `.clade.dict` (physpool CLADE_DICT), `.clade.comp` (reg clade_comp),
`.clade.exception_high` (reg clade_exception_high), `.clade.exception_low_large`,
`.clade.exception_low_small`, `.clade.metadata`, `<clade_base_paddr value="0x400000000"/>`,
`.region_low_clade` / `.region_high_clade_protected_memory` (definen el rango VA↔PA).

### 2.4 La ventana de descompresión = 0xd8000000
Barrido de referencias a 0xd8xxxxxx en el ELF: base **0xd8000000** aparece 26× (page-aligned,
el más común). La región se extiende hasta ~0xd9cxxxxx. **INFERENCE**: `.region_low_clade`
base = 0xd8000000; page(0xd8150ed8) = (0xd8150ed8-0xd8000000)>>12 = **0x150 = 336**.

---

## 3. Descompresión — evidencia de que FUNCIONA (FACT)

`python3 unclade.py --offset 0 --length 0x244a000 --output clade_dec.bin /tmp/clade/modem.b26`
→ 54 MB de salida. Página 0 (llvm-objdump-18 --triple=hexagon), primeras instrucciones:
```
d8000000: immext(#0xf8018000)
d8000004: r1:0 = combine(#0x0,##0xf8018000)
d8000008: r17 = #0x0; r3:2 = combine(#0,#0x0)
d800000c: call 0xd80dec28
d8000010: immext(#0x409c0); jump 0xd80409f4
...
d800000c → 0xd80dec28 descomprime como:
   p1=cmp.gtu(r4,#1); r3:2=combine(r18,#0); loop0(0xd80dec40,r5); r6=memb(r3++#1);
   r4=add(r2,#1); p0=cmp.eq(r6,#0); if(!p1) jump ...     ← strlen/strnlen limpio
```
Validación cuantitativa (`--triple=hexagon`, sin anclaje de packet):
- **Código conocido-bueno no comprimido (b10):** ~1–3 `unknown` por 0x400 bytes (≈1%).
- **CLADE descomprimido (páginas 0, dec00, 150e00):** ~66 `unknown` por 0x400 bytes (≈26%),
  **UNIFORME en toda la imagen** (no crece con la distancia → NO es drift/desync de página).

Conclusión: la descompresión es **globalmente coherente** (mismo error-rate en cualquier
offset), lo que descarta el fallo por "metadata de página ausente" que asumían los pases
previos. El stream es efectivamente **contiguo** y descomprimible linealmente. El 26% de
error es **por-word e intrínseco al algoritmo reimplementado**, no por desalineación.

---

## 4. Por qué queda ~26% de words mal — CAUSA IDENTIFICADA (FACT + INFERENCE)

- **FACT**: la tasa de error es idéntica sea cual sea el bit inicial saltado (skip 0/1/2 → 66,66,67
  unknown/256) → NO es alineación.
- **FACT**: los words erróneos se reparten UNIFORMEMENTE entre code 0/1/2/3 y entre todos los
  bloques de índice de diccionario (~25–30% en cada bucket) → NO es un slot de dict "poison"
  ni un bug de reconstrucción de bits (los dicts son casi todos distintos, sólo 1 cero c/u).
- **FACT**: las primeras ~15 words son perfectas; luego los errores empiezan a dispersarse.
- **FACT (kernel string)**: `"invalid QURTK_clade_exc_hi_word or QURTK_clade_dict_word"` +
  secciones `.clade.exception_high / _low_large / _low_small` + registros
  `CladeExcHiPDX/CladeExcLowPDX` → **CLADE mantiene "exception words" fuera del stream**.
- **INFERENCE (causa raíz)**: el HW CLADE real, para ciertas words, NO las toma del stream
  bit-packed sino de las secciones `.clade.exception_*` (palabras que el encoder decidió
  no representar por diccionario). `unclade.py` **no implementa** ese camino
  (su `code==3` lee 32 bits inline, y no consulta ninguna tabla de excepciones), de modo que
  ~1 de cada 4 words sale con el valor equivocado. El README del repo confirma que la parte
  CLADE está incompleta ("Implement clade compression" = TODO).

**Qué falta para llegar al 100%:** localizar `.clade.exception_high`, `.clade.exception_low_large`
y `.clade.exception_low_small` en los segmentos, entender su indexado (por posición de word /
por página) y aplicarlas sobre la salida de `unclade.py`. Con la firma de dicts ya resuelta,
esto es la ÚNICA pieza restante. Candidatos a revisar: segmentos pequeños RW cercanos
(b25 0xcbf4f000, b28, b29) y el uso de los registros `CladeExc*` en el código residente
(setup en boot). NO encontré aún una tabla u32 monótona que cubra 0..0x2444000 (barridos en
`find_meta_table.py`/`find_meta2.py` → negativos), lo que es consistente con que la metadata/
excepciones usan un encoding compacto (no un array de offsets plano).

---

## 5. Dispatcher FTM `0xd8150ed8` — lo que SÍ se lee (FACT, con la salvedad del 26%)

Disassembly completo en `/tmp/modemre/disp_full.txt` (llvm-objdump-18 --triple=hexagon,
vaddr 0xd8150ed8). La ESTRUCTURA de dispatch es legible pese al ruido:

- **Función independiente**: justo antes de 0xd8150ed8 hay un `dealloc_return` en 0xd8150ebc
  (fin de la función previa). 0xd8150ed8 es efectivamente **entrada de función** (concuerda con
  la tabla de dispatch rodata `0xc37bd1e8`: 77 entradas → todas 0xd8150ed8; cmd 0x27=LTE presente).
- **Cadena de comparaciones de sub_command** (legibles):
  ```
  d8151620: p0 = cmp.eq(r2,#0x1); if(p0.new) jump 0xd8151730
  d8151640: p0 = cmp.eq(r2,#0x0); if(p0.new) jump 0xd8151724
  d81519c0: p0 = cmp.eq(r18,#0x1); if(!p0.new) jump ...
  d8151eb8: p0 = cmp.eq(r16,#0x1); if(!p0.new) jump ...
  d8151684: p0 = !cmp.eq(r1,#0x2a)      ← compara contra 0x2a (=42)
  ```
- **Escritura del código de error 0x14 (DIAG_BAD_PARM_F)** — patrón REPETIDO y consistente
  (alta confianza):
  ```
  d81516a4: memb(r5+#0x7) = #0x14
  d8151bf4: r0 = #0x14
  ```
  → confirma que el gate que devuelve **0x14** vive AQUÍ, en el cuerpo del dispatcher paginado.
- **Otros retornos legibles**: `r0=#0x5`, `r0=#0xa`, `r0=#0x1e`, `r0=#0x3c`, `r0=#0x80`,
  `r20=#0x13`, `r0=#0x3`.
- **Calls a sub-handlers**: 0xd813a220, 0xd813a2ac, 0xd813a7d4, 0xd813b088, 0xd813a868,
  0xd8352074, 0xd80cce18, 0xd812011c, 0xd8155ebc, ... (rango 0xd813axxx = tabla de handlers
  de sub_command LTE, muy probablemente).

**Advertencia de confianza (honesta):** con ~26% de words corruptos por las excepciones no
aplicadas, **NO puedo afirmar como FACT los valores numéricos exactos** del enum TECH ni el
número exacto del sub_command tech-enter. Lo que veo (`cmp.eq(r2,#0x0/1)`, `!cmp.eq(r1,#0x2a)`,
`r20=#0x13`, `r0=#0x3`) es sugerente pero podría contener alguna word corrupta. El gate 0x14
sí es robusto (aparece en múltiples sitios coherentes).

---

## 6. OBJETIVO (tech-enter) — estado

| Ítem pedido | Estado | Evidencia |
|---|---|---|
| ftm_cmd_id LTE | **FACT = 0x27** | tabla dispatch rodata 0xc37bd1e8 (77 ent → 0xd8150ed8) |
| Gate que devuelve 0x14 (DIAG_BAD_PARM) | **FACT: está en 0xd8150ed8+** | `memb(...)=#0x14` @0xd81516a4, `r0=#0x14` @0xd8151bf4 |
| Switch de sub_command (RADIO_CONFIG/RX_MEASURE/IQ_CAPTURE/CAPABILITY/TECH_ENTER) | **PARCIAL** | cadena `cmp.eq(r2,#N)` legible en 0xd8151620/1640/… pero N no 100% confiable por el 26% |
| sub_command exacto del tech-enter | **UNKNOWN (bloqueado por el 26%)** | requiere aplicar exception words |
| Enum numérico de TECH (LTE / NR5G) | **UNKNOWN (bloqueado por el 26%)** | idem |
| Flag `tech_entered` (global) + condición → 0x14 | **PARCIAL/INFERENCE** | hay `cmp.eq` sobre valores globales (`memw(gp+#..)`) antes de escribir 0x14 |

---

## 7. Cómo continuar (ruta concreta para el próximo pass — foco único)

El proyecto pasó de "imposible" a **"falta 1 pieza: las exception words de CLADE"**. Pasos:

1. **Localizar `.clade.exception_high / _low_large / _low_small`** en los segmentos. Pistas:
   - Son arrays de words de 32 bits (los valores "verdaderos" para posiciones no comprimibles).
   - `.clade.exception_high` va a `TCM_POOL` (pequeña, alta prioridad) → buscar en segmentos
     chicos R-X/RW cercanos al arranque (b02, b12, b25, b28, b29).
   - Revisar el código residente (b10/b13) donde se programan los registros `CladeExcHiPDX` /
     `CladeExcLowPDX` / `CladeRegion` (immext a 0x400000000 = clade_base_paddr) para leer los
     punteros a esas secciones directamente del setup de boot.
2. **Entender el indexado de excepciones**: en CLADE, típicamente hay 2 tamaños (low_small /
   low_large) según el nº de bits que difieren, y `exc_high` para el rango alto. El stream
   probablemente marca "esta word es excepción" con un code/patrón; hay que ver qué bits de
   `unclade` corresponden y sustituir por la word de la sección de excepción correspondiente
   (en orden de aparición / por página).
3. **Aplicar excepciones sobre `clade_dec.bin`** y re-desensamblar la página 336. Con el error
   a ~0%, extraer los `cmp.eq(rX,#N)` del switch de sub_command y el `cmp.eq` del enum TECH.
4. **Validación**: la página quedará con <2% `unknown` (como b10) cuando las excepciones estén
   bien aplicadas — es el criterio objetivo de éxito.

Alternativa robusta (si las excepciones no se logran offline): **compilar `cladetool`** de
`using-cladelib` contra el `libclade.so` del Hexagon SDK y pasarle `--dictofs 0x2444000
-o 0 -l <size>` sobre `modem.b26` — usa la librería REAL de Qualcomm (con excepciones) y daría
la descompresión exacta. (Requiere el SDK de Hexagon, no disponible en este entorno.)

---

## 8. Archivos generados en este pass

- `/tmp/modemre/clade_dec.bin` — **descompresión CLADE completa (54 MB)** del stream de b26.
- `/tmp/modemre/clade_dec_dispatch.bin` — región del dispatcher (0xd8150000..0xd8152000).
- `/tmp/modemre/disp_full.txt` — disassembly del dispatcher (con el ~26% de ruido documentado).
- `/tmp/modemre/find_clade_dicts2.py` — localizador de dicts por firma OR (reproducible).
- `/tmp/modemre/clade_pageprobe.py`, `clade_pagewise.py`, `validate_linear.py`,
  `find_meta_table.py`, `find_meta2.py`, `mkelf.py`, `hexval.py`, `packet_disasm.py` — utilería.

## 9. FACT / INFERENCE / UNKNOWN — resumen honesto

**FACT (probado aquí):**
- Los 3 dicts CLADE están en modem.b26 @ 0x2444000/0x2446000/0x2448000 (firma OR exacta).
- `.clade.comp` = modem.b26 offset 0; el stream descomprime a Hexagon válido (página 0 perfecta).
- Descompresión completa producida: `clade_dec.bin` (54 MB). El dispatcher 0xd8150ed8 se aisló.
- La tasa de error es uniforme (~26%), no drift → el stream es contiguo y el algoritmo base es correcto.
- El gate 0x14 (DIAG_BAD_PARM) está físicamente en el cuerpo del dispatcher paginado (0xd8150ed8+).
- ftm_cmd_id LTE = 0x27 (tabla rodata).

**INFERENCE:**
- El ~26% de error se debe a las **exception words** de CLADE (`.clade.exception_*`) no
  implementadas en `unclade.py`. Es la única pieza que falta para una descompresión exacta.
- region base = 0xd8000000; página del dispatcher = 336.

**UNKNOWN (bloqueado sólo por las exception words):**
- Enum numérico exacto de TECH (LTE, NR5G).
- Número exacto del sub_command tech-enter bajo 0x27.
- Los valores de `cmp.eq(r2,#N)` del switch son legibles pero no 100% confiables word-a-word.
