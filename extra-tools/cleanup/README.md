# rhodep-cleanup

	sudo ./install.sh
	sudo rhodep-cleanup            # run it now
	sudo rhodep-cleanup --dry-run  # or just see what it would free

A weekly disk-space cleanup for the phone, plus a permanent cap on the systemd
journal. The disk had drifted to 50G used; roughly 2G of that was junk that
comes straight back with normal use, and the journal alone was 633M.

## What it deletes

Only things that regenerate on their own, so deleting them costs nothing:

- **systemd journal** — vacuumed to 50M (and capped at 100M permanently, see
  below). It re-fills as the system logs.
- **apt archives + stale lists** (`apt-get clean`, `autoclean`) — re-fetched on
  the next install.
- **user caches** — GPU shader caches, thumbnails, browser caches and npm's
  `_cacache`, under every home directory. All rebuilt on demand.
- **coredumps** (`/var/lib/systemd/coredump`) — crash dumps, never needed to run
  the phone.
- **fwupd metadata cache** — re-downloaded when fwupd next refreshes.
- **rotated logs** (`*.gz`, `*.1`, ...) and any live `*.log` over 20M is
  *truncated* (not deleted, so the open file handle keeps working).
- **`/tmp` and `/var/tmp`**.

## What it never touches

Nothing dpkg owns, so **no apt hold is affected** — the holds protect packages,
and none of these files belong to a package. It also leaves alone everything
that is actually data rather than cache: the toolset in `/opt` (Havoc, wifite3,
...), Go/nuclei/pipx installs, `/var/lib/libpostal`, mariadb/postgres,
flatpak, the clamav virus signatures, and the AI tools' data under
`~/.local/share`. If you can lose it and not notice, it is here; if losing it
would break something, it is not.

## The journal cap

`journald/99-rhodep-cap.conf` sets `SystemMaxUse=100M` and
`SystemMaxFileSize=20M`. journald enforces this itself, continuously, so the
journal never grows back to hundreds of megabytes between cleanup runs. The
weekly vacuum in the script is just there to reclaim it immediately rather than
waiting for the next rotation.

## The timer

`rhodep-cleanup.timer` runs `OnCalendar=weekly` with `Persistent=true`, so if
the phone was off at the scheduled time it catches up on the next boot, and a
1h `RandomizedDelaySec` keeps it from fighting boot for I/O. The service runs at
`Nice=10` / idle I/O for the same reason.

	systemctl list-timers rhodep-cleanup.timer   # when it next runs
	journalctl -u rhodep-cleanup                 # what it freed last time

Change the schedule by editing the `.timer` (`OnCalendar=daily`, etc.) and
`systemctl daemon-reload`.
