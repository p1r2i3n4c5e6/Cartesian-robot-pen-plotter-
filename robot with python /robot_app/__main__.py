"""Allow ``python -m robot_app`` to launch the GUI."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
