"""`python -m bonsai` opens the window; `python -m bonsai <command>` runs the command line tool."""

import sys

if len(sys.argv) > 1:
    from .cli import main
else:
    from .ui.main_window import run as main

sys.exit(main())
