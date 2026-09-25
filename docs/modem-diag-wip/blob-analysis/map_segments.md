# Mapa de segmentos / memoria — baseband Qualcomm SM6375 (Moto G82 5G)

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133**
Imagen: `modem.mdt` (ELF32 header) + `modem.b00..b34` (split MBN).
Machine: **0xa4 = QUALCOMM DSP6 (Hexagon v66)**. e_type=ET_EXEC. e_entry(paddr)=**0x8b800000**.
e_phoff=0x34, e_phentsize=32, **e_phnum=36** (índices 0..35). e_flags=0x67.

Leyenda: **FACT** = verificado byte/estructura en esta imagen · **INFERENCE** = deducción con base · **UNKNOWN** = no determinado estáticamente aquí.

> Nota de direcciones: cada program header tiene **p_vaddr ≠ p_paddr**. p_paddr es contiguo
> desde 0x8b800000 (layout de carga físico); **p_vaddr es la dirección virtual real que usa el
> código en runtime** (0xc0.. / 0xc3.. / 0xc8.. / 0xca.. / 0xcc.. / 0xce.. / 0x1e4.. / 0xa1.. /
> 0x9b..). Todo el RE se hace contra **p_vaddr**. (FACT)

---

## 1. TABLA DE SEGMENTOS COMPLETA (36 program headers) — FACT

Parseado directo de `modem.mdt` (ELF PH). `p_flags` bits: 0x8000000 = flag QC "segmento MBN";
bits bajos 4=R 2=W 1=X. `bfile` = fichero `modem.bNN` correspondiente (indexado por orden de PH).

| idx | bNN | p_vaddr | p_paddr | p_filesz | p_memsz | perm | contenido | notas |
|----:|-----|---------|---------|----------|---------|------|-----------|-------|
| 0 | b00 | 0x00000000 | 0x00000000 | 0x4b4 | 0 | --- | **ELF/hash header MBN** | no cargable; metadata firma |
| 1 | b01 | 0x9b800000 | 0x9b800000 | 0x2068 | 0x3000 | --- | header/QC metadata | no R/W/X |
| 2 | b02 | 0xc0800000 | 0x8b800000 | 0x2930 | 0x3000 | **R-E** | **código** (arranque/vector) | plano |
| 3 | b03 | 0xa44f9000 | 0x8b803000 | 0x2460 | 0x2460 | R-- | rodata ("wlan…") | plano |
| 4 | b04 | 0xc0900000 | 0x8b900000 | 0x3bd40 | 0x3c000 | **RWE** | código+datos RW | plano |
| 5 | b05 | 0xc0980000 | 0x8b980000 | 0x2c190 | 0x2d000 | **RWE** | código+datos RW | plano |
| 6 | b06 | 0xc09ad000 | 0x8b9ad000 | 0x3e4a0 | 0x3f000 | RW- | datos RW (init) | plano |
| 7 | b07 | 0xc09ec000 | 0x8b9ec000 | 0xe3b0 | 0xf000 | R-- | rodata ("Inva…") | plano |
| 8 | b08 | 0xc09fb000 | 0x8b9fb000 | 0x2380c | 0x24000 | **RWE** | código+datos RW | plano |
| 9 | b09 | 0xc0a1f000 | 0x8ba1f000 | 0x60064 | 0x61000 | **RWE** | código+datos RW | plano |
| 10 | b10 | 0xc0a80000 | 0x8ba80000 | 0x29ab14 | 0x29b000 | **R-E** | **código grande (DIAG core)** | plano, 2.7MB |
| 11 | b11 | 0xc0d20000 | 0x8bd20000 | 0x9140 | 0xa000 | R-- | rodata | plano |
| 12 | b12 | 0xc0d30000 | 0x8bd30000 | 0x6000 | 0x6000 | **R-E** | **código** | plano |
| 13 | b13 | 0xc0d36000 | 0x8bd36000 | 0x31f1b0 | 0x320000 | **R-E** | **código grande (DIAG/dlpager, -O0)** | plano, 3.2MB |
| 14 | b14 | 0xc1080000 | 0x8c080000 | 0x40000 | 0x40000 | RW- | datos RW | plano |
| 15 | b15 | 0xc10c0000 | 0x8c0c0000 | 0x39c000 | 0x39c000 | RW- | datos RW | plano, 3.7MB |
| 16 | b16 | 0xc145c000 | 0x8c45c000 | **0** | 0x1000 | RW- | **.bss** | SIN fichero |
| 17 | b17 | 0xc145d000 | 0x8c45d000 | 0x23aeb8 | 0x23b000 | RW- | datos RW (init) | plano, 2.3MB |
| 18 | b18 | 0xc16c0000 | 0x8c6c0000 | 0xa9cb0 | 0xaa000 | RW- | datos RW (init) | plano |
| 19 | b19 | 0xc1780000 | 0x8c780000 | 0x2d0000 | 0x2d0000 | RW- | datos RW | plano, 2.9MB |
| 20 | b20 | 0xc1a50000 | 0x8ca50000 | 0x96e988 | **0x1b03000** | RW- | datos RW + **.bss grande** | file 9.9MB, mem 28MB |
| 21 | b21 | 0xc3553000 | 0x8e553000 | 0xd315f4 | **0x5616000** | **R--** | **RODATA/tablas (DIAG, FTM, TLV)** | file 13.8MB, mem 90MB sparse |
| 22 | b22 | 0xa1073000 | 0x93b69000 | 0x2d | 0x2d | R-- | rodata mínima (45 B) | plano |
| 23 | b23 | 0xc8b6a000 | 0x93b6a000 | 0x5cdd97 | 0x5ce000 | **RW-** | **DATA RW init (master tables DIAG, g-tablas)** | file 6.1MB |
| 24 | b24 | 0xc9138000 | 0x94138000 | **0** | **0x2e17000** | RW- | **.bss RAM RUNTIME (48MB)** | SIN fichero |
| 25 | b25 | 0xcbf4f000 | 0x96f4f000 | 0x19900 | 0x1a000 | RW- | datos RW | plano |
| 26 | b26 | 0xcc000000 | 0x97000000 | 0x244a040 | 0x244b000 | RW-* | **`.clade.comp` (código comprimido CLADE) + `.clade.dict`** | file 38MB |
| 27 | b27 | 0xce480000 | 0x99480000 | 0x11922f | 0x11a000 | R-- | **RF ML1 LTE (ZLIB-comprimido)** | file 1.1MB → 6.3MB descomp |
| 28 | b28 | 0xce59a000 | 0x9959a000 | 0x16000 | 0x16000 | RW- | datos RW | plano |
| 29 | b29 | 0xce5b1000 | 0x995b1000 | 0x1064 | 0x2000 | R-- | rodata ("diag…") | plano |
| 30 | b30 | 0x1e400000 | 0x99600000 | 0x14044 | 0x15000 | **R-E** | **código (TCM/island?)** | VA baja 0x1e4 |
| 31 | b31 | 0x1e415000 | 0x99615000 | 0x1378 | 0xa000 | RW- | datos RW (TCM) | VA baja |
| 32 | b32 | 0x1e41f000 | 0x9961f000 | 0xb0 | 0x1000 | RW- | datos RW (TCM) | VA baja |
| 33 | b33 | 0xa1000000 | 0x99700000 | 0x72f48 | 0x72f48 | R-- | rodata | plano, 470KB |
| 34 | b34 | 0xa44fc000 | 0x99773000 | 0xe4 | 0xe4 | R-- | rodata mínima | plano |
| 35 | — | 0xa44fd000 | 0x99774000 | **0** | 0x208c000 | R-- | **.bss R-- (34MB)** | SIN fichero (no hay b35) |

\* b26 tiene perm RW- en el PH pero el XML QuRT declara sus sub-secciones CLADE como
`mapping="rx"` → la ventana descomprimida (0xd8xxxxxx) es **ejecutable**. El b26 RW- es el
*backing* comprimido en DDR. (FACT)

**Clasificación:**
- **Código plano (R-E / RWE):** b02, b04, b05, b08, b09, b10, b12, b13, b30. (FACT)
- **Código comprimido (CLADE, no en ningún PH ejecutable):** ventana virtual 0xd8xxxxxx,
  backing = b26. (FACT)
- **Datos comprimidos (ZLIB):** b27 (RF ML1 LTE). (FACT)
- **RODATA/tablas:** b03, b07, b11, b21 (la grande), b22, b29, b33, b34. (FACT)
- **DATA RW init:** b06, b14, b15, b17, b18, b19, b20(parte), b23, b25, b28, b31, b32. (FACT)
- **.bss (filesz=0):** b16, **b24 (48MB, la RAM runtime de los globals)**, b35 (34MB), y las
  colas sparse de b20 (mem 28MB vs file 9.9MB) y b21 (mem 90MB vs file 13.8MB). (FACT)

---

## 2. MAPA DE VA WINDOWS — propósito - FACT salvo lo indicado

| Ventana VA | Segmento/backing | comprimida | contenido | descomp. disponible |
|-----------|------------------|-----------|-----------|---------------------|
| **0xc0xxxxxx** | b02/b04/b05/b08/b09/b10/b12/b13 (R-E/RWE) | NO | **DIAG core, dlpager, ruteo, arranque** (código plano) | sí (nativo en ELF) |
| **0xc09.. / 0xc0d..** | b06/b07/b11 | NO | rodata/datos de soporte | sí |
| **0xc1xxxxxx** | b14/b15/b17/b18/b19/b20 (RW-) | NO | datos RW init + colas .bss | sí (parte inicializada) |
| **0xc3xxxxxx** | **b21 (R--)** | NO | **RODATA: tablas DIAG/FTM, TLV, format-strings, dispatch table @0xc37bd1e8, campos tech-enter @0xc37bee24, nombres RAT @0xc37857dc** | sí (nativo) |
| **0xc8xxxxxx (0xc8b..–0xc9137)** | **b23 (RW-)** | NO | **DATA init: master table DIAG @0xc8dc3b54, descriptor dlpager @0xc8d0a338** | sí (nativo) |
| **0xc906xxxx** | **b23 (RW-)** | NO | **g-tablas de params por comando FTM (g16 tech-enter @0xc906c8e8, grupos @0xc906c438)** | sí (nativo) |
| **0xca6xxxxx / 0xca7xxxxx / 0xca9xxxxx / 0xcaaxxxxx** | **b24 (RW-, filesz=0)** | N/A | **RAM RUNTIME (.bss) — todos los globals de estado FTM/RFTEST** | N/A (se rellena en runtime; cero al boot) |
| **0xcbf4xxxx** | b25 (RW-) | NO | datos RW | sí |
| **0xcc000000..0xce44b000** | **b26 (RW-)** | **CLADE** | **`.clade.comp` (código comprimido) @off0 + `.clade.dict` @0x2444000** | es el backing; se descomprime a 0xd8xxxxxx |
| **0xce444000** | b26 | — | 3 diccionarios CLADE (3×0x2000) | sí (usados por libclade.so) |
| **0xce480000..0xce59a000** | **b27 (R--)** | **ZLIB (78 9c)** | **RF ML1 LTE (código/datos)** | **sí → seg27_dec.bin (6.3MB, base 0xce480000)** |
| **0xce59.. / 0xce5b..** | b28/b29 | NO | datos RW / rodata diag | sí |
| **0x1e4xxxxx** | b30/b31/b32 (R-E/RW-) | NO | **código+datos TCM/island** (VA baja) | sí (nativo) |
| **0xa1000000 / 0xa44fxxxx** | b33/b34 (R--) | NO | rodata | sí |
| **0xd4400000..0xd6539000** | **sin PH** (pool dlpager distinto) | (paginado) | remap dlpager pool #2 (descriptor @0xc8d0a338) | **NO** (no está en el ELF) |
| **0xd8xxxxxx** (≈0xd8000000..≥0xdb397464) | **sin PH; backing = b26 CLADE** | **CLADE** | **CÓDIGO FTM/DIAG/RFTEST descomprimido: dispatcher 0xd8150ed8, handlers FTM, UNPACK TLV, tech-enter** | **sí (parcial) → clade_dec_full.bin 10MB + clade_dec.bin 54MB** |

### Detalle del descriptor dlpager @0xc8d0a338 (b23) — FACT
```
comp_start  = 0xcc000000   (= b26)
comp_end    = 0xce13a000
remap_start = 0xd4400000
remap_end   = 0xd6539000
field       = 0x00c00000
```
**INFERENCE:** ese descriptor es de un pool de paginado que remapea a **0xd4400000**, NO al
window 0xd8xxxxxx donde vive el dispatcher. La ventana 0xd8xxxxxx (`.region_low_clade`) es el
**pool CLADE de código** (XML: `mapping="rx"`), servido por la MMU al fallar página; su base
(0xd8000000) se dedujo de 26 referencias page-aligned en el código plano. **INFERENCE.**

### XML QuRT embebido (@ modem_full.elf off 0x27c4xxx) — FACT
Declara las secciones CLADE (nombres, sin direcciones — se asignan en link):
```
.clade.dict            physpool=CLADE_DICT   mapping=rx  tlb_lock=boot
.clade.comp            clade_register=clade_comp  mapping=rx  tlb_lock=boot
.clade.exception_high  physpool=TCM_POOL     mapping=rx   (cache_policy=1)
.clade.exception_low_large / _small          mapping=rx  tlb_lock=boot
.clade.metadata        mapping=rx  tlb_lock=boot
.region_low_clade  = clade_register clade_region_low_pd0
.region_high_clade_protected_memory = clade_region_high_pd0
<clade_base_paddr value="0x400000000"/>
<pool_allocation_order value="address"/>
```
→ Las exception words y metadata CLADE están en **TCM/pools que NO son program headers** del
MBN; por eso `unclade.py` (Python) falla en ~26% de words — sólo `libclade.so` (el codec real
de QC) las resuelve internamente. (FACT)

---

## 3. CLADE — estado de la compresión de código - FACT

### 3.1 Diccionarios (confirmados byte a byte)
En **modem.b26**, al final del segmento, 3 diccionarios de **2048 words (0x2000 bytes) cada uno**:
```
dict0 @ file 0x2444000   OR de todas las words = 0x3fffffff  (30 bits)
dict1 @ file 0x2446000   OR = 0x007fffff  (23 bits)
dict2 @ file 0x2448000   OR = 0x0001ffff  (17 bits)
```
Firma exacta `{0x3fffffff,0x007fffff,0x0001ffff}` de `cladetool.cpp`. Cuadra con
`(0x244a040 & ~0x1fff) - 0x6000 = 0x2444000`. Bytes verificados:
```
dict0: 00201c1c 00000000 00200820 1e08181c ...
dict1: 7eff2d00 7cff2d00 40002d00 42002d00 ...
dict2: 20780000 20e60100 a0430100 e1e00000 ...
```
**FACT.**

### 3.2 `.clade.comp`
= **modem.b26 offset 0** (stream bit-packed CLADE, sin header). Primeros bytes:
`b2 15 20 d6 8a 29 08 28 ...` (entropía 7.77). **FACT.**

### 3.3 Traducción de request
```
.clade.comp  = b26 @ 0            (VA backing 0xcc000000)
.clade.dict  = b26 @ 0x2444000    (VA 0xce444000), 3×0x2000
req_va(comp) = 0xcc000000 + (output_vaddr - 0xd8000000)
```
El extractor `clade_extractor_sm6375` + `libclade.so` descomprime CUALQUIER rango
HW-accurate (0.14% invalid en 1.048.333 instrucciones). **FACT.**

### 3.4 Proporción código CLADE vs plano
- **Código plano en el ELF (R-E/RWE):** suma de filesz de b02,b04,b05,b08,b09,b10,b12,b13,b30
  ≈ **0x3f0 0000 ≈ 6.6 MB**. (FACT)
- **Código CLADE (backing comprimido b26):** filesz 0x244a040 ≈ **38 MB comprimido**;
  descomprime a un window ≥ 54 MB (clade_dec.bin llega a 0xdb397464 = **~54 MB** y no se
  confirmó el final). **INFERENCE:** el grueso del código del modem (dispatch FTM, RFTEST,
  ML1, stacks de protocolo) vive CLADE-comprimido → **>80% del código total es CLADE**, el
  plano es sólo el arranque + DIAG core + dlpager + TCM. (FACT proporción aproximada / el
  tamaño exacto descomprimido total = **UNKNOWN** hasta barrer todo el window.)

### 3.5 ¿Otras secciones comprimidas (q6zip)?
- **b27 = ZLIB** (magic `78 9c`), NO q6zip; descomprime limpio con `zlib.decompress` →
  0x61681b bytes = **seg27_dec.bin** (match exacto). Es RF ML1 LTE. **FACT.**
- **q6zip clásico (dict+ptrlist):** NO hallado en el ELF reensamblado (6 barridos: 0 headers
  q6zip auto-consistentes, 0 ptr-lists monótonas hacia b26). El mecanismo de b26 es **CLADE**,
  no q6zip. **FACT.**
- Barrido de las 35 bNN por magic zlib: **sólo b27** lo tiene. **FACT.**
- **INFERENCE:** no hay otras secciones comprimidas más allá de CLADE(b26) y ZLIB(b27).

---

## 4. QUÉ NO TENEMOS DESCOMPRIMIDO (puntos ciegos) - FACT

### 4.1 Ventana CLADE 0xd8xxxxxx — parcial
- **Tenemos:** `clade_dec_full.bin` = 0xd8000000..**0xd8a00000** (10 MB, HW-accurate, limpio).
  `clade_dec.bin` = 0xd8000000..**0xdb397464** (54 MB, HW-accurate).
- **FALTA:** todo lo que exista **por encima de 0xdb397464** en el window CLADE (final exacto
  del window = UNKNOWN; el XML no da tamaño, sólo `.region_low_clade`). Si el código sube más
  allá, hay que extraerlo con `clade_extractor_sm6375 <out> <req_va=0xcc000000+(va-0xd8000000)> <len>`.
- **Punto ciego real:** el **límite superior** del window CLADE. Backing = b26. (INFERENCE)

### 4.2 Ventana dlpager 0xd4400000..0xd6539000 — NO tenemos
- Es un **pool de paginado distinto** (descriptor @0xc8d0a338). **No está en ningún PH** del
  MBN. Su backing comprimido (comp_start 0xcc000000 se solapa con b26, comp_end 0xce13a000)
  sugiere que **también se sirve desde b26/CLADE** pero remapeado a 0xd4. **No descomprimido.**
  - **INFERENCE:** requeriría el mapa de páginas dlpager (ausente del ELF) o volcado en vivo.
  - **Estado: PUNTO CIEGO.** (FACT que no está; INFERENCE de que su fuente es b26.)

### 4.3 Segmentos .bss (filesz=0) — nada que descomprimir
- b16, **b24 (48MB — la RAM de los globals)**, b35 (34MB), colas sparse de b20/b21. Son cero
  al boot y se rellenan en runtime → **no hay contenido estático** que recuperar. (FACT)

### 4.4 Resumen de rangos faltantes

| Rango VA faltante | ¿en qué backing estaría? | por qué falta |
|-------------------|--------------------------|---------------|
| 0xd8a00000..0xdb397464 | b26 (CLADE) | fuera de clade_dec_full.bin (10MB); **sí** en clade_dec.bin (54MB) |
| > 0xdb397464 (si existe) | b26 (CLADE) | límite superior del window no determinado |
| 0xd4400000..0xd6539000 | b26/CLADE remap dlpager | mapa de páginas ausente del ELF |
| b24 (0xc9138000..0xcbf4f000) | .bss | contenido sólo en runtime |
| b35 (0xa44fd000..) | .bss | contenido sólo en runtime |

---

## 5. LAYOUT DE LA RAM DE DATOS — dónde caen los globals - FACT

**TODOS** los globals conocidos caen en el **segmento 24 (b24)**:
```
p_vaddr = 0xc9138000   p_memsz = 0x2e17000 (48 MB)   p_filesz = 0 (.bss)   perm RW-
```
→ Es un **.bss puro** (sin fichero `modem.b24`). **Cero-inicializado al boot** por el runtime
(QuRT), NO cargado desde el MBN. Su contenido en tiempo de análisis estático es **desconocido**
(se puebla en runtime por los inicializadores C / registro dinámico). (FACT)

| Global | Segmento | Tipo init | Rol (según pases previos) |
|--------|----------|-----------|---------------------------|
| 0xca79a850 | **b24 (.bss)** | bss → runtime | tabla RFTEST runtime |
| 0xca7897b0 | **b24 (.bss)** | bss → runtime | tech flags |
| 0xca79c494 | **b24 (.bss)** | bss → runtime | carrier ptr |
| 0xca65b414 | **b24 (.bss)** | bss → runtime | RFDEBUG table (config registro FTM) |
| 0xca65d640 | **b24 (.bss)** | bss → runtime | module ctx |
| 0xca733d10 | **b24 (.bss)** | bss → runtime | (estado FTM) |
| 0xca9ef490 | **b24 (.bss)** | bss → runtime | (estado FTM) |
| 0xcaad9d00 | **b24 (.bss)** | bss → runtime | (estado FTM) |

**Consecuencias para el RE / portado:**
- Ninguno de estos globals tiene valor inicial en el firmware; **no** hay que buscarlos en
  `.data`. Se construyen por el código de init/registro (que vive CLADE en 0xd8xxxxxx) y por el
  registro dinámico de handlers FTM (funciones tipo 0xd816cf58 que escriben @0xca65b414). (FACT
  ubicación .bss / INFERENCE quién los escribe.)
- La **DATA inicializada** de verdad (tablas maestras, g-tablas, descriptores) está en **b23**
  (RW-, VA 0xc8b6a000..0xc9138000) y **b20/b14/b15/b17/b18/b19** — esos sí traen bytes del MBN.
- El límite b23→b24 (0xc9138000) es exactamente donde termina la DATA con fichero y empieza la
  RAM .bss runtime. (FACT)

---

## 6. FACT / INFERENCE / UNKNOWN — cierre con evidencia

**FACT (verificado byte/estructura en esta imagen):**
- 36 program headers parseados de `modem.mdt`; tabla completa §1 (p_vaddr/p_paddr/filesz/memsz/flags).
- Código plano = b02,b04,b05,b08,b09,b10,b12,b13,b30. RODATA grande = b21. DATA init = b23 (+otros).
- **b24 = .bss 48MB (filesz=0)**; los 8 globals listados caen todos ahí → cero al boot.
- **b26 = CLADE:** dicts @0x2444000/6000/8000 (OR 0x3fffffff/0x007fffff/0x0001ffff), `.clade.comp` @0.
- **b27 = ZLIB** (`78 9c`) → descomprime exacto a seg27_dec.bin (0x61681b B, RF ML1 LTE).
- La ventana 0xd8xxxxxx **NO está en ningún PH**; es código CLADE descomprimido en runtime
  (XML: `.clade.comp` mapping=rx). Backing = b26. req_va = 0xcc000000+(va-0xd8000000).
- Descompresión CLADE HW-accurate resuelta (libclade.so, 0.14% invalid). Tenemos 0xd8000000..
  0xdb397464 en clade_dec.bin; 0xd8000000..0xd8a00000 en clade_dec_full.bin.
- Descriptor dlpager @0xc8d0a338: comp 0xcc000000..0xce13a000, remap 0xd4400000..0xd6539000.
- Tablas clave: dispatch FTM @0xc37bd1e8 (b21), campos tech-enter @0xc37bee24 (b21),
  g16 params @0xc906c8e8 (b23), master DIAG @0xc8dc3b54 (b23).

**INFERENCE:**
- >80% del código del modem es CLADE (plano ≈6.6MB vs CLADE ≥54MB descomprimido).
- Base del window CLADE = 0xd8000000 (26 refs page-aligned; XML no da la dirección).
- El pool 0xd4400000 se sirve también de b26 (comp_start solapa) pero remapeado; su mapa de
  páginas no está en el ELF.
- Los globals .bss de b24 se pueblan por el código de init/registro dinámico (en 0xd8xxxxxx).

**UNKNOWN (no determinable estáticamente en esta imagen):**
- Límite superior exacto del window CLADE (¿termina en 0xdb397464 o sigue?).
- Contenido runtime de b24/b35/.bss (sólo existe en vivo).
- Contenido de la ventana dlpager 0xd4400000..0xd6539000 (mapa de páginas ausente).
- Valores iniciales de los globals FTM (se construyen en runtime).

---

## 7. Cómo extraer lo que falta (reproducible)

```bash
# Código CLADE de cualquier rango del window 0xd8xxxxxx:
#   req_va = 0xcc000000 + (output_va - 0xd8000000)
QEMU_LD_PREFIX=/usr/x86_64-linux-gnu \
LD_LIBRARY_PATH=/tmp/qbs:/usr/lib/x86_64-linux-gnu \
qemu-x86_64-static /tmp/modemre/clade_extractor_sm6375 /tmp/out.bin <req_va_hex> <len_hex>

# p.ej. rango por encima de 0xd8a00000:  req_va = 0xcc000000+(0xd8a00000-0xd8000000)=0xcca00000
# Desensamblar:
python3 /tmp/modemre/mkelf.py /tmp/out.bin <output_va> /tmp/out.elf
llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66 /tmp/out.elf

# RF ML1 LTE (b27, zlib) — ya extraído:
python3 -c "import zlib;open('/tmp/seg27.bin','wb').write(zlib.decompress(open('/tmp/modemre/modem.b27','rb').read()))"
# base VA 0xce480000  ->  seg27_dec.bin
```
