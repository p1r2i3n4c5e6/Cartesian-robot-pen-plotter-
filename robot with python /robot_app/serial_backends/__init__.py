"""Pluggable serial backends.

Two implementations:

- ``DirectSerialBackend``: pyserial running in its own QThread, talks
  straight to the GRBL controller (ESP32 / Arduino).  Default mode.
- ``CncjsBackend``: relays everything through a local cncjs server over
  Socket.IO, so the user can still see the job in the browser UI.

Both expose the same public surface so the GUI does not care which one
is active.  The parent :class:`robot_app.serial_worker.SerialWorker`
chooses between them based on the user's *connection mode* setting.
"""

from .direct import DirectSerialBackend  # noqa: F401
from .cncjs import CncjsBackend          # noqa: F401
