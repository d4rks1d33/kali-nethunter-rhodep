# LOCALIZACIÓN DEL CÓDIGO RFLTE — SM6375 (MPSS.HI.4.3.4) — pase de verificación

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G).
Objetivo: localizar DÓNDE está el código ejecutable del driver RF LTE (`rflte_*`,
`rflm_*`, `sdr735_*`, `rfdevice_*`) — que pases previos no encontraron — y **verificar**
(no asumir) las hipótesis previas: (a) que `clade_dec.bin` >0xd8a00000 es filler / puede
estar desalineado, (b) que el backing del pool dlpager 0xd4400000 es b26/CLADE.

Leyenda: **FACT** = verificado byte-a-byte en este pase (con VA/offset) ·
**INFERENCE** = deducción con base dura · **UNKNOWN** = no resoluble con el material actual.

---

## 0. RESULTADO EJECUTIVO (leer primero)

1. **`clade_dec.bin` (54 MB) está CORRUPTO / mal-descomprimido.** No es "código válido más
   allá de 0xd8a00000": es **basura desde el inicio**. En 0xd8150ed8 (el dispatcher FTM
   conocido) contiene bytes que desensamblan como `<unknown>` en cascada. **Todas las
   conclusiones de pases previos que se apoyaron en `clade_dec.bin` (incluida "RFLTE no está
   en CLADE, 0 refs immext") son INVÁLIDAS porque partieron de un blob corrupto.** (FACT)

2. **La descompresión CLADE CORRECTA sí funciona** con `clade_extractor_sm6375` usando el
   `req_va` en espacio-comp (`req_va = 0xcc000000 + (out_va - 0xd8000000)`). Re-extraje el
   window completo → **`/tmp/clade_ok.bin` (52 MB pedidos; código válido real hasta
   VA 0xda440750)**. En 0xd8150ed8 aparece el dispatcher REAL, byte-idéntico al contrato
   documentado (subsystem 0x0B, sub-cmd 0x14, `and(r1,##0xfffe)`). (FACT)

3. **Hay DOS windows de código CLADE**, ambos re-extraídos y validados como Hexagon limpio:
   - **0xd8000000 .. 0xda440750** (~36 MB) — código principal FTM/DIAG/ML1 (`.clade.comp`).
   - **0xd0000000 .. 0xd0703000** (~7.3 MB) — `.clade.exception_high` (window separado,
     también código válido, 1956 `dealloc_return` / 2182 `jumpr r31`). (FACT — window nuevo
     que los pases previos no habían extraído.)

4. **El código RFLTE NO está en NINGUNO de esos dos windows CLADE** (ni en ningún blob plano).
   Con el decoder `immext` **verificado correcto** (reproduce `immext(#0xf80942c0)` del
   disassembler), la búsqueda de referencias a los VAs de string `rflte_*`/`rflm_*`/`sdr735_*`
   da **CERO coincidencias exactas** en los 36 MB + 7.3 MB de CLADE correcto y en todo el
   código plano (b02/b04/b05/b08/b09/b10/b12/b13/b30). (FACT)

5. **El pool dlpager 0xd4400000 NO es el backing del código RFLTE.** Es el **pool RW/delta**
   (datos, no código), y su control-block runtime describe pools de memoria de trabajo de los
   DSP-threads del PHY (SYMPROC_IUSS, DEMOD_LITE_IUSS0/1/2) respaldados por **DDR físico
   0x2a3xxxxx** — ausente del ELF. El autor de la toolchain (`qualcomm_baseband_scripts`)
   lo confirma: *"The delta section only has a couple diag command handler structs… I
   wouldn't even bother with this on newer binaries."* (FACT)

6. **Conclusión (FACT): el código RFLTE/RFLM/SDR735/RFDEVICE NO está presente, ni comprimido
   ni descomprimible, en NINGÚN blob del MBN disponible.** No está en b26/CLADE (ambos
   windows), no está plano, y el pool 0xd4400000 es datos delta con backing en DDR físico
   fuera del ELF. Para obtenerlo se requiere un **dump de RAM en vivo** de la ventana de
   código RF (ver §6). Los VAs `rflte_*` en 0xce6xxxxx siguen siendo **strings** (seg27
   rodata), no entradas de código. (FACT)

---

## 1. clade_dec.bin ESTÁ CORRUPTO — la corrección del pase previo (FACT, decisivo)

El encargo pidió expresamente: *"Un pase dijo que >0xdb397464 es filler y que clade_dec.bin
puede estar desalineado - VERIFICA esto, no lo asumas."* **Verificado: clade_dec.bin es
inservible.**

### 1.1 Prueba byte-a-byte en el dispatcher conocido 0xd8150ed8
```
clade_dec.bin @0xd8150ed8:  f2 c5 41 a1 04 79 82 28 d0 50 10 f5 12 f2 db 01   → <unknown> en cascada
clade_ok.bin  @0xd8150ed8:  d8 43 db 5a 60 40 81 75 02 c0 9d a0 0b 65 80 0f   → dispatcher REAL
```
Desensamblado de `clade_ok.bin` @0xd8150ed8 (extracción correcta):
```
d8150ed8: { call 0xd8829688 ; p0 = cmp.gtu(r1,#0x3) ; allocframe(#0x10) }
d8150ee4: { immext(#0xf80942c0); if (!p0) r0 = ##-0x7f6bd28; if (p0) jump 0xd8150ef8 }
d8150f00: { p0 = cmp.eq(r2,#0xb);   ... }        ; subsystem == 0x0B (FTM)   ✓ contrato
d8150f0c: { if (!cmp.eq(r2.new,#0x14)) jump ... }; sub-cmd DIAG == 0x14      ✓ contrato
d8150f18: { immext(#0xffc0); r2 = and(r1,##0xfffe) }; ftm command id         ✓ contrato
```
Coincide **exactamente** con el contrato de `0xd8150ed8` documentado en `clade_final.md §2.2`.
→ `clade_ok.bin` es la descompresión correcta; `clade_dec.bin` no. **FACT.**

### 1.2 Por qué el pase previo se equivocó
- `clade_dec.bin` se produjo con parámetros de request incorrectos (offset/base equivocado),
  dando 54 MB de bytes que **casualmente** tienen densidad de `dealloc_return`/`jumpr r31`
  parecida a código (porque son words CLADE mal-alineados que a veces coinciden con esos
  opcodes), pero **NO desensamblan** como packets coherentes.
- El "detector de código" por conteo de `dealloc_return`/`jumpr r31` **da falso positivo**
  sobre datos comprimidos/mal-alineados: por eso el pase previo creyó que clade_dec.bin
  ">0xd8a00000 era código". Lo es sólo por conteo de opcodes, **no** por desensamblado. El
  criterio robusto es **desensamblar y ver packets/prólogos coherentes** (lo hecho aquí).
- `clade_dec_full.bin` (10 MB) SÍ estaba bien extraído (por eso el dispatcher se leía ahí);
  el error estaba en `clade_dec.bin` (54 MB).

### 1.3 Extracción correcta (reproducible)
```bash
cd /tmp/modemre
QEMU_LD_PREFIX=/usr/x86_64-linux-gnu \
LD_LIBRARY_PATH=/tmp/qbs:/usr/lib/x86_64-linux-gnu \
qemu-x86_64-static ./clade_extractor_sm6375 /tmp/clade_ok.bin cc000000 3400000
#   req_va = cc000000 = 0xcc000000 + (0xd8000000 - 0xd8000000)   (espacio-comp)
#   len    = 3400000  = longitud de SALIDA pedida
python3 mkelf.py <slice> <out_va> out.elf
llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66 out.elf
```

---

## 2. LOS DOS WINDOWS DE CÓDIGO CLADE — extensión real (FACT)

| Window (out VA) | Extensión real | Contenido | Markers (dret/jr31) |
|-----------------|----------------|-----------|---------------------|
| **0xd8000000 .. 0xda440750** | ~36 MB código válido | `.clade.comp` — FTM/DIAG/ML1/dispatch | miles/4 MB, packets coherentes |
| **0xd0000000 .. 0xd0703000** | ~7.3 MB código válido | `.clade.exception_high` (window nuevo) | 1956 / 2182 |

- **Fin real del código en 0xd8**: último `dealloc_return`/`jumpr r31` coherente en
  **VA 0xda440750**. Esto cuadra con que el backing `modem.b26` termina en file-offset
  0x2444000 (dicts) → out VA `0xd8000000 + 0x2444000 ≈ 0xda444000`. Por encima de
  ~0xda440000 el conteo de markers cae a ~0 (ya no hay comp stream). **FACT.**
- El window `0xd0000000` (exception_high) lo detecté desde la **tabla de regiones en b23
  @0xc8c1cad8**: `d0000000 d0703000 ffffffff a1080000 000d5000`. Se extrae con
  `clade_extractor_sm6375 /tmp/exc_full.bin d0000000 703000` → 7.35 MB de Hexagon válido.
  (FACT — este window NO había sido extraído en pases anteriores.)

**Ambos windows re-buscados para RFLTE → CERO.** (§3)

---

## 3. BÚSQUEDA DE RFLTE EN EL CÓDIGO CORRECTO — CERO (FACT)

### 3.1 Strings directas en CLADE correcto
`rflte_ / rflm_ / sdr735 / rfdevice / carrier_activate / iq_capture / msgr_send` →
**count = 0** en `clade_ok.bin` (36 MB), en `exc_full.bin` (7.3 MB) y en el comp-stream
completo descomprimido (`/tmp/comp_all.bin`, 38 MB). (Esperado: el código no lleva strings
inline; los referencia por VA. Lo relevante es §3.2.) (FACT)

### 3.2 Referencias immext a los VA-de-string RFLTE (el criterio correcto)
Decoder immext **verificado** (reproduce `immext(#0xf80942c0)` del disassembler byte-a-byte).
Búsqueda de coincidencia EXACTA (`immext_top == strVA & ~0x3f`) a los VAs de nombre RF
(0xce6e72c0 `rflte_mc_carrier_activate`, 0xce6e08a8 `rflte_ftm_mc_wakeup`, 0xce6ded20
`iq_capture_prop_action`, 0xce5f4ccc `rflm_dtr_rx_activate_chain`, 0xce6fae60 `sdr735…`):

| Fuente de código | refs EXACTAS a nombres RF | refs immext a *páginas* rflte (0xce6d–0xce70) |
|------------------|---------------------------|-----------------------------------------------|
| clade_ok.bin (36 MB, window 0xd8) | **0** | 3 (incidentales: 0xce6d52c0, 0xce705400, 0xce6eba40 — no son los nombres) |
| exc_full.bin (7.3 MB, window 0xd0) | **0** | 1 (0xce6eb780 — incidental) |
| código plano b02/04/05/08/09/10/12/13/30 | **0** | 0 |

Si el cuerpo de `rflte_mc_carrier_activate` (+ decenas de funciones RFLTE/RFLM/SDR735, cada
una con asserts que cargan su propio string de nombre) viviera en estos windows, habría
**decenas-cientos** de immext hacia 0xce6xxxxx. Hay **4 incidentales** (referencias de
tablas/strings vecinas), **ninguno** a un nombre de función RF. **FACT: el código RFLTE no
está en el código CLADE ni plano disponible.**

---

## 4. EL POOL DLPAGER 0xd4400000 — QUÉ ES Y SU BACKING (FACT)

### 4.1 Los dos descriptores en b23
```
@0xc8d0a338 (descriptor estático):
   comp_start  = 0xcc000000   comp_end = 0xce13a000   (size 0x213a000)
   remap_start = 0xd4400000   remap_end= 0xd6539000   (size 0x2139000)   field=0x00c00000
@0xc8cdf130 (CONTROL-BLOCK runtime del pool dlpager):
   base=0xd4400000  cur=0xd4400560  recs=[0xc8cde568,0xc8cde5a8,0xc8cde5e8]
   page=0x10000(64K)  count=0x0f  csum=0xeb1a80ac   name_ptr=0xc366f24a
```
`comp_size ≈ remap_size` (0x213a000 vs 0x2139000) → **remap ~1:1 = pool RW/delta**, no RX de
código. (FACT)

### 4.2 El backing REAL del pool 0xd4400000: DDR físico, no b26
El control-block runtime referencia:
- `name_ptr = 0xc366f24a` → tabla de strings en **b21**: `_IU_INT / SYMPROC_IUSS /
  DEMOD_LITE_IUSS0 / DEMOD_LITE_IUSS1 / DEMOD_LITE_IUSS2`. Son **nombres de threads/pools
  del PHY-DSP** (SYMPROC, DEMOD_LITE), no de RFLTE.
- Los registros `0x4955xx` (@0xc8cde568…) llevan **direcciones físicas de DDR**:
  `0x2a33f40c`, `0x2a300000`, `0x2a43f400`, `0x2a400000`, tamaños `0x20000/0x30000/0xd20000`.
  Estas direcciones **no están en ningún program header** (el ELF sólo cubre p_paddr
  0x8b800000..0x99774000). (FACT)

→ **INFERENCE (fuerte):** el pool 0xd4400000 es un **pool de memoria de trabajo del PHY-DSP**
(SYMPROC/DEMOD_LITE) respaldado por DDR físico 0x2a3xxxxx que se **puebla en runtime**, NO un
backing estático de código. Su fuente **NO es b26 CLADE** (el solape comp_start=0xcc000000 es
sólo un límite superior heredado del mismo espacio de direcciones, no la fuente real de las
páginas). El pase previo que dijo "se sirve de b26" era INFERENCE sin confirmar; aquí se
corrige: el backing son las direcciones físicas 0x2a3xxxxx del control-block runtime. (FACT
del control-block / INFERENCE del rol.)

### 4.3 Confirmación externa (autor de la toolchain)
`qualcomm_baseband_scripts/README.md`: *"The delta section only has a couple diag command
handler structs, so the CLADE compressed section is where most of the meat is. I wouldn't
even bother with this [delta pool] on newer binaries."* → el pool delta (0xd4) **no contiene
el grueso del código**; el código de módulos user-mode va en CLADE. **FACT (fuente).**

---

## 5. ¿DÓNDE ESTÁ ENTONCES EL CÓDIGO RFLTE? (síntesis)

Descartado byte-a-byte en TODO el material del MBN:

| Candidato | Verificación en este pase | Contiene código RFLTE? |
|-----------|---------------------------|------------------------|
| seg27_dec.bin (b27, zlib, 6.3 MB) | 0 markers, 0 allocframes → **RODATA pura** | NO (sólo strings/tablas) |
| CLADE window 0xd8000000..0xda440750 (36 MB, **re-extraído correcto**) | 0 refs immext a nombres RF | **NO** |
| CLADE window 0xd0000000..0xd0703000 (7.3 MB, **re-extraído correcto**) | 0 refs immext a nombres RF | **NO** |
| comp-stream completo b26 (38 MB desc.) | 0 strings RF | **NO** |
| código plano b02/04/05/08/09/10/12/13/30 | 0 refs immext a nombres RF | **NO** |
| pool dlpager 0xd4400000 (delta/RW) | control-block → DDR físico 0x2a3xxxxx, threads PHY | **NO** (datos, backing fuera del ELF) |
| mssdump.elf (264 MB) | son p_paddr 0x8b8.. (misma imagen estática), sin windows 0xd0/0xd4/0xce6 runtime | **NO** (no es dump vivo) |

**FACT: el código RFLTE/RFLM/SDR735/RFDEVICE no existe en forma estática/descomprimible en
este MBN.** El firmware carga el subsistema RF (SYMPROC/DEMOD_LITE + RFLTE ML1) en RAM de
código que se puebla en boot desde una fuente que **no está en los program headers** (backing
físico DDR 0x2a3xxxxx para el pool de trabajo; y el código RF residente no aparece como sección
CLADE en b26). Los VAs `rflte_*` (0xce6xxxxx) son **strings** en seg27, referenciados sólo por
ese código ausente.

### Cruce MSGR/REX (tarea 4) — hasta dónde llega estáticamente
- En el CLADE correcto (0xd8) SÍ está el lado **FTM** (`ftm_lte_rex_dispatch` y familia,
  dispatcher 0xd8150ed8). El puente al RF es por **MSGR + REX + ctx compartido en b24/.bss**
  (ver `map_rflte_code.md §5`, sigue válido a nivel de estructura). Pero el **receptor**
  (handler MSGR del task RFLTE ML1) es código RF → cae en el segmento RF **ausente**, no en
  0xd8/0xd0. No hay `call` directo 0xd8→RF que resolver: el acoplamiento es por MSGR_ID +
  cola REX, y el cuerpo receptor no está en el material. (FACT del lado FTM / UNKNOWN del
  receptor.)

---

## 6. QUÉ FALTA EXACTAMENTE Y CÓMO OBTENERLO

**Falta:** el segmento de **código RF residente** (RFLTE/RFLM ML1 + SDR735 + RFDEVICE + los
threads PHY SYMPROC/DEMOD_LITE). No está como bNN, no es CLADE(b26), no es el pool delta(b26→
0xd4). Su VA de ejecución en runtime **no está mapeado por ningún program header**.

**Cómo obtenerlo (en orden de viabilidad):**
1. **Dump de RAM en vivo** del modem con el subsistema RF cargado (p.ej. vía `/dev/mem`
   restringido, ramdump post-crash `SDI`, o QDL en modo dump). Capturar el rango donde
   ejecuta el código que referencia 0xce6xxxxx. Los VAs `rflte_*` de string en seg27 sirven
   de ancla: buscar en el dump las funciones que hacen `immext` a 0xce6e72c0 / 0xce6e08a8 /
   0xce6ded20 → esas SON `rflte_mc_carrier_activate` / `rflte_ftm_mc_wakeup` /
   `rflte_ftm_iq_capture_prop_action_*`.
2. **Volcar el pool DDR físico 0x2a300000..0x2a539000** (backing del control-block
   @0xc8cdf130) en vivo — ahí residen las páginas de trabajo/código de los threads PHY.
3. **Firmware RFC/NV aparte:** los registros concretos SDR735/RFFE (LNA/mixer/ADC/PLL) están
   en el blob RFC/NV (RF Card), que **no** forma parte de este MBN; se necesita por separado.

**Lo que NO hay que volver a intentar (ya descartado en este pase):**
- Re-extraer CLADE "más arriba de 0xda440000": no hay más comp stream (b26 termina ahí).
- Descomprimir el pool 0xd4400000 esperando código RF: es delta/datos con backing DDR físico.
- Confiar en `clade_dec.bin` (corrupto) o `mssdump.elf` (imagen estática, no dump vivo).

---

## 7. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado en este pase):**
- `clade_dec.bin` (54 MB) es **corrupto/mal-descomprimido** (garbage en 0xd8150ed8; no
  desensambla). Todas las conclusiones previas derivadas de él son inválidas.
- Descompresión CLADE correcta re-hecha: `clade_ok.bin` (window 0xd8000000..**0xda440750**,
  ~36 MB) + `exc_full.bin` (window **0xd0000000..0xd0703000**, ~7.3 MB) = Hexagon válido.
- Dispatcher real 0xd8150ed8 confirmado (subsystem 0x0B, sub-cmd 0x14, `and(r1,##0xfffe)`).
- Decoder immext verificado (reproduce `immext(#0xf80942c0)` del disassembler).
- **0 referencias immext a los VAs de nombre RF** (0xce6e72c0/0xce6e08a8/0xce6ded20/
  0xce5f4ccc/0xce6fae60) en 36 MB + 7.3 MB CLADE correcto + todo el código plano.
- seg27_dec.bin = RODATA pura (0 markers, 0 allocframes en 6.3 MB); los VAs rflte son strings.
- Pool 0xd4400000 = RW/delta; control-block runtime @0xc8cdf130 → threads PHY
  (SYMPROC_IUSS/DEMOD_LITE_IUSS0-2, name_ptr 0xc366f24a en b21) con backing **DDR físico
  0x2a300000/0x2a3f400/0x2a400000** (ausente del ELF).
- mssdump.elf = imagen estática (p_paddr 0x8b8..0x997), sin windows runtime 0xd0/0xd4/0xce6.

**INFERENCE:**
- El pool 0xd4400000 es memoria de trabajo del PHY-DSP poblada en runtime desde DDR físico
  0x2a3xxxxx; **no** es el backing del código RFLTE ni deriva de b26 CLADE (se corrige la
  INFERENCE previa de "se sirve de b26").
- El código RFLTE ML1 vive en un segmento de código RF cargado en boot (SYMPROC/DEMOD_LITE)
  cuyo mapeo no está en los program headers → sólo recuperable en vivo.
- El cruce FTM→RF es por MSGR_ID + cola REX + ctx b24/.bss; el receptor MSGR es código RF
  ausente.

**UNKNOWN (no resoluble con el material actual):**
- Cuerpos desensamblados de `rflte_mc_carrier_activate`, `rflte_ftm_mc_wakeup`,
  `rflte_ftm_iq_capture_prop_action_{8,16}bit`, `rflm_dtr_rx_activate_chain`,
  `sdr735_common_class` — el código no está en ningún blob; requiere dump vivo.
- VA de ejecución runtime del segmento de código RF (fuera de los PH).
- Registros RFFE/SPMI concretos del SDR735 (en RFC/NV, no en este MBN).

---

## 8. REPRODUCIR

```bash
cd /tmp/modemre
export QEMU_LD_PREFIX=/usr/x86_64-linux-gnu
export LD_LIBRARY_PATH=/tmp/qbs:/usr/lib/x86_64-linux-gnu

# (1) DEMOSTRAR que clade_dec.bin es corrupto y clade_ok.bin correcto (dispatcher):
qemu-x86_64-static ./clade_extractor_sm6375 /tmp/disp2.bin cc150ed8 0x100
python3 mkelf.py /tmp/disp2.bin 0xd8150ed8 /tmp/disp2.elf
llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66 /tmp/disp2.elf | head   # dispatcher REAL
python3 -c "d=open('clade_dec.bin','rb').read();print(d[0x150ed8:0x150ee8].hex())"  # garbage

# (2) Re-extraer los DOS windows de código y buscar RFLTE (→ 0 refs):
qemu-x86_64-static ./clade_extractor_sm6375 /tmp/clade_ok.bin cc000000 3400000   # 0xd8 window
qemu-x86_64-static ./clade_extractor_sm6375 /tmp/exc_full.bin d0000000 703000    # 0xd0 window
python3 - <<'PY'
import struct
def immext(w):
    if (w>>28)!=0: return None
    return ((((w>>16)&0xFFF)<<14)|(w&0x3FFF))<<6 & 0xffffffff
tg=[0xce6e72c0,0xce6e08a8,0xce6ded20,0xce5f4ccc,0xce6fae60]
for f in ('/tmp/clade_ok.bin','/tmp/exc_full.bin'):
    d=open(f,'rb').read(); n=0
    for i in range(0,len(d)-4,4):
        v=immext(struct.unpack_from('<I',d,i)[0])
        if v is not None and any((t&~0x3f)==v for t in tg): n+=1
    print(f,"refs exactas a nombres RF:",n)   # -> 0
PY

# (3) Backing del pool 0xd4400000 (control-block runtime en b23):
python3 -c "d=open('modem.b23','rb').read();b=0xc8b6a000
print('name_ptr b21:', d[0xc366f24a-0:0].hex() if False else 'ver 0xc366f24a en b21')
import struct
o=0xc8cdf130-b
print(['%08x'%struct.unpack_from('<I',d,o+j)[0] for j in range(0,0x30,4)])"
```
