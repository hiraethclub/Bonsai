"""Entry point for packaged builds (PyInstaller). The packaged app is the window only;
the command line tools are run from source: python -m bonsai <command>."""

import sys

from bonsai.ui.main_window import run

sys.exit(run())
