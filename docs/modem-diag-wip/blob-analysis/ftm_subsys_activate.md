# FTM SUBSYS (0x0B) — POR QUÉ NO RESPONDE Y CÓMO HACER QUE RESPONDA
Build: **MPSS.HI.4.3.4-00494-MANNAR_GEN_PACK-1.24452.133** (SM6375 / Moto G82 5G)
Imágenes: `_dis_b13.txt` (DIAG core, VA 0xc0d36000+, NO comprimido) · `modem.b21` (rodata, VA 0xc3553000) · `modem.b23` (data, VA 0xc8b6a000) · `clade_dec_full.bin` (código paginado 0xd8xxxxxx).
Leyenda: **FACT** = leído del disasm/bytes (VA citada) · **INFERENCE** = deducción sobre el modelo Qualcomm consistente con la evidencia · **UNKNOWN** = sólo en vivo.

---

## 0. TL;DR — LA CAUSA RAÍZ (cambia el diagnóstico de todos los passes previos)

**Tu `subsys_cmd_code = 0x0014` (el u16 @0x02 de tu paquete) NO es un "campo constante de FTM": ES el índice que selecciona el handler en la tabla de dispatch del subsys 0x0B. Y el valor 0x14 cae, adentro del dispatcher común 0xd8150ed8, en la ÚNICA rama que hace `return 0` (respuesta nula) → el DIAG core lo descarta EN SILENCIO (sin 0x13, sin 0x14, sin nada).**

Tu paquete actual:
```
4b 0b | 14 00 | 5a 03 | 00 00 00 00 | 27 00 | <sub> <ntlv> ...
 ^cmd  ^u16@2  ^u16@4   ^@6..@9       ^@0a..
 0x4b   0x0014  0x035a                 0x0027
```
- @0x00 `0x4b` = DIAG_SUBSYS_CMD_F. **FACT** (0xc0d55e30).
- @0x01 `0x0b` = subsys_id FTM. **FACT** (0xc0d55f30).
- **@0x02 `0x0014` = subsys_cmd_code = EL ftm_cmd_id QUE INDEXA LA TABLA 0xc37bd1e8.** **FACT** (0xc0d55f48: `r22 = memuh(r17+#0x2)`).
- @0x04 `0x035a` = el "id" secundario que el dispatcher valida (`& 0xfffe == 0x35a`). **FACT** (0xd8150f18).
- @0x0a `0x0027` = lo que vos creías "ftm_cmd" NO se lee en este path. **FACT (no hay lectura de @0x0a en el gate de 0xd8150ed8; sólo se lee @0x08/@0x09 si cmd==0x24).**

**La corrección estructural clave:** el campo que elige la tecnología/comando FTM es **@0x02**, NO @0x0a. Los passes previos que mandaban el header CORTO (`4b 0b <ftm_cmd:u16 @0x02> <sub:u16> <ntlv:u16>`) ponían 0x27 en @0x02 y por eso **respondían**. Tu header LARGO nuevo puso **0x14 en @0x02** (y 0x27 quedó en @0x0a, donde nadie lo lee) → matcheás la entrada 0x14 de la tabla → rama silenciosa.

**Fix inmediato:** poné en **@0x02** el ftm_cmd_id de la tecnología (p.ej. `27 00` para LTE), **NO** `14 00`. El 0x14 NO es un subsys_cmd_code válido para comandar: es una entrada reservada cuyo handler devuelve 0.

---

## 1. CÓMO SE REGISTRA EL SUBSYS FTM (0x0B) EN EL DIAG DISPATCH — FACT

### 1.1 El record de registro en seg23 @ 0xc8dc3b50 (bytes reales)
`modem.b23` (VA base 0xc8b6a000), offset 0x259b50. **20 bytes/record**:
```
0000ff00 0b004b00 ff000000 ffffffff e8d17bc3
```
Decodificado (LE):
| off | valor | significado |
|-----|-------|-------------|
| +0x00 | u16 `0x0000` | (range lo del cmd_code global — no usado en el match) |
| +0x02 | u16 `0x00ff` | **cmd_code marker = 0xff (marca "esto es un subsys")** |
| +0x04 | u16 `0x000b` | **subsys_id = 0x0B (FTM)** |
| +0x06 | u16 `0x004b` | **count = 75 entradas** |
| +0x08 | `0x000000ff` | flags |
| +0x0c | `0xffffffff` | flags |
| +0x10 | `0xc37bd1e8` | **puntero a la tabla de rangos (75 entradas de 8 bytes)** |

El record siguiente (@0xc8dc3b64) es subsys 0x0A: `...0a000100...40d47bc3` (count=1, tbl=0xc37bd440). **FACT.**

### 1.2 Construcción de la master table — `diagpkt_tbl_reg` @ 0xc0d6498c (b13)
En boot, esta función **copia el PUNTERO al record** (r17) dentro del array de la master table `0xc92e4400`:
```
c0d649ac: memw(r2+#0x0) = r17     ; master_table[i] = &record  (record de §1.1)
```
→ La master table `0xc92e4400[]` es un array de punteros a los records de 20 bytes de seg23. Hay un array paralelo de flags `0xc92e4600[]` (1 byte, valor 3 = "fin de tabla"). **FACT** (0xc0d64af0 loop, 0xc0d64b00 `memb(r2++)=r4` con r4=3).

**Respuesta a tu pregunta 1:** SÍ, el subsys 0x0B está registrado SIEMPRE (record estático en seg23, copiado a la master table en boot). count=75, tbl=0xc37bd1e8. **No falta registrar nada.** No responde por lo del §3, no por falta de registro.

---

## 2. LA CONDICIÓN DE DISPATCH DEL SUBSYS FTM — EL PATH EXACTO (FACT)

### 2.1 Entrada: DIAG core matchea 0x4b y extrae subsys+cmd — `diagpkt_process` @ 0xc0d55e1c
```
c0d55e1c: r22 = memub(r17+#0x0)          ; cmd_code = 0x4b
c0d55e30: p1 = cmpb.eq(r22,#0x4b); if(p1) r20=#0xff ; marca subsys
c0d55e40: jump 0xc0d36424
c0d3642c: p1 = cmp.gtu(r16,#0x3); if(p1) jump 0xc0d55f20   ; len>3 -> ok
c0d55f30: if (p1) r21 = memub(r17+#0x1)  ; r21 = subsys_id = 0x0b   <<<
c0d55f48:         r22 = memuh(r17+#0x2)  ; r22 = subsys_cmd_code = u16@0x02   <<<<<<<
          jump 0xc0d55eb8                ; -> master table walk
```
**FACT.** r21 = subsys_id (@0x01). **r22 = subsys_cmd_code = u16 @0x02.**

### 2.2 Master table walk — @ 0xc0d55f58 (loop1, 128 iter)
```
r2 = 0xc92e4400 (array ptrs records)   r3 = 0xc92e4600 (flags)
por cada entry (record*):
  c0d55f58: r4 = memb(r3)      ; flag==3 -> FIN de tabla -> jump 0xc0d364f8 (BAD_CMD 0x13)
  c0d55f64: r4 = memw(r2)      ; record ptr; ==0 -> skip
  c0d55f6c: r5 = memh(r4+#0x4) ; record+0x4 = subsys_id;  if(r5==r21) match subsys -> 0xc0d55f88
  c0d55f88: r5 = memh(r4+#0x2) ; record+0x2 = 0xff marker; if(r5!=r20) skip  (r20=0xff)
  c0d55f90: r5 = memw(r4+#0x10); record+0x10 = tabla de rangos; ==0 -> skip
  c0d55f98: r6 = memuh(r4+#0x6); record+0x6 = COUNT (75); ==0 -> skip. r4 = tbl+2
  loop0 (COUNT rangos, stride 8):
    c0d55fa8: r5 = memh(r4-2)  = range.lo ; if !(lo>r22) -> chequea hi (0xc0d55fbc)
    c0d55fbc: r5 = memh(r4+0)  = range.hi ; if (r22>hi) -> siguiente rango (r4+=8)
    -> rango donde lo<=r22<=hi
  c0d55fc8: r2 = memw(r4+2) = range.handler   ; ==0 -> jump 0xc0d364d8 (BAD_CMD 0x13)
  c0d55ffc: callr r2            ; <<< LLAMA AL HANDLER (r0=pkt, r1=len)
  c0d56004: r16 = r0            ; r16 = valor de retorno = puntero de respuesta
  c0d56014: if (r16==0) jump 0xc0d560bc   ; <<<<< HANDLER DEVOLVIÓ 0 -> SILENCIO
            else call 0xc0d560c8          ; commitea la respuesta
```
**FACT (todo).** 0xc0d560bc = `dealloc_return` PELADO, **sin construir ninguna respuesta**.

### 2.3 La tabla de rangos 0xc37bd1e8 (bytes reales, `modem.b21`)
Entry = `{u16 lo; u16 hi; u32 handler}` (8 bytes). Las 75 entradas tienen `lo==hi` (valor único) y **TODAS** `handler=0xd8150ed8`. La entrada que te importa:
```
[42] lo=0x0014 hi=0x0014 handler=0xd8150ed8      <-- tu 0x14 matchea AQUÍ
[ 4] lo=0x0027 hi=0x0027 handler=0xd8150ed8      <-- 0x27 (LTE) matchea AQUÍ
```
(Hay una entrada 76 `[75] lo=0x0000 hi=0xffff` catch-all, pero **count=75 → sólo se recorren [0..74]**, la catch-all NO se alcanza. Un cmd_code fuera de la lista → 0x13 BAD_CMD.) **FACT.**

### 2.4 QUÉ CAMPO INDEXA QUÉ TABLA — la aclaración que pediste
| campo del paquete | offset | lo lee | indexa |
|---|---|---|---|
| cmd_code `0x4b` | @0x00 | 0xc0d55e1c | selecciona el path "subsys" (r20=0xff) |
| subsys_id `0x0b` | @0x01 | 0xc0d55f30 | matchea record `+0x04` en master table |
| **subsys_cmd_code `0x0014`** | **@0x02** | **0xc0d55f48** | **indexa la tabla de rangos 0xc37bd1e8 (por lo/hi)** |
| id `0x035a` | @0x04 | 0xd8150f18 (dentro del handler) | validado `&0xfffe==0x35a` |
| (tu "ftm_cmd" `0x0027`) | @0x0a | **NADIE en el gate** | — (no participa del dispatch de nivel 1/2) |

→ **La tabla 0xc37bd1e8 se indexa por el u16 @0x02 (subsys_cmd_code), NO por @0x0a.** El agente previo que dijo "@0x02 es constante 0x14 y @0x0a=ftm_cmd" se equivocó: es al revés. El header CORTO que funcionaba ponía el ftm_cmd (0x27) en @0x02, que es lo correcto. **FACT.**

---

## 3. POR QUÉ SILENCIO TOTAL (ni 0x13 ni 0x14) — EL HANDLER 0xd8150ed8 (FACT)

`0xd8150ed8` es el dispatcher común FTM (código descomprimido en `clade_dec_full.bin`). Su prólogo:
```
d8150edc: p0 = cmp.gtu(r1,#0x3); if(!p0) error            ; len<=3 -> error
d8150efc: r2 = memb(r0+#0x1)
d8150f00: if (r2 != 0x0b) jump 0xd8150f3c                  ; GATE subsys@0x01==0x0b
d8150f04: r2 = memub(r17+#0x2)|(memb(r17+#0x3)<<8)         ; = u16@0x02
d8150f0c: if (r2 != 0x14) jump 0xd8150f3c                  ; GATE cmd@0x02==0x14  <<<<<
d8150f10: r1 = u16@0x04
d8150f1c: r2 = and(r1,#0xfffe)
d8150f24: if (r2 != 0x35a) jump 0xd8150f3c                 ; GATE (id@0x04&0xfffe)==0x35a
--- si LOS TRES GATES PASAN (subsys=0x0b, cmd=0x14, id=0x35a): ---
d8150f2c: call 0xd8150e14        ; emite un F3 (SSID 0x17) — sólo log
d8150f34: r0 = 0xf8088108        ; (puntero a struct de msg)
d8150f38: jump 0xd8150fe4
d8150fe8: r0 = #0x0 ; jump <epílogo>   ; <<<<< RETORNA 0
```
**FACT.** Con tu paquete `4b 0b 14 00 5a 03 ...`:
- subsys@0x01 = 0x0b ✓
- cmd@0x02 = 0x14 ✓
- id@0x04 = 0x035a; `0x035a & 0xfffe = 0x035a` ✓

→ **Los tres gates PASAN → rama 0xd8150f2c → emite un F3 y `return 0`.** El master walk (§2.2, 0xc0d56014) ve `r16==0` → `jump 0xc0d560bc` = `dealloc_return` **sin respuesta**. **Ése es tu silencio EXACTO.** No es 0x13 (comando reconocido), no es 0x14 (no llega al packer), no es nada: el handler devuelve NULL adrede para el cmd_code 0x14.

### 3.1 Qué es 0x14 entonces
0x14 es una **entrada reservada / no-op** del dispatcher (posiblemente un "registration self-descriptor" o un ping interno): es el ÚNICO valor de @0x02 para el que 0xd8150ed8 tiene un match explícito de gate que termina en `return 0`. Para **cualquier otro** cmd_code válido de la tabla (0x27, 0x08, 0x07...), el gate `cmd!=0x14` es verdadero → salta a **0xd8150f3c = el path REAL de dispatch** (aloca respuesta y ejecuta). **FACT + INFERENCE (semántica de "reservado").**

### 3.2 El path real (0xd8150f3c → f58 → f84) — lo que SÍ produce respuesta
```
d8150f3c: (aloca descriptor via 0xd814d760, r1=0x17)
d8150f58: r16 = descriptor ; memh(r16+0xc)=len ; memw(r16+0x10)=...
d8150f84: r1 = memuh(r16+0xc) ; call 0xd8051b58 (alloc buffer)
d8150fc4: call 0xd80f40fc     ; -> 0xc0d64328 = diagpkt_subsys_alloc (arma header 4b 0b ..)
d8150fdc: call 0xd814ff38     ; arma respuesta 4b 0b 14 00 <id 35a/35b> 03 ...
          r0 = <ptr respuesta>  ; <<< RETORNA != 0 -> el master walk la COMMITEA
```
0xd814ff38 escribe literalmente `memb(r0+0)=0x4b; memb(r0+1)=0x0b; memb(r0+2)=0x14; memb(r0+5)=0x03; r2=0x35a/0x35b @+0x04`. **FACT.** → El path f3c retorna un puntero de respuesta ≠ 0 → drena.

**Conclusión §3:** el silencio se produce **sólo** con cmd@0x02 = 0x14. Cambiá @0x02 a un ftm_cmd real y caés en el path f3c que sí responde.

---

## 4. ¿EL SUBSYS FTM CORRE EN EL MPSS O SE RUTEA A OTRO PD? — FACT

**Corre LOCAL en el MPSS. NO se forwardea a otro peripheral/PD.**
- El handler 0xd8150ed8 se ejecuta en el MISMO contexto del `callr r2` del master walk del DIAG core del MPSS (0xc0d55ffc). No hay indirección a `diagfwd_peripheral`/DCI en este path. **FACT.**
- La respuesta se aloca con `diagpkt_subsys_alloc` (0xc0d64328) del pool local y se commitea por el mismo `diagbuf_send_pkt` que ya tenés resuelto (ver `diag_transport_full.md`). **FACT.**
- No hay tabla "subsys→peripheral" que desvíe el 0x0B: el record de seg23 apunta directo a código del MPSS (0xd8xxxxxx paginado, pero MPSS). **FACT.**

→ Tu comando entra por CMD del MPSS, se procesa en el MPSS, y la respuesta vuelve por DATA del MPSS (que ya servís). **No hay que alcanzar otro PD.** El problema era 100% el cmd_code 0x14.

---

## 5. APPS 0x00 (responde) vs SUBSYS 0x4b/0x0b (no) — DÓNDE DIVERGE (FACT)

| paso | apps 0x00 | subsys 4b/0b/0x14 (tu caso) |
|---|---|---|
| match cmd_code @0x00 | 0x00: path apps (tabla por cmd 0x00) | 0x4b: path subsys (r20=0xff) |
| lookup | master table por cmd 0x00 → handler version | master table subsys 0x0b → tabla rangos 0xc37bd1e8 |
| índice | — | **u16@0x02 = 0x14 → entry[42] → 0xd8150ed8** |
| handler | arma respuesta (58 bytes build-date) → `return ptr!=0` | 0xd8150ed8 gates pasan → `return 0` |
| resultado | commit → drena → LO VES | `r16==0` → `dealloc_return` → **SILENCIO** |

La divergencia es en el **valor de retorno del handler**: apps 0x00 retorna un buffer; el FTM con cmd=0x14 retorna 0. Ambos cruzan el MISMO gate de `diagpkt_rsp_send` (feature+diagID) y el MISMO drain — por eso apps 0x00 te llega: tu transporte está bien. El FTM 0x14 muere ANTES del commit, en el propio handler. **FACT.**

---

## 6. CÓMO HACER QUE RESPONDA — ACCIONABLE

### 6.1 El fix (una línea)
**Poné el ftm_cmd_id de la tecnología en @0x02, no `14 00`.** El header correcto para este build es el CORTO que ya te funcionaba:
```
4b 0b | <ftm_cmd:u16 @0x02> | <sub_command…/payload>
```
Para LTE: `4b 0b 27 00 ...`  (0x27 = FTM_LTE, entry[4]). **FACT: 0x27 cae en f3c = path real.**

Si querés mantener el `id 0x35a` en el paquete (algunos dispatchers lo esperan), el layout que el handler 0xd8150ed8 valida en su path f3c es:
```
@0x00 4b | @0x01 0b | @0x02..03 <ftm_cmd u16> | @0x04..05 <id: (x&0xfffe)==0x35a, usá 5a 03> | @0x06.. payload
```
pero OJO: el gate `id==0x35a` SÓLO se evalúa en la rama cmd==0x14. En el path f3c (cmd!=0x14) el id 0x35a NO es obligatorio para entrar; lo consume internamente el packer de la tecnología. **INFERENCE (el gate id sólo está en la rama 0x14) — validar en vivo.**

### 6.2 subsys_cmd_code correcto
- **Tu 0x14 está MAL** para comandar: es la entrada reservada silenciosa.
- Valores válidos que caen en el path f3c (responden): **cualquiera de la tabla EXCEPTO 0x14**. En particular los que ya respondían en vivo: **0x07, 0x0d, 0x10, 0x1b, 0x27**. Para RF-test LTE usá **0x27**. **FACT (lista §2.3) + FACT (vivos previos).**
- Un cmd_code fuera de la lista de 75 → **0x13 BAD_CMD** (0xc0d364f8/0xc0d55f80), NO silencio. Eso te sirve para distinguir en vivo.

### 6.3 Nada que activar/registrar
El subsys 0x0B está registrado siempre (§1). No hay feature-flag ni PD extra. El único requisito extra es el que ya resolviste (gate feature+diagID+real-time para que la respuesta DRENE, ver `diag_transport_full.md`). Con cmd@0x02≠0x14, el handler retorna buffer y ese buffer cruza tu transporte OK. **FACT.**

### 6.4 Plan en vivo inmediato (para cerrar)
1. Mandá `4b 0b 27 00` + payload mínimo (num_tlv=0). Esperado: respuesta (eco/REPACK) o 0x14 BAD_PARM — **NO silencio**. Si ves respuesta/0x14 → confirmado que @0x02 es el campo correcto.
2. Mandá `4b 0b 14 00 5a 03 ...` (tu paquete actual) y confirmá que sigue en silencio → prueba directa de que 0x14 es la rama `return 0`.
3. Mandá `4b 0b ff 00` (cmd 0xff, fuera de tabla) → esperado **0x13 BAD_CMD** → confirma que el silencio del 0x14 NO es "no registrado" sino "handler retorna 0".
4. Con @0x02=0x27 andando, retomá la secuencia RF-test/IQ (COMMAND_CAPABILITY, RADIO_CONFIG, RX_MEASURE/IQ_CAPTURE) de `iq_sequence.md` / `rftest_command_format.md`, pero con el **sub_command y TLVs DESPUÉS de @0x04**, no metiendo el 0x14 en @0x02.

---

## 7. FACT / INFERENCE / UNKNOWN — cierre con VAs

**FACT (leído del disasm/bytes):**
- DIAG core matchea 0x4b @0xc0d55e30 → path subsys (r20=0xff); extrae `subsys_id=memub(pkt+1)` @0xc0d55f30 y `subsys_cmd_code=memuh(pkt+2)` @0xc0d55f48.
- Master table `0xc92e4400[]` = punteros a records de seg23; construida por `diagpkt_tbl_reg` @0xc0d6498c (`memw(r2)=r17` @0xc0d649ac). Flags `0xc92e4600[]`, 3=fin.
- Record FTM @0xc8dc3b50 (seg23): `+0x02=0xff, +0x04=subsys 0x0b, +0x06=count 75, +0x10=tbl 0xc37bd1e8`. Bytes: `0000ff00 0b004b00 ...`.
- Tabla de rangos 0xc37bd1e8 (b21): 75×`{u16 lo,u16 hi,u32 handler}`, todas handler=0xd8150ed8. entry[42]=0x14, entry[4]=0x27.
- Walk (0xc0d55f58): indexa por `r22=subsys_cmd_code (@0x02)`; match rango → `callr handler` @0xc0d55ffc; **si retorno==0 → `dealloc_return` sin respuesta @0xc0d56014→0xc0d560bc**; si !=0 → commit @0xc0d560c8.
- No-encontrado / handler-null → **0x13 BAD_CMD** (0xc0d364d8, 0xc0d364f8, `combine(r17,#0x13)`).
- Handler 0xd8150ed8: gates subsys@1==0x0b (d8150f00), cmd@2==0x14 (d8150f0c), (id@4&0xfffe)==0x35a (d8150f24). **Los 3 OK → rama d8150f2c → F3 + `return 0`.** cmd!=0x14 → d8150f3c = path real (aloca vía 0xd80f40fc→0xc0d64328, responde vía 0xd814ff38 que escribe `4b 0b 14 .. 35a/35b 03`).
- 0xd8150ed8 referenciado 76× SÓLO en b21 (la tabla de rangos), 0× en b23/b26 → es el handler runtime, no un callback de boot.
- FTM corre LOCAL en MPSS (callr directo), sin forward a otro PD.

**INFERENCE:**
- 0x14 @0x02 = entrada reservada/no-op del dispatcher (self-descriptor/ping) → por eso `return 0` silencioso.
- El gate `id==0x35a` sólo aplica a la rama 0x14; los ftm_cmd reales (0x27 etc.) usan el id internamente en el packer, no como gate de entrada.
- El header CORTO previo funcionaba porque ponía el ftm_cmd en @0x02 (correcto); el header LARGO nuevo lo rompió al poner 0x14 en @0x02.

**UNKNOWN (sólo en vivo o descomprimiendo más páginas):**
- El sub_command exacto y los TLVs obligatorios de cada comando RF-test bajo @0x02=0x27 (siguen en q6zip, ver `rftest_entry_0x14.md`/`iq_sequence.md`).
- Si algún ftm_cmd espera además el id 0x35a como gate propio (validar mandando con/sin @0x04=0x35a).
- El valor numérico del sub_command de IQ_CAPTURE/RADIO_CONFIG (probar 0x27 con num_tlv=0 y COMMAND_CAPABILITY).
