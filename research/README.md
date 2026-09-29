# research/

Reverse-engineering material and findings produced while porting mainline Linux
(postmarketOS + Kali NetHunter) to the Motorola Moto G82 5G (`rhodep`, SM6375 /
Snapdragon 695). This is the analysis behind the patches in `../kernel/patches/`
and the docs in `../docs/`.

## Layout

| Dir | What it is |
|-----|-----------|
| `modem-blob/` | Modem firmware RE: the FTM/RFTEST protocol map, the CLADE decompression toolchain + decompressed Hexagon code, the full IQ-capture reverse engineering, and the finding that raw-IQ capture is structurally unreachable from the AP (cal-mode lives in resident code outside the MBN). See `modem-blob/README.md`. |
| `nfc/` | NFC (S3FWRN5) card-emulation RE: the LineageOS vendor HAL/firmware, the listen-mode activation work, and the eSE routing findings. See `nfc/README.md`. |
| `camera/` | Camera (S5KJN1) bring-up RE: the decoded vendor sensormodule power sequence, the CamX HAL / KMD analysis, the FP5 comparison, and the vendor blobs. The AP side is fully brought up; the sensor's own digital core is the remaining wall (see `../docs/CAMERA-SENSORS-FEASIBILITY.md`). |
| `docs/` | The original per-subsystem hardware maps (hardware map, PMIC/regulators, camera/flash, modem/RF/GNSS, mainline gaps, cross-reference with the port). |
| `logs/` | Raw device-tree / techpack extraction logs from the vendor sources. |

## What is NOT here: the Motorola vendor sources (`motorola/`)

The vendor kernel sources this RE was done against — Motorola's downstream
`kernel-msm` trees (three Android versions), the `techpack-*` (camera/audio/
display/video), the `wlan-*` stacks, and the split camera/display device trees —
are **~215k files and several GB**, are third-party code cloned from Motorola's
public release repos, and are intentionally **not** committed here.

They are only referenced as read-only truth (e.g. `blair-camera.dtsi`,
`cam_sensor_util.c`, the SSC config). To reproduce, clone the matching tags from
Motorola's public source drops (the tags are named in each doc, e.g.
`S1SUS32.73-13-4-3`, `MMI-S1SU32.73-34-7`, `T1SUS33.1-124-6-12`) from
<https://github.com/MotorolaMobilityLLC>, and place them under a local
`research/motorola/` (git-ignored). The findings here cite them by
`file:line` so the analysis stands on its own.

## Large artifacts

The decompressed modem code, the modem firmware blob, and the camera vendor
blobs are kept here as `.tgz`/`.gz`/`.so`/`.bin` so the expensive extraction
(libclade + qemu for the modem; the sensormodule decode for the camera) does not
have to be redone. None exceed GitHub's per-file limit. See each subdir's README
for how to use them.
