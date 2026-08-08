# debos build helpers

- `wip.toml` — copy to `kali-nethunter-pro/devices/qcom/configs/wip.toml`. Defines
  rhodep (chipset sm6375, vendor motorola, model rhodep) and the boot image offsets.
- `binwrap/debos` — wrapper that adds `--disable-fakemachine` (needed with no KVM,
  e.g. in a container). Put `binwrap/` first in PATH so `build.sh` picks it up.
  Requires the real `debos` in PATH (`go install github.com/go-debos/debos/cmd/debos@latest`).
- `binwrap/systemd-nspawn` — wrapper adding `--register=no --keep-unit` for
  running nspawn inside a container. Also set `SYSTEMD_NSPAWN_UNIFIED_HIERARCHY=1`
  when invoking build.sh.

See the top-level README "Building the Kali rootfs (debos)" for the full flow.
