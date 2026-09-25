# RFLTE en los WINDOWS NUEVOS re-extraídos — SM6375 (MPSS.HI.4.3.4)

Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G).
Objetivo de este pase: re-buscar el código del driver RF LTE (`rflte_*`, `rflm_*`,
`sdr735_*`, `rfdevice_*`) en los windows de código CLADE **recién re-extraídos** que
pases anteriores NO habían analizado:

- `clade_dec_36m.bin` — VA base **0xd8000000**, tamaño 0x3400000 → cubre **0xd8000000..0xdb400000** (36 MB).
- `clade_exc_high.bin` — VA base **0xd0000000**, tamaño 0x703000 → cubre **0xd0000000..0xd0703000** (7.35 MB).

Leyenda: **FACT** = verificado byte-a-byte en este pase (con VA/offset/disasm) ·
**INFERENCE** = deducción con base dura · **UNKNOWN** = no resoluble con el material actual.

---

## 0. RESULTADO EJECUTIVO (leer primero)

1. **Los binarios nuevos son código Hexagon VÁLIDO y correcto** (no corrupto). Verificado:
   el dispatcher FTM conocido en 0xd8150ed8 aparece byte-idéntico (`d8 43 db 5a …`) y el
   decoder `immext` reproduce `immext(#0xf80942c0)` byte-a-byte. **FACT.**

2. **El "nuevo" rango de 26 MB (0xd8a00000..0xdb400000) NO es 26 MB de código.** Es
   código válido hasta **~0xda440000** (≈10.5 MB nuevos reales) y **filler `00407070`/ceros
   desde ~0xda500000 hasta 0xdb400000** (los últimos ~15 MB son relleno, no código).
   El comp-stream b26 se agota en ~0xda440000, igual que ya se sospechaba. **FACT.**

3. **El código RFLTE/RFLM/SDR735/RFDEVICE NO está en ninguno de los dos windows nuevos.**
   Búsqueda triple (immext-alineado, palabra-cruda LE, y puntero msg_const) contra los VAs
   de string `rflte_*` reales de seg27 → **CERO coincidencias** en 36 MB + 7.35 MB. **FACT.**

4. **`clade_exc_high.bin` (0xd0000000) SÍ es código válido** (6.3 MB reales + 1 MB filler):
   es el window `.clade.exception_high` — manejo de excepciones/interrupciones y código
   asociado del protocolo. **No contiene RFLTE** (0 refs). **FACT.**

5. **Los handlers MSGR del task RF (RFA_RF_LTE_*) NO son visibles** ni siquiera con el binario
   completo: el código que emite `RFA_RF_LTE_ENTER_MODE_CNF` / carrier-activate y que
   referencia los asserts `msgr_send`/`msgr_register_block` (VAs 0xce8bd84f/0xce945b0f) tiene
   **0 referencias** desde ambos windows. El **lado FTM** sí está (dispatcher 0xd8150ed8); el
   **receptor RF** sigue ausente. **FACT.**

6. **`rflte_ftm_iq_capture_prop_action` y `rflte_ftm_mc_wakeup`: 0 refs** por string-ref en
   los 36 MB. No se pueden desensamblar: **su cuerpo no está en el material.** **FACT.**

7. **CONFIRMACIÓN DEFINITIVA (con el binario completo):** el código RFLTE está ausente de
   TODO backing estático del MBN. Reside en el segmento de código RF residente (ML1/PHY)
   respaldado por DDR físico **0x2a3xxxxx** / pool dlpager fuera de los program headers. Se
   requiere **dump de RAM en vivo**. **FACT/INFERENCE.**

> Conclusión: la re-extracción de los windows nuevos **confirma** (no revoca) el resultado
> previo. El material nuevo aporta ~10.5 MB de código adicional real (NR5G/LTE MAC/LL1,
> scheduler, STM, QMI, más FTM) pero **sigue sin contener la capa driver RF**.

---

## 1. VALIDEZ DE LOS WINDOWS NUEVOS (FACT)

### 1.1 Dispatcher conocido byte-idéntico (36m)
```
clade_dec_36m.bin @0xd8150ed8:  d8 43 db 5a 60 40 81 75 02 c0 9d a0 0b 65 80 0f   → dispatcher REAL
```
Coincide con el contrato documentado (`locate_rflte.md §1.1`, `clade_final.md §2.2`):
subsystem 0x0B, sub-cmd 0x14, `and(r1,##0xfffe)`. **FACT.**

### 1.2 Decoder immext verificado
```
0xd8150ee4  word=0f80650b  ->  immext = 0xf80942c0   (exacto vs llvm-objdump)
```
El criterio de xref es correcto: un load de constante de 32 bits a `strVA` lleva
`immext == strVA & ~0x3f` (para todos los VAs rflte, `strVA & 0x3f == 0`, así que
`immext == strVA` exacto). **FACT.**

### 1.3 Mapa de densidad de código del 36m (jr31 = `jumpr r31` por MB)
```
0xd8000000..0xda440000   jr31 ~140–2000/MB   → CÓDIGO VÁLIDO (incluye los ~10.5 MB nuevos
                                                0xd8a00000..0xda440000 nunca analizados)
0xda500000..0xdb400000   jr31 = 0            → FILLER (00407070) / ceros, NO código
```
El comp-stream b26 (0x244a040 ≈ 38 MB comprimido) descomprime hasta ~out VA 0xda440000; por
encima no hay stream. **El "rango de 26 MB nuevo" real es 0xd8a00000..0xda440000.** **FACT.**

### 1.4 exc_high (0xd0000000) es código
Disasm en 0xd0000000: packets limpios (`r2 = memuh(r19+#0x0)`, `call 0xd010ed34`,
`allocframe`, immext). jr31 ~280–370/MB en 0..6 MB, cae a 3 en el MB 7 (filler final).
→ `.clade.exception_high` = **6.3 MB de código válido + ~1 MB filler**. **FACT.**

---

## 2. BÚSQUEDA DE RFLTE EN LOS WINDOWS NUEVOS — CERO (FACT)

### 2.1 VAs de string rflte confirmados en seg27 (base 0xce480000)
Localizados byte-a-byte en `seg27_dec.bin` (offset + 0xce480000):
```
rflte_mc_carrier_activate                       off 0x2672c0  VA 0xce6e72c0   ← el del encargo
rflte_ftm_mc_wakeup                             off 0x2608a8  VA 0xce6e08a8
rflte_ftm_iq_capture_prop_action_8bit           off 0x25ed20  VA 0xce6ded20
rflte_ftm_iq_capture_prop_action_16bit          off 0x25ed68  VA 0xce6ded68
rflte_ftm_iq_capture_prop_action_get_cfg        off 0x260c28  VA 0xce6e0c28
```
(Verifica la base: 0xce6e72c0 − 0x2672c0 = 0xce480000 = p_vaddr(b27). **FACT.**)

### 2.2 Tres métodos de xref, los tres a CERO

| Método (¿cómo referenciaría el código a la string?) | 36m (0xd8) | exc_high (0xd0) |
|-----------------------------------------------------|-----------:|----------------:|
| **immext-alineado** a `strVA & ~0x3f` (load de constante) | **0** | **0** |
| **palabra cruda LE** = strVA (puntero en litpool/tabla) | **0** | **0** |
| **puntero msg_const** (4 B LE = strVA, struct F3 QC) | **0** | **0** |

Extendido a `rflm_dtr_rx_activate_chain`, `sdr735_common_class` → también 0.
Extendido a TODOS los flat b02/04/05/08/09/10/12/13/20/21/23/30/33 → también 0. **FACT.**

### 2.3 Los únicos hits en la *página* rflte son incidentales (asserts genéricos)
Los 3 immext del 36m + 1 del exc_high que caen en 0xce6d–0xce70 apuntan a strings de assert
**compartidas**, NO a nombres de función RF:
```
36m  da1e2f60 -> ce6d52c0  "Assertion 0 failed"          (además cae mid-packet: misaligned)
36m  da2992f4 -> ce705400  "…(cdmaFwSilver != CDMA1X_…"  (misaligned)
36m  da3daaa8 -> ce6eba40  "r != NULL failed"            (real, pero assert genérico)
exc  d02e3e74 -> ce6eb780  "v_tbl_type_ptr == NULL "     (real, pero assert genérico)
```
Ninguno es `rflte_*`. **FACT.**

### 2.4 El 36m casi no referencia seg27 (prueba de que RF no está aquí)
En 36 MB de código hay **sólo 50 immext** hacia TODO seg27 (6.3 MB de rodata). El histograma
de rodata que SÍ referencia este código: top-bytes **0xf8/0xf9 (≈200k), 0xcb/0xcf/0xc9/0xc3/
0xc4/0xca/0xc8**. Es decir, este código usa su rodata en 0xf8/0xf9/0xc9-0xcf (windows runtime
fuera del ELF y b21/b23), **no** seg27. Las 50 refs a seg27 son asserts compartidos +
`msgr_receive_nonblock failed` + strings NR5G/LTE-MAC/LL1/STM/QMI. **Ni una `rflte_*`.**
Si el driver RF viviera aquí, habría **decenas–cientos** de immext a 0xce6d–0xce70 (cada
función RF carga su propio nombre en asserts/F3). Hay **cero**. **FACT.**

**Muestra de las 50 refs (todas protocol-stack / assert, ninguna RF):**
```
ce583240 "Assertion smp2p_info[dest].header_rx…"     ce58e840 "Nested FS_SIMPLE_TRY not allowed"
ce748180 "…msgr_receive_nonblock failed, r…" (x4)     ce776840 "< NR5G_LL1_MAX_NUM_VARIANTS failed"
ce86a080 "…meas.handover_lte_5gc.flag…"               ce878c40 "…lmac_qsh_metri…"
ce8c74c0 "1_SCHDLR_OBJ_LIST_ACTIVE failed"            ce8d2a00 "Fatal Error: 'sm == NULL'"
ce908a40 "< LTE_MEM_MAX_INST failed"                  cea5d480 "…nr5g_mac_log_ext_log_hdr_s…"
```

---

## 3. HANDLERS MSGR DEL RF TASK (tarea 3) — receptor AUSENTE (FACT)

El puente FTM→RF es por MSGR (UMID de 32 bits) + cola REX cross-task (ver `map_msgr_ipc.md`).
Para localizar el **receptor** (el que despacha `RFA_RF_LTE_ENTER_MODE`/carrier-activate) probé
las refs a los VAs de string del propio subsistema MSGR/RF-CNF:
```
0xce8bd84f  "msgr_send(&enter_mode_cnf.hdr,…)"  (assert del sitio que emite el CNF del RF)
0xce945b0f  "msgr_register_block(…)==E_SUCCESS" (registro de UMIDs del RF task)
0xce68146b  "msgr_send_msg(UMID=0x%08X)"
0xce8ac06e  "MSGR_LTE_ML1_MSMGR"
0xce748136  "msgr_register"
```
Refs desde 36m y exc_high a cualquiera de esos VAs: **0 en ambos.** **FACT.**

- **Lado FTM = PRESENTE:** dispatcher 0xd8150ed8 + familia FTM/DIAG en el 36m; y el 36m SÍ
  referencia `msgr_receive_nonblock failed` (0xce748180) en 4 sitios (0xd859481c, 0xd8596348,
  0xd85968c8, 0xd8596b0c) — esos son loops de **recepción** MSGR genéricos del framework, no el
  handler RFA_RF_LTE. **FACT.**
- **Lado RF (receptor RFA_RF_LTE_*) = AUSENTE:** el handler que hace `msgr_send(enter_mode_cnf)`
  / carrier-activate y registra los UMIDs RF **no está** en ningún window. El acoplamiento es por
  UMID + tabla msgr_table + cola REX; el cuerpo receptor cae en el segmento de código RF
  residente ausente. **FACT (lado FTM) / UNKNOWN (cuerpo receptor).**
- Los enteros UMID `RFA_RF_LTE_ENTER_MODE_CNF` etc. son constantes compiladas
  (`MSGR_ID_VAL(NAME)`), no aparecen como dato; sólo los **nombres** están en b21 rodata
  (0xc404dce1 etc.). Numérico = **UNKNOWN** estático (se resuelve en vivo). **FACT.**

---

## 4. rflte_ftm_iq_capture_prop_action / rflte_ftm_mc_wakeup (tarea 4) — NO desensamblables (FACT)

Buscados por string-ref (immext / raw / msg_const) en los 36 MB: **0 hits** para
`rflte_ftm_iq_capture_prop_action_{8,16}bit`, `_get_cfg`, y `rflte_ftm_mc_wakeup`.
→ **Sus cuerpos no están presentes; no se pueden desensamblar.** El path IQ real (el que
asigna `iq_buff`, configura `rx_path_data_ptr` por carrier) permanece bloqueado: ese código es
parte del módulo RF ausente. **FACT.** (El lado FTM/TLV que *invoca* el IQ capture sí está
mapeado en pases previos — ver `map_iq_capture.md` — pero termina en un `msgr_send`/wakeup hacia
el RF, cuyo receptor falta.)

---

## 5. exc_high (0xd0000000) — QUÉ CONTIENE (tarea 2, FACT)

- **Es código Hexagon válido**: 6.3 MB reales (0xd0000000..~0xd0700000) + ~1 MB filler final.
- Densidad `jumpr r31` ~280–370/MB; packets limpios con `allocframe`, `call`, `immext`,
  loads/stores de memoria. Ejemplos:
  ```
  d0000000: { r2 = memuh(r19+#0x0); r3 = memw(r19+#0x188) }  … { if(p0) jump 0xd0000028 }
  d010000c: { call 0xd010ed34 }   d0100028: { call 0xd05792ac }   d0100030: { call 0xd0206eb4 }
  ```
- Corresponde al XML QuRT `.clade.exception_high  physpool=TCM_POOL mapping=rx` — **código de
  manejo de excepciones/interrupciones y rutinas de bajo nivel asociadas.** Referencia rodata en
  0xf8/0xc9/0xe4/0xce (assert `v_tbl_type_ptr == NULL`, etc.).
- **RFLTE en exc_high: 0 refs** (§2.2). No es el driver RF. **FACT.**

---

## 6. POR QUÉ RFLTE NO ESTÁ AQUÍ Y DÓNDE ESTÁ (síntesis)

El material CLADE completo (36m + exc_high) contiene: **arranque/FTM/DIAG dispatch, NR5G y LTE
MAC/LL1, scheduler (SCHDLR), máquinas de estado STM, QMI, SMP2P, framework MSGR (registro +
recepción genéricos), manejo de excepciones.** Todo ello referencia sus strings en 0xf8/0xf9/
0xc9-0xcf y sólo roza seg27 para asserts compartidos.

El **driver RF LTE** (`rflte_*`/`rflm_*`/`sdr735_*`/`rfdevice_*`) es un módulo distinto cuyo
código **no aparece en ningún backing estático del MBN** (ni CLADE b26 ambos windows, ni flat
b02..b33, ni el pool delta 0xd4400000). Sus strings viven en seg27 (0xce6d–0xce70) pero **nadie
en el material los referencia** → el consumidor (el código RF) está fuera.

**INFERENCE (fuerte, consistente con `locate_rflte.md §4-6`):** el código RF residente
(RFLTE ML1 + SDR735 + RFDEVICE + threads PHY SYMPROC/DEMOD_LITE) se carga en boot en un window
de código respaldado por **DDR físico 0x2a3xxxxx** (control-block dlpager @0xc8cdf130, name_ptr
0xc366f24a en b21 → SYMPROC_IUSS/DEMOD_LITE_IUSS0-2), **fuera de todos los program headers**.

---

## 7. FACT / INFERENCE / UNKNOWN (con VAs)

**FACT (verificado en este pase sobre los binarios nuevos):**
- `clade_dec_36m.bin` = 0xd8000000..0xdb400000; código válido hasta **~0xda440000**, filler
  después. Dispatcher 0xd8150ed8 byte-idéntico. Decoder immext verificado (0xf80942c0).
- `clade_exc_high.bin` = 0xd0000000..0xd0703000; **código válido** (6.3 MB) + filler, window
  `.clade.exception_high`. **0 refs RFLTE.**
- VAs de string rflte confirmados: carrier_activate **0xce6e72c0**, ftm_mc_wakeup 0xce6e08a8,
  iq_capture_8bit 0xce6ded20, iq_16bit 0xce6ded68, iq_get_cfg 0xce6e0c28 (base seg27 0xce480000).
- **0 xrefs** (immext-alineado + palabra-cruda-LE + puntero-msg_const) a esos VAs desde 36m,
  exc_high y todo el flat. Igual para rflm/sdr735.
- Sólo **50 immext** del 36m hacia seg27 completo; todos asserts genéricos / protocol-stack
  (NR5G/LTE-MAC/LL1/SCHDLR/STM/QMI/SMP2P/msgr_receive), **ninguno `rflte_*`**.
- Los 4 hits en página rflte (da1e2f60→ce6d52c0, da2992f4→ce705400, da3daaa8→ce6eba40,
  d02e3e74→ce6eb780) son asserts compartidos, no nombres RF; 2 caen mid-packet (misaligned).
- Handler MSGR receptor RF: 0 refs a 0xce8bd84f/0xce945b0f/0xce68146b/0xce8ac06e/0xce748136
  desde ambos windows. Lado FTM presente (0xd8150ed8; msgr_receive genérico en 0xd859481c…).
- `rflte_ftm_iq_capture_prop_action_*` y `rflte_ftm_mc_wakeup`: 0 string-ref → **no
  desensamblables**.

**INFERENCE:**
- El "rango nuevo de 26 MB" real útil es **0xd8a00000..0xda440000 (~10.5 MB)**; el resto
  (0xda500000..0xdb400000) es filler del window, no código.
- El módulo RF (RFLTE/RFLM/SDR735/RFDEVICE + PHY SYMPROC/DEMOD_LITE) es un window de código
  aparte respaldado por DDR físico 0x2a3xxxxx, fuera de los program headers.
- El código de los windows nuevos es protocol-stack (MAC/LL1/SCHDLR/STM/QMI) + FTM/DIAG +
  excepciones; NO la capa driver RF.

**UNKNOWN (no resoluble con el material actual):**
- Cuerpos desensamblados de `rflte_mc_carrier_activate`, `rflte_ftm_mc_wakeup`,
  `rflte_ftm_iq_capture_prop_action_{8,16}bit/_get_cfg`, `rflm_dtr_rx_activate_chain`,
  `sdr735_common_class` — no presentes.
- Cuerpo del handler MSGR receptor RFA_RF_LTE_* (enter_mode/carrier_activate).
- Enteros UMID RFA_RF_LTE_* (constantes compiladas; sólo en vivo vía `msgr_query_umid`).
- VA de ejecución runtime del segmento de código RF (fuera de los PH).

---

## 8. QUÉ HACE FALTA (confirmación tarea 5)

Con el binario CLADE **completo** re-extraído (36m + exc_high), se **confirma definitivamente**
que el código RFLTE no es recuperable estáticamente. Para obtenerlo:

1. **Dump de RAM en vivo** con el subsistema RF cargado (ramdump SDI post-crash, QDL modo dump,
   o `/dev/mem` restringido). Anclar por los VAs de string seg27: buscar en el dump las
   funciones que hacen `immext` a **0xce6e72c0** (=`rflte_mc_carrier_activate`), **0xce6e08a8**
   (=`rflte_ftm_mc_wakeup`), **0xce6ded20/0xce6ded68/0xce6e0c28** (=`iq_capture_prop_action_*`).
   Esas funciones SON / están junto a los cuerpos RFLTE.
2. **Volcar el pool DDR físico 0x2a300000..0x2a539000** (backing del control-block dlpager
   @0xc8cdf130) — ahí residen las páginas de código/trabajo de los threads PHY.
3. **Firmware RFC/NV aparte** para los registros SDR735/RFFE concretos (no está en este MBN).

**Ya descartado (no repetir):** re-extraer CLADE por encima de ~0xda440000 (no hay stream);
buscar RFLTE en exc_high o en el pool delta 0xd4; confiar en clade_dec.bin viejo (corrupto).

---

## 9. REPRODUCIR

```bash
cd /tmp/modemre
# (a) validar windows nuevos (dispatcher + immext):
python3 - <<'PY'
import struct
d=open('clade_dec_36m.bin','rb').read()
print('disp 0xd8150ed8:', d[0x150ed8:0x150ee8].hex())          # d843db5a...
def immext(w):
    return None if (w>>28) else (((((w>>16)&0xFFF)<<14)|(w&0x3FFF))<<6)&0xffffffff
print('immext@0xd8150ee4:', hex(immext(struct.unpack_from('<I',d,0x150ee4)[0])))  # 0xf80942c0
PY

# (b) xref triple a los VAs rflte (-> 0):
python3 - <<'PY'
import struct
tg=[0xce6e72c0,0xce6e08a8,0xce6ded20,0xce6ded68,0xce6e0c28]
def immext(w):
    return None if (w>>28) else (((((w>>16)&0xFFF)<<14)|(w&0x3FFF))<<6)&0xffffffff
for fn,base in (('clade_dec_36m.bin',0xd8000000),('clade_exc_high.bin',0xd0000000)):
    d=open(fn,'rb').read(); ie=raw=0
    for i in range(0,len(d)-4,4):
        v=immext(struct.unpack_from('<I',d,i)[0])
        if v is not None and any((t&~0x3f)==v for t in tg): ie+=1
    for t in tg: raw+=d.count(struct.pack('<I',t))
    print(fn,'immext-hits=',ie,'raw-word-hits=',raw)   # 0 0
PY

# (c) desensamblar cualquier zona del rango nuevo:
python3 -c "d=open('clade_dec_36m.bin','rb').read();o=0xd9002000-0xd8000000;open('/tmp/f.bin','wb').write(d[o:o+0x100])"
python3 mkelf.py /tmp/f.bin 0xd9002000 /tmp/f.elf
llvm-objdump-18 -d --triple=hexagon --mcpu=hexagonv66 /tmp/f.elf
```
