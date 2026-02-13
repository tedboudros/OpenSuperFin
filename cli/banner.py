"""ASCII art banner for OpenSuperFin CLI."""

BANNER = r"""
  ___                   ____                        _____ _
 / _ \ _ __   ___ _ __ / ___| _   _ _ __   ___ _ __|  ___(_)_ __
| | | | '_ \ / _ \ '_ \\___ \| | | | '_ \ / _ \ '__| |_  | | '_ \
| |_| | |_) |  __/ | | |___) | |_| | |_) |  __/ |  |  _| | | | | |
 \___/| .__/ \___|_| |_|____/ \__,_| .__/ \___|_|  |_|   |_|_| |_|
      |_|                          |_|
"""

TAGLINE = "Lightweight event-driven trading advisory system"


def print_banner() -> None:
    """Print the banner and tagline."""
    print(BANNER)
    print(f"  {TAGLINE}")
    print()
