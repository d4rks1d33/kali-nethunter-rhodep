import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from .window import PandoraEmuApp  # noqa: E402


def main() -> int:
    return PandoraEmuApp().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
