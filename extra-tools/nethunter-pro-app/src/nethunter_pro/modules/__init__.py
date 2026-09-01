"""Importing this package registers every module, in sidebar order."""
# Core / device
from . import kali_services    # noqa: F401
from . import net_control      # noqa: F401
from . import docker           # noqa: F401
from . import sysinfo          # noqa: F401
# Recon
from . import net_discovery    # noqa: F401
from . import nmap             # noqa: F401
from . import searchsploit     # noqa: F401
from . import routersploit     # noqa: F401
from . import tv_remote        # noqa: F401
# Wi-Fi
from . import wifi_attacks     # noqa: F401
from . import deauth           # noqa: F401
from . import evil_twin        # noqa: F401
from . import wifipumpkin      # noqa: F401
from . import phishkin3        # noqa: F401
from . import wifite          # noqa: F401
from . import pwnagotchi       # noqa: F401
from . import pineapple        # noqa: F401
from . import driftnet         # noqa: F401
# Payloads / exploitation
from . import payloads         # noqa: F401
from . import set_tool         # noqa: F401
# USB / radio attacks
from . import duckhunter       # noqa: F401
from . import hid              # noqa: F401
from . import usb_arsenal      # noqa: F401
from . import bluetooth        # noqa: F401
from . import blueducky        # noqa: F401
from . import carsenal         # noqa: F401
from . import gps              # noqa: F401
# Services and misc
from . import vnc              # noqa: F401
from . import custom_commands  # noqa: F401
from . import terminal         # noqa: F401
