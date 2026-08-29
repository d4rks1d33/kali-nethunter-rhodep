# Glitched lines while the brightness moves — investigation, parked

Status: **cause characterised, not fixed.** The patch in this directory is a
dead end kept for the next attempt, not something to build.

The symptom is a band of corrupted lines across the panel, seen while the
brightness ramps under a repainting compositor. It has been in the port since
the panel came up.

## What it actually is

Six registers, snapshotted before the error handlers clear them (that is what
`0064-DIAGNOSTIC-drm-msm-dsi-log-raw-error-registers.patch` in `kernel/patches/`
is for), read the same on every single occurrence:

```
fifo=88881010 timeout=00000001 ack=00000000 status0=00000005
phy=00088888 clk=008037c3 mdp_busy=1 dma_busy=0
```

Decoded, `fifo` is `DLN0_HS_FIFO_UNDERFLOW | DLN1 | DLN2 | DLN3`, plus
`DLN0_LP_FIFO_EMPTY`, and `status0` is `CMD_MODE_ENGINE_BUSY |
CMD_MODE_MDP_BUSY`. All four HS lanes run dry while the MDP is mid-transfer.

`dma_busy=0` is worth stating plainly: the command DMA is *not* running when
the error fires, so this is not two DMAs fighting. A DCS write lands on top of
the pixel stream and the lanes starve.

The `DLN0_LP_FIFO_EMPTY` bit is noise. `dsi_fifo_status()` raises
`DSI_ERR_STATE_FIFO` for any non-zero `FIFO_STATUS`, and that register carries
`*_FIFO_EMPTY` bits, which are the normal idle condition. A small number of
captured errors are nothing but those bits, with `mdp_busy=0`.

## How it reproduces, measured

`scripts/rhodep-glitch-capture` samples the backlight at 20 Hz for a session
and lines the DSI errors up against it. Ninety seconds of real use, scrolling
first and moving the brightness afterwards:

| what was happening              | errors |
| ------------------------------- | -----: |
| scrolling a long list           |      0 |
| brightness moving, screen still |      0 |
| brightness moving while repainting | 20 |

All twenty fell within 300 ms of a backlight write. The first error of the
session arrived at +41 s, which is when the brightness part started; forty
seconds of scrolling before it produced nothing.

Synthetic loads agree and are worth knowing about before anyone tries to
automate this: `scripts/rhodep-repaint-bench` scrolling a GPU texture at a
steady 59 fps raised almost nothing across sixteen runs, and driving the
backlight from sysfs at 20, 60, 120 and 240 Hz against a still screen raised
nothing at all. **The artefact needs the real compositor path with real touch
input.** A benchmark can measure dropped frames here; it cannot measure this.

## The mechanism

`dsi_ctrl_enable()` already sets `DSI_TRIG_CTRL_BLOCK_DMA_WITHIN_FRAME` for 6G
v1.2 and later, which asks the controller not to begin a command DMA inside a
frame. The window it may use instead lives in a separate register, and
`dsi_host.c` never writes it — zero occurrences in the whole driver. The
controller is told what not to do and never told when it may.

The vendor driver (`techpack/display/msm/dsi`, LineageOS for `holi`) programs
it in `dsi_ctrl_hw_22_configure_cmddma_window()`, enabled for controller
versions 2.2 through 2.5. SM6375 is 2.4.

```c
reg = DSI_R32(ctrl, DSI_TRIG_CTRL);
reg &= ~0xF;
reg |= 0xc;
DSI_W32(ctrl, DSI_TRIG_CTRL, reg);

reg = line_no | (window << 16);
DSI_W32(ctrl, DSI_DMA_SCHEDULE_CTRL2, reg);
```

with, for command mode, `line_no = v_active + TEARCHECK_WINDOW_SIZE` and
`window = v_active`.

Two details do not survive the move to mainline unchanged:

- `DSI_DMA_SCHEDULE_CTRL2` is at 0x104 downstream and is **not in the register
  map** mainline generates its headers from. The whole downstream map sits four
  bytes above mainline's — `DSI_TRIG_CTRL` is 0x84 there and 0x80 here — so the
  mainline offset should be 0x100. Deduced, not documented.

- `DSI_TRIG_CTRL_DMA_TRIGGER` is declared three bits wide here with an enum
  that stops at 6, and `dsi_ctrl_enable()` writes `TRIGGER_SW`. Downstream
  clears four bits and writes `0xc`, so the field is wider than this map says
  and the value cannot be spelled with the enum.

## What was tried, in order, and what each attempt cost

| attempt | change | result |
| ------- | ------ | ------ |
| v122 | window programmed once at host enable, trigger untouched | no effect, 23 errors vs 20 |
| v123 | window per transfer + trigger nibble `0xc` | **black screen** |
| v124 | v123 gated on `msm_host->enabled` | **black screen**, same failure |
| v125 | v123 gated on the MDP having completed a frame | boots, but worse |

v123 and v124 both died the same way:

```
dsi_cmds2buf_tx: cmd dma tx failed, type=0x39, data0=0xf0, len=12, ret=-110
panel: sending dcs data f0 55 aa 52 08 00 failed: -110
panel: Failed to initialize panel: -110
```

`-ETIMEDOUT` on the panel's first initialisation command. The window is
defined against the tear signal, which does not exist before the panel is up,
so the controller waited for something that never arrived.

**That failure is the strongest evidence the offset and the trigger value are
right.** Writing either to the wrong place is inert — that is exactly what v122
was. v123 blocked the DMA for real.

v124 failed because `msm_host->enabled` is the wrong gate on this panel:
`panel-novatek-nt37701.c` sets `ctx->panel.prepare_prev_first = true`, so
`msm_dsi_host_enable()` runs *before* `nt37701_prepare()` and the flag is
already true when the initialisation commands go out. The vendor's own
`display->enabled` does not mean the same thing here.

v125 gates on `DSI_IRQ_CMD_MDP_DONE` having fired, which is the point frames
are actually running. It boots, the panel initialises cleanly and DCS writes
work. It is still not a fix:

| image | errors | backlight samples that moved | errors per sample |
| ----- | -----: | ---------------------------: | ----------------: |
| v121  |     20 |                          163 |             0.123 |
| v122  |     23 |                          163 |             0.141 |
| v125  |      7 |                           37 |         **0.189** |

The raw count fell only because that session moved the brightness far less.
Per unit of brightness movement v125 is the worst of the three. It also
introduced a **new and worse symptom**: the whole panel fills with vertical
coloured lines and stays that way until a lock/unlock cycle reinitialises it,
which looks like the DSC decoder losing sync.

So the window is being programmed somewhere that corrupts the compressed
stream rather than avoiding the collision.

## Where to pick this up

The offsets are almost certainly right and the gate is now correct. What is
not known is how to compute the window for this panel. Things not yet tried:

- ~~The panel may define a schedule line and window in its own DT node.~~
  **Checked, it does not, so the fallback values the attempts used were right.**
  The property names are `qcom,mdss-dsi-dma-schedule-line` and
  `qcom,mdss-dsi-dma-schedule-window` (`dsi_panel.c`, not the names guessed
  here), both default to 0 when absent, and neither appears in
  `dsi-panel-mot-csot-nt37701-655-1080x2400-dsc-cmd-common-v1.dtsi`, in the
  tianma variant, or anywhere else in the vendor tree for this panel. With both
  at 0 `dsi_configure_command_scheduling()` takes the command-mode fallback --
  `line = TEARCHECK_WINDOW_SIZE + v_active`, `window = v_active` -- which is
  exactly what v122 to v125 programmed.

  This matters mostly for what it rules out: the failures were not caused by
  feeding the register the wrong numbers. Whatever is wrong is in when the
  write happens, not in what is written.
- `DSI_DMA_SCHEDULE_CTRL` at 0x100 downstream (0xFC here) with `BIT(28)` is the
  *video mode* path and was never touched; whether command mode also needs it
  set is unknown.
- Downstream resets the trigger controls after every transfer
  (`reset_trig_ctrl`); this patch leaves the nibble at `0xc` until the next
  full `dsi_ctrl_enable()`.
- The DSC configuration may interact: the corruption seen in v125 is
  decoder-shaped, not line-shaped.

## Dead ends, so nobody repeats them

- **Refresh rate makes no visible difference to the error rate.** Once patch
  0097 made all four rates available, the same repaint-plus-brightness workload
  was run at each: 120, 90, 60 and 48 Hz gave 0, 1, 0 and 1 `dsi_err_worker`
  lines over 20 seconds. The idea was that a slower rate leaves more idle time
  between frames for a command DMA to fit into, so the glitch should ease off at
  48 Hz. Nothing of the sort showed up.

  **But that run proves little**, and the reason is worth recording: the
  synthetic ramp in `rhodep-repaint-bench` produces 0-1 errors per run against
  70-79 in one real KWin ambient-brightness run, so there was never enough
  signal to see a difference with. Whatever KWin does that the bench does not is
  still the missing ingredient, and finding it would be worth more than another
  fix attempt. Note `dsi_err_worker()` uses `pr_err_ratelimited`, so counts are
  capped at roughly ten per five seconds and a genuinely bad run undercounts.

- **Brightness in LP instead of HS** (patch 0062): no change.
- **`clk_scale` 100 → 230**: no change to the glitch, and it added a crimson
  tint. The vendor's own DSI clock for the 60 Hz timing is 400 MHz against the
  383.3 MHz mainline computes, a 4% shortfall; 230 puts it at 881 MHz, above
  even the 120 Hz timing's 801 MHz. Clock rate is not the problem.
- **Waiting for `CMD_MODE_MDP_BUSY` to clear in software** (patch 0063): the
  wait never timed out once, and the error still fires with `mdp_busy=1`.
  Software cannot win that race, which is why the hardware window exists.
- **Deep cpuidle**: 20 errors with it on, 20 with it off. Unrelated to the
  glitch, though it does cost frames — see the note in KERNEL-TECHNICAL.md.
- **QoS LUTs**: mainline's SM6375 catalog takes `safe_lut` and `qos_lut` from
  the vendor's 120 Hz row while the panel runs at 60. Real inconsistency, but
  the 120 Hz values are the *more* aggressive ones, so correcting them would
  reduce the priority the DPU asks for, not raise it. Left alone deliberately.

## The panel runs at 60 Hz and the hardware does 120

Worth recording separately. The vendor DTSI
(`dsi-panel-mot-csot-nt37701-655-1080x2400-dsc-cmd-common-v1.dtsi`) defines four
timings — 48, 60, 90 and 120 Hz — with a 144 Hz one commented out. All four
carry **identical porches**; only the clock differs (320, 400, 601, 801 MHz).
Mainline exposes 60 Hz only. That is a feature gap, and it also explains why
the DPU catalog's LUTs are the 120 Hz ones: they were written for the rate the
panel is meant to run at.
