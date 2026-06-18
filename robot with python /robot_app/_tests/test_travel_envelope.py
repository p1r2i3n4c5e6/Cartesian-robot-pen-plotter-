"""Verify the Travel-Envelope group fixes the user's soft-limit bug.

User-reported bug:
  "MY MAX LENGTH IS 61 CM BUT THE JOG ENDS AT 80 MM AS PER THE JOG.
   ONLY SPEED CHANGES AFTER CALIBRATING — NOT THE DISTANCE.
   IF I PUT ANY MEASURED INPUT IN CALIBRATION, ONLY SPEED CHANGES."

Root cause:  GRBL/FluidNC clips every move at the soft-limit boundary
``$130`` / ``$131`` / ``$132`` (X / Y / Z max travel).  Calibration only
adjusts steps/mm — it cannot change the firmware's max-travel envelope.
The user had ``$131 = 80`` so every Y jog was clipped at 80 mm
regardless of what they entered into the calibration wizard.

Fix:  surface a Travel-Envelope group right inside the Jog panel with
X/Y/Z spinboxes and an Apply button that writes ``$130/$131/$132`` to
the controller.  Also mirror values from the Homing panel's ``$$``
read-back so what's shown matches what's in firmware.
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from PyQt5.QtWidgets import QApplication
from robot_app.main_window import MainWindow

app = QApplication.instance() or QApplication([])
w = MainWindow()
jog = w.jog_panel

# ─── A. Travel-envelope group exists with X / Y / Z spinboxes ──────────
assert hasattr(jog, 'env_spins'), 'JogPanel missing env_spins'
assert set(jog.env_spins.keys()) == {'X', 'Y', 'Z'}, jog.env_spins.keys()
print('1. Jog panel exposes env_spins for X / Y / Z')

# Reasonable defaults that match a typical 60cm cartesian
assert jog.env_spins['X'].value() == 610.0
assert jog.env_spins['Y'].value() == 610.0
assert jog.env_spins['Z'].value() == 100.0
print(f'2. Defaults: X={jog.env_spins["X"].value()}mm  '
      f'Y={jog.env_spins["Y"].value()}mm  '
      f'Z={jog.env_spins["Z"].value()}mm')

# ─── B. Apply pushes the right $$ commands to the controller ───────────
sent = []
# `is_connected` is a property — fake connection at the backend level.
backend = jog.serial_worker.backend
backend.is_connected = True   # type: ignore[attr-defined]
real_send = jog.serial_worker.send_raw
jog.serial_worker.send_raw = lambda cmd: (sent.append(cmd) or True)
try:
    jog.env_spins['X'].setValue(610.0)
    jog.env_spins['Y'].setValue(610.0)
    jog.env_spins['Z'].setValue(100.0)
    jog._apply_envelope()
    assert sent == ['$130=610.000', '$131=610.000', '$132=100.000'], sent
    print(f'3. Apply sends: {sent}')
finally:
    jog.serial_worker.send_raw = real_send

# ─── C. Apply WHILE DISCONNECTED warns instead of silently failing ─────
sent.clear()
backend.is_connected = False   # type: ignore[attr-defined]
try:
    jog.serial_worker.send_raw = lambda cmd: (sent.append(cmd) or True)
    # Patch QMessageBox so the test doesn't actually pop a dialog.
    from PyQt5 import QtWidgets
    seen = {}
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda *a, **k: seen.setdefault('called', True)
    )
    jog._apply_envelope()
    assert seen.get('called'), 'expected QMessageBox.warning when offline'
    assert sent == [], 'must NOT send commands when disconnected'
    print('4. Apply while disconnected → warning, no commands sent')
finally:
    jog.serial_worker.send_raw = real_send
    backend.is_connected = True   # type: ignore[attr-defined]

# ─── D. update_envelope_from_grbl mirrors the firmware values ──────────
jog.env_spins['X'].setValue(1.0)
jog.env_spins['Y'].setValue(1.0)
jog.env_spins['Z'].setValue(1.0)
jog.update_envelope_from_grbl({130: 600.0, 131: 610.0, 132: 95.0})
assert jog.env_spins['X'].value() == 600.0
assert jog.env_spins['Y'].value() == 610.0
assert jog.env_spins['Z'].value() == 95.0
print('5. update_envelope_from_grbl populates spinboxes from $$ data')

# Out-of-range / missing keys are ignored gracefully.
jog.update_envelope_from_grbl({999: 'garbage', 130: 'nope'})
assert jog.env_spins['X'].value() == 600.0  # unchanged
print('6. Garbage / missing $$ keys are ignored gracefully')

# ─── E. Homing panel emits grbl_settings_loaded after $$ parse ─────────
# Connect a fresh spy directly to the signal — MainWindow already
# connected the homing → jog wiring at construction time, so we
# verify the END-TO-END flow updates the jog spinboxes.
captured = []
hp = w.homing_panel
hp.grbl_settings_loaded.connect(lambda d: captured.append(dict(d)))
hp._reading_grbl = True
hp._read_buffer = {130: 700.0, 131: 700.0, 132: 110.0}
# Patch QMessageBox so tests are silent.
from PyQt5 import QtWidgets
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: None)
# Reset jog spinboxes so we can detect they were updated by the signal.
jog.env_spins['X'].setValue(1.0)
jog.env_spins['Y'].setValue(1.0)
jog.env_spins['Z'].setValue(1.0)
hp._apply_read_buffer()
assert captured, 'grbl_settings_loaded should have fired'
assert captured[-1] == {130: 700.0, 131: 700.0, 132: 110.0}, captured
# AND the jog panel's spinboxes should have been updated end-to-end.
assert jog.env_spins['X'].value() == 700.0, jog.env_spins['X'].value()
assert jog.env_spins['Y'].value() == 700.0
assert jog.env_spins['Z'].value() == 110.0
print(f'7. End-to-end: $$ read → jog envelope updated to '
      f'X={jog.env_spins["X"].value()}  '
      f'Y={jog.env_spins["Y"].value()}  '
      f'Z={jog.env_spins["Z"].value()}')

print()
print('=' * 64)
print('TRAVEL-ENVELOPE FIX VERIFIED — soft-limit editor works end-to-end')
print('=' * 64)
