# ftm_phase_byte — What sets the FTM phase byte `0xcb8dbec8` (struct `0xcb8dbeb0` +0x18) to 3

SM6375 baseband (MPSS.HI.4.3.4), Hexagon QDSP6. Static, read-only RE of the
decompressed image `/tmp/modemre/clade_dec_36m.bin` (VA base `0xd8000000`).
Legend: **FACT** = verified in the image bytes · **INFERENCE** = deduced with basis ·
**UNKNOWN** = not statically determinable.

---

## TL;DR

- **The one and only writer of `0xcb8dbec8` (= `0xcb8dbeb0+0x18`) in the entire 36 MB image is
  `0xd80d1534: memb(##0xcb8dbeb0+0x18) = #0x3`**, inside function **`0xd80d1464`**. There is
  **no other store to that byte anywhere** (whole-image scan; only that single immediate-3
  store). **FACT.**
- `0xd80d1464` is the **lazy one-time initializer of the global FTM command-dispatch
  service** (call it `ftm_dispatch_init`). It self-guards: on entry it reads the phase
  byte and, if it is already 3, returns immediately; otherwise it builds the service
  (allocations + subsystem registration) and, near the end, stamps `+0x18 = 3` as the
  "**service initialized / ready**" sentinel. **FACT.**
- The byte is therefore **not a multi-value walk-to-3 enum**. It is a binary
  **"initialized" flag**: **0 = not yet initialized (boot .bss value), 3 = initialized/ready**.
  The magic value is literally `3`; nothing ever writes 1 or 2, and nothing ever resets it.
  **FACT.**
- **Trigger:** `0xd80d1464` is invoked **lazily on the first FTM request that reaches any of
  the four FTM request-validators** (`0xd80d1750` = the query in the task, plus siblings
  `0xd80d13d0`, `0xd80d15c0`, `0xd80d1660`). Those validators are the shared FTM
  dispatch/validate primitive called from **~1800 FTM/RF handler sites**. So the phase
  becomes 3 the **first time the modem processes essentially any FTM/DIAG-FTM command** — it
  is not a dedicated "enter cal" command. **FACT.**
- `0xcb8dbeb0` is a **different object** from the RF-cal *session* (the one with `+0xc` and
  `+0x89a8`). `0xcb8dbeb0` is a small, statically-located **global** (seg24 .bss, fixed VA)
  = the FTM dispatch-service control block. The session is a large, **runtime-allocated**
  per-tech object (LTE FTM object near `0xca79a820`). **FACT.**

---

## 1. The writer(s) of `0xcb8dbec8 = 3` — FACT

Whole-image scan (`scan_phase.py` / `scan_phase2.py` / `scan_phase3.py` /
`scan_phase4.py`, plus an authoritative llvm-objdump pass over every immext into
`0xcb8dbe80..0xcb8dbf40`): there is exactly **one** store to `struct+0x18`.

```
d80d1464  0cb876fa  immext(#0xcb8dbe80)
d80d1468  78004610  r16 = ##0xcb8dbeb0            ; (llvm prints ##-0x34724150)
d80d1470  91104302  r2  = memb(r16+#0x18)         ; read phase
d80d1474  2402e38c  if (cmp.eq(r2.new,#0x3)) jump 0xd80d1588   ; already ready -> return
d80d1478..d80d1530  ...init work (allocs + subsystem calls; see §4)...
d80d1534  3c10cc03  memb(r16+#0x18) = #0x3        ; <<<< WRITES THE PHASE BYTE = 3
...
d80d1588  3e041f40  r17:16 = memd(r29+#0x0); dealloc_return
```

- **Writer VA: `0xd80d1534`** (`memb(##0xcb8dbeb0+0x18) = #0x3`). **FACT.**
- **Enclosing function: `0xd80d1464`** (prologue `allocframe(#0x8)`, `dealloc_return`
  at `0xd80d1588`). **FACT.**
- No absolute-store form (`memX(##0xcb8dbec8)=…`) exists; the write is register-relative
  off the struct-base pointer loaded at `0xd80d1464/1468`. **FACT.**
- **No reset**: nothing in the image writes 0/1/2 (or anything else) to `+0x18`. The scan
  found stores to `+0x0`(word), `+0x4`(word), `+0x8`(half `+0x10` field), `+0xc`(word) of
  the struct — but the ONLY `+0x18` store is the `=3` above. **FACT.**

Address arithmetic note: two of the sibling validators load the base as
`##0xcb8dbec0` and read `memb(base+#0x8)`; `0xcb8dbec0+0x8 == 0xcb8dbec8 == 0xcb8dbeb0+0x18`
— **it is the same byte**, just reached through a different immediate base. **FACT.**

---

## 2. The command/event that triggers the writer — FACT / INFERENCE

### 2.1 Direct callers of the writer function `0xd80d1464`

`refs36.py 0xd80d1464` → **4 callers**, all inside the FTM dispatch cluster:

| caller (call site) | enclosing validator fn | how it reaches the writer |
|--------------------|------------------------|---------------------------|
| `0xd80d1790`       | `0xd80d1750` (**the task's query fn**) | phase!=3 → `call 0xd80d1464` → re-read |
| `0xd80d13ec`       | `0xd80d13d0`           | phase!=3 → `call 0xd80d1464` → re-read |
| `0xd80d15dc`       | `0xd80d15c0`           | phase!=3 → `call 0xd80d1464` → re-read |
| `0xd80d1684`       | `0xd80d1660`           | phase!=3 → `call 0xd80d1464` → re-read |

All four validators have the **identical shape** (verified by disassembly):

```
r18 = ##struct                         ; 0xcb8dbeb0 (or 0xcb8dbec0)
r2  = memb(struct+0x18)                ; read phase   (or +0x8 with the ec0 base)
if (r2 == 3) skip-init                 ; already ready
    call 0xd80d1464                    ; <-- lazy init: builds service, sets phase=3
r1  = memub(struct+0x18)               ; re-read phase
if (r1 != 3) -> error return           ; init failed
... bounds-check memh(struct+0x10) > memh(request+0x2) ...
callr <per-request handler>            ; dispatch the FTM request
```

So the phase is written **as a side effect of the first FTM request that runs one of these
validators while the service is still uninitialized**. **FACT.**

### 2.2 Who calls the validators (the actual command surface)

`refs36.py 0xd80d1750` returns **~1800 call sites** spanning the whole FTM/RF handler
space (`0xd800xxxx`, `0xd815xxxx`–`0xd82axxxx`, `0xd83xxxxx`, `0xd84xxxxx`, `0xd86xxxxx`,
`0xd87xxxxx`, etc.). This is a **shared FTM request validate/log-alloc/dispatch primitive**,
not a single command. Examples from prior passes that also call it: `0xd81505c8`,
`0xd815062c`, `0xd81507e8`, `0xd815232c`, … (the `0xd815xxxx` FTM handler cluster). The
sibling validators are narrower (`0xd80d13d0` ← `0xd8069d0c`; `0xd80d15c0` ←
`0xd80d2640`,`0xd80f5234`,`0xd833ada0`,`0xd84215b4`,`0xd85ba088`). **FACT.**

### 2.3 Category of the trigger

**Category = "FTM service first-use (lazy init)", reached from a DIAG/FTM command.**
Because the validators front essentially the entire FTM command dispatch, the phase byte is
set to 3 the **first time the modem handles any FTM/DIAG-FTM request** (`ftm_cmd` via the
DIAG FTM subsystem, incl. RFTEST 0x27, FTM_COMMON `0xd8271290`, etc.). It is **NOT** gated
by a specific sub-command, an operating-mode change, a QMI handler, or a msgr message — any
one of them that ends up in an FTM handler → validator → init will stamp it. **FACT** that
the mechanism is lazy-init-on-first-FTM-request; **INFERENCE** that in a normal FTM bring-up
the very first FTM DIAG packet (e.g. the FTM_SET_MODE / subsystem-activate that precedes
RFTEST) is what triggers it.

---

## 3. The enum at `struct+0x18` — meaning and "walk to 3" — FACT

- **It is not an incrementing phase enum.** Every reader in the image compares the byte to
  **exactly `3`** and does nothing with any other value except "call init". The only writer
  writes the constant `3`. Boot value is `0` (see §4, .bss). **FACT.**
- **Value map (FACT):**
  - `0x00` — **uninitialized** (boot; the byte lives in .bss = zero at load).
  - `0x03` — **initialized / service ready** (magic "ready" sentinel written by
    `ftm_dispatch_init` `0xd80d1464`).
  - `0x01`, `0x02`, `0x04+` — **never written anywhere** in the image (dead values).
- **"Sequence that walks it to 3":** there is no multi-step walk. A **single** event —
  the first FTM request through any validator while phase==0 — calls `0xd80d1464`, which
  writes `3` once and then guards against re-init forever after. So the "sequence" is
  literally *one* FTM command reaching the FTM dispatcher. **FACT.**
- **Interpretation of the number 3:** it is a fixed "ready/valid" magic (a common QC
  idiom for a "module initialized" cookie), not `FTM_PHASE_CAL`. The cal-specific logic
  lives elsewhere (the `session+0x89a8=7` / `session+0xc=2` gates and the per-carrier
  band-mode selectors), **not** in this byte. This byte only asserts "the FTM command
  dispatch service is up so the request may be dispatched." **INFERENCE** (basis: sole
  value written is the ready sentinel; the byte gates dispatch, not cal-mode).

---

## 4. `0xcb8dbeb0` vs the session object — FACT

**They are different objects.**

- **`0xcb8dbeb0` (the phase struct):** a small, **statically-located global**. Segment scan
  of `modem.mdt`: it lies in **seg24 = pure .bss** (`p_va=0xc9138000`, `filesz=0`,
  `memsz=0x2e17000`, RW-), at `off_in_seg=0x27a3eb0`. **Boot value = 0** (zeroed .bss).
  Physical (via the fixed `PA = VA − 0x35000000` rule established in `gate_globals_phys.md`):
  `PA(struct)=0x968dbeb0`, `PA(phase byte)=0x968dbec8`. Its observed field layout (from the
  validators + init):
  - `+0x0`(half) / `+0x2` — request-ID range / lo bound (`memh`)
  - `+0x4`(word) — a pointer/handle (set at `0xd80d1488`,`0xd80d149c`)
  - `+0x8`(word) — a pointer (`0xd80d1494`)
  - `+0xc`(word) — a sub-object pointer (`0xd80d1500`)
  - `+0x10`(half) — **handler-table count / max request index** (bounds-checked vs
    `memh(request+0x2)` in every validator; init'd from `add(##0x1000,#0)` at `0xd80d14b0`)
  - `+0x14`(word) — **handler table base** (read at `0xd80d179c` etc. before `callr`)
  - `+0x18`(byte) — **the phase / "initialized" flag** (this report's target)
  This is the **FTM command-dispatch service control block**. **FACT.**

- **The RF-cal *session* (with `+0xc` and `+0x89a8`):** a **large, runtime-allocated**
  per-tech FTM object (base ≈ `0xca79a820` LTE FTM object; passed by pointer,
  `session = memw(ctx)` in TECH_ENTER `0xd81dfeec`). `+0x89a8` (~35 KB into the object) is
  the RF-mode byte; `+0xc` is the session-mode word. All accesses are through a runtime
  pointer, never a fixed immediate. **FACT** (corroborated by `tech_enter_callback.md`,
  `cal_trigger_chain.md`, `tech_state_gate.md`).

- **Relationship:** the phase struct **gates whether an FTM request is dispatched at all**
  (`0xd80d1750` returns nonzero only when `phase==3` **and** the request passes the bounds
  check and the per-request `callr` handler returns nonzero). The RFTEST-0x10xx config
  command relies on `0xd80d1750()!=0` (writer #2 path `0xd84b1f78` → `0x89a8=7` @
  `0xd84b2124`) to be allowed to proceed. So the phase struct is an **upstream enabling
  precondition** for the session's `+0x89a8` write — but it is a **separate global**, not the
  session. **FACT.**

---

## 5. Full cal-mode entry recipe (byte-exact where possible) — FACT / INFERENCE / UNKNOWN

The `0xcb8dbec8==3` requirement is **satisfied automatically** by FTM bring-up; it is a
precondition (service-up), not a distinct command you must craft. The full recipe:

1. **(Automatic) FTM service init → `0xcb8dbec8 = 3`.**
   Send **any FTM DIAG command** that reaches the FTM dispatcher. In practice the FTM
   session is opened first (FTM subsystem activate / `FTM_SET_MODE`, see
   `ftm_subsys_activate.md` / `ftm_set_mode_verified.md`); the very first such packet runs a
   validator (`0xd80d1750`/siblings), which calls `0xd80d1464`, which writes
   `memb(0xcb8dbeb0+0x18)=3` at `0xd80d1534`. After this, all `0xd80d1750()` calls that pass
   their per-request check return nonzero (phase gate satisfied). **FACT** (mechanism);
   **INFERENCE** (that the normal first FTM packet is the trigger). **No dedicated byte
   pattern is needed for this step** — it is a side effect of first FTM use.

2. **Arm the session RF-cal mode → `session+0x89a8 = 7`.**
   Send the **RFTEST config command**: DIAG **`ftm_cmd = 0x27`**, RFTEST sub-command
   **`0x10xx`**, with the flattened-request **byte `+0x0f == 0`** (the config-apply branch).
   Dispatcher `0xd8272cd8` → `callr 0xd826d3e4/0xd8292298` selects the config branch
   `0xd8292298` (fn `0xd8291504`) when `byte+0x0f==0` → RF driver `0xd8d3ed08` /
   `0xd84b1f78`. The `=7` (vs 2/0) is written iff the internal selectors resolve to cal:
   - `0xd80d1750()!=0` — **requires `0xcb8dbec8==3`** (step 1) **plus** request validity;
     stashed at `r29+0x1c`, used at `0xd84b2104`, `r2=#7` @`0xd84b211c`, store @`0xd84b2124`.
   - and/or the per-carrier band-mode selector `memub(idx*0x178 + ≈0xcb9eb074) ∉ {9,0xa}`
     (writer #1 `0xd8d3ee28`, gated `r20==3`).
   **FACT** (path & gates, from `cal_trigger_chain.md`). Exact TLV byte offsets of the
   `0x10xx` payload beyond `+0x0f/+0x10/+0x11/+0x0a` are in `rftest_command_format.md`.

3. **Enter → TECH_ENTER opens the cal branch.**
   Send **`ftm_cmd = 0x27`**, RFDEBUG sub **`0x000D`** (TECH_ENTER, TECH=1/LTE). Commit
   `0xd81dfecc` **reads** `memb(session+0x89a8)` @`0xd81dff20`, `cmp.eq(#7)` @`0xd81dff30`,
   and requires `session+0xc==2` @`0xd81e5d20`; when `0x89a8==7`, the LTE per-tech callback
   worker (`0xd8247574`, consumer `0xd8247b24`) runs the RF-config/cal block. **FACT.**

**What is in-image vs out-of-image:**
- The **writer of the phase byte (`0xd80d1534`)** and its trigger path are **fully in-image**
  and byte-exact. **FACT.**
- The **live value** of `0xcb8dbec8` is runtime-only (.bss = 0 at boot). The dump bytes read
  0. Observing `==3` at runtime needs a live device (DIAG peek of PA `0x968dbec8`, subject to
  the MSS/XPU restriction noted in `gate_globals_phys.md`). **UNKNOWN** statically.
- The `7-vs-2-vs-0` outcome of step 2 depends on **live internal RF state** (carrier
  band-mode class, session phase). If the modem is not actually in FTM/cal service with the
  carrier's RF config loaded, step 2 writes `2` or `0`, not `7`. That part is
  **runtime-state-dependent**, not out-of-image but not statically decidable. **INFERENCE.**

**Byte-exact summary of the DIAG packets** (framing per `rftest_command_format.md` /
`diag_transport_full.md`):
```
Step 1 (implicit): any FTM DIAG packet through the FTM dispatcher
                    -> ftm_dispatch_init 0xd80d1464 -> memb(0xcb8dbeb0+0x18)=3
   (typically the FTM_SET_MODE / FTM subsystem-activate packet you already send first)
Step 2 (arm):  DIAG FTM  ftm_cmd=0x27  RFTEST sub=0x10xx  request[+0x0f]=0x00
                    -> session+0x89a8 = 7   (when phase==3 and RF selectors == cal)
Step 3 (enter):DIAG FTM  ftm_cmd=0x27  RFDEBUG sub=0x000D  (TECH=1/LTE)
                    -> reads session+0x89a8==7 && session+0xc==2 -> cal branch
```

---

## 6. FACT / INFERENCE / UNKNOWN — with VAs

**FACT**
- Sole writer of `0xcb8dbec8` (`0xcb8dbeb0+0x18`): `0xd80d1534 memb(##0xcb8dbeb0+0x18)=#0x3`,
  in fn `0xd80d1464`. Whole-36 MB scan finds no other `+0x18` store, and no absolute-form
  store to `0xcb8dbec8`.
- `0xd80d1464` self-guards on entry (`memb(+0x18)==3 → return` @`0xd80d1470/1474`,
  ret @`0xd80d1588`) → it is a **lazy one-time init**; `3` is a "ready" sentinel.
- Direct callers of `0xd80d1464`: `0xd80d1790` (in `0xd80d1750`, the task's query),
  `0xd80d13ec` (in `0xd80d13d0`), `0xd80d15dc` (in `0xd80d15c0`), `0xd80d1684`
  (in `0xd80d1660`). All four are FTM request validators of identical shape.
- The query `0xd80d1750`: `r18=##0xcb8dbeb0` @`0xd80d1780`, `r2=memb(r18+0x18)` @`0xd80d1788`,
  `cmp.eq(#3)`; if!=3 `call 0xd80d1464` @`0xd80d1790`; re-read; bounds-check
  `memh(+0x10)>memh(req+2)` @`0xd80d17c8`; then `callr` per-request handler @`0xd80d17ec`;
  return depends on phase + handler.
- `0xd80d1750` is called from ~1800 sites across the FTM/RF stack → shared FTM
  validate/dispatch primitive.
- Struct `0xcb8dbeb0` is in seg24 .bss (`0xc9138000`/`0x94138000`, filesz 0), boot value 0;
  `PA(phase)=0x968dbec8`.
- The RF-cal *session* (`+0xc`, `+0x89a8`) is a distinct runtime-allocated object
  (base ≈ `0xca79a820`), addressed via a pointer, never a fixed immediate.
- `0xcb8dbec0+0x8` == `0xcb8dbeb0+0x18` == `0xcb8dbec8` (same byte, two base immediates).
- Arm/enter chain (step 2/3) VAs as in `cal_trigger_chain.md`
  (`0xd8272cd8`, `0xd8292298`/`0xd8291504`, `0xd8d3ed08`/`0xd8d3ee28`,
  `0xd84b1f78`/`0xd84b2124`, `0xd81dfecc`/`0xd81dff30`, `0xd81e5d20`).

**INFERENCE**
- The number `3` is a generic "module initialized / valid" cookie, not a cal-specific
  phase (basis: only value ever written; gates dispatch, not cal).
- In a normal FTM bring-up the first FTM DIAG packet (FTM_SET_MODE / subsystem-activate)
  is what lazily triggers `0xd80d1464` and stamps `+0x18=3` before RFTEST is sent.
- The `7-vs-2-vs-0` result of step 2 is decided by live RF/carrier state.

**UNKNOWN**
- Runtime live value of `0xcb8dbec8` (.bss=0 statically; needs live device / DIAG peek;
  MSS/XPU restricted per `gate_globals_phys.md`).
- Live values of the internal cal selectors `memub(idx*0x178+≈0xcb9eb074)` and
  `session+0x89a8`/`+0xc`.
- Exact seg21-rodata resource/task names referenced by `0xd80d1464` (seg21 is
  q6zip-compressed in the raw `.bNN`; not decoded here).

---

## 7. Repro commands

```
cd /tmp/modemre
# The sole writer + its enclosing init fn:
./dis36.sh 0xd80d1464 0xd0            # init; entry guard @1470/1474; memb(+0x18)=#3 @1534; ret @1588
python3 scan_phase4.py               # whole-image: only +0x18 store is d80d1534 (=3)
python3 refs36.py 0xd80d1464         # 4 callers (the four FTM validators)
# The query and its siblings (all set phase via 0xd80d1464 on first use):
./dis36.sh 0xd80d1750 0x80           # task's query: r18=##0xcb8dbeb0; memb(+0x18)==3; callr dispatch
./dis36.sh 0xd80d13d0 0x40           # sibling (base 0xcb8dbec0, +0x8)
./dis36.sh 0xd80d15c0 0x40           # sibling
./dis36.sh 0xd80d1660 0x40           # sibling (base 0xcb8dbeb0, +0x18)
python3 refs36.py 0xd80d1750         # ~1800 FTM/RF callers -> shared FTM primitive
# Arm/enter chain (step 2/3): see cal_trigger_chain.md
```
