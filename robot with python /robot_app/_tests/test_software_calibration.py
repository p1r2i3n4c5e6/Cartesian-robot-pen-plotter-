"""End-to-end test for software-side motion calibration.

Verifies the user's exact complaint:
  "I tell the robot to move 5 cm in the software, then my robot moves
   10 to 15 cm" — the calibration wizard must produce a software scale
   that, when applied, makes every G-code coordinate physically match
   what the user requested, *without* writing to GRBL/FluidNC EEPROM.
"""
import os
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Use a temp settings file so we don't trample the user's real one.
_tmp = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
_tmp.close()
import robot_app.config as _cfg
_cfg.SETTINGS_FILE = type(_cfg.SETTINGS_FILE)(_tmp.name)
import robot_app.settings as _settings
_settings.SETTINGS_FILE = _cfg.SETTINGS_FILE

from PyQt5.QtWidgets import QApplication, QGraphicsTextItem
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtCore import qInstallMessageHandler

qt_warnings = []


def _qh(_mode, _ctx, msg):
    if 'parse stylesheet' in msg.lower() or 'qcss' in msg.lower():
        qt_warnings.append(msg)


qInstallMessageHandler(_qh)
app = QApplication(sys.argv)

import robot_app.main_window as mw
mw.QMessageBox.information = lambda *a, **k: None
mw.QMessageBox.warning = lambda *a, **k: None
mw.QMessageBox.question = lambda *a, **k: None
mw.QMessageBox.critical = lambda *a, **k: None

w = mw.MainWindow()
print('1. MainWindow built OK')

# ─── A. Default state: scales = 1.0, no compensation ───────────────────
gen = w.gcode_generator
assert gen.calibration_scales == (1.0, 1.0, 1.0)
assert not gen.calibration_active
print(f'2. Default calibration scales: {gen.calibration_scales}')

# ─── B. set_calibration_scales clamps unsafe values ────────────────────
gen.set_calibration_scales(100.0, -2.0, 0.0)   # all unsafe
# 100.0 → clamped to 10.0; -2.0 → clamped to 1.0; 0.0 → clamped to 1.0
assert gen.calibration_scales == (10.0, 1.0, 1.0), gen.calibration_scales
print(f'3. Clamping: extreme inputs → {gen.calibration_scales}')

gen.set_calibration_scales(1.0, 1.0, 1.0)      # restore

# ─── C. Build a tiny stroke and verify scale is applied ────────────────
# Place black text at (40, 40) mm so we know what coordinates appear.
pos = w.canvas.mm_to_scene(40, 40)
font = QFont('Arial', 24)
ti = QGraphicsTextItem('AB')
ti.setDefaultTextColor(QColor(0, 0, 0))
ti.setFont(font)
ti.setPos(pos)
w.canvas.scene.addItem(ti)
w.canvas.strokes.append({
    'type': 'text', 'color': QColor(0, 0, 0), 'text': 'AB',
    'pos': pos, 'font': font, 'rotation': 0.0, 'letter_spacing': 0.0,
    'item': ti,
})


def _spans(gcode):
    xs, ys, zs = [], [], []
    for line in gcode.splitlines():
        s = line.strip().upper()
        if not (s.startswith(('G0 ', 'G1 ', 'G00 ', 'G01 '))
                or s.startswith(('G0\t', 'G1\t'))):
            continue
        for tok in s.split():
            if tok.startswith('X'):
                try: xs.append(float(tok[1:]))
                except: pass
            if tok.startswith('Y'):
                try: ys.append(float(tok[1:]))
                except: pass
            if tok.startswith('Z'):
                try: zs.append(float(tok[1:]))
                except: pass
    return xs, ys, zs


# Baseline (no scaling)
gc_base = gen.generate(w.canvas.strokes)
bx, by, bz = _spans(gc_base)
assert bx and by and bz, 'no motion in baseline'
print(f'4. Baseline: X-max={max(bx):.2f}mm, Y-max={max(by):.2f}mm, '
      f'Z-max={max(bz):.2f}mm')

# Scale X by 0.667 (commanded 50 / measured 75)
gen.set_calibration_scales(0.667, 1.0, 1.0)
gc_scaled = gen.generate(w.canvas.strokes)
sx, sy, sz = _spans(gc_scaled)
# Each X in scaled output should be (corresponding baseline X) × 0.667
ratio_x = max(sx) / max(bx)
ratio_y = max(sy) / max(by)
ratio_z = max(sz) / max(bz)
assert abs(ratio_x - 0.667) < 0.005, ratio_x
assert abs(ratio_y - 1.0) < 0.005, ratio_y
assert abs(ratio_z - 1.0) < 0.005, ratio_z
print(f'5. After X×0.667 scale: X-ratio={ratio_x:.4f}, '
      f'Y-ratio={ratio_y:.4f}, Z-ratio={ratio_z:.4f}')

# Header annotation present
assert 'Software calibration ACTIVE' in gc_scaled
assert 'X×0.6670' in gc_scaled
print('6. G-code header advertises active calibration')

# Reset
gen.set_calibration_scales(1.0, 1.0, 1.0)
gc_reset = gen.generate(w.canvas.strokes)
assert 'Software calibration: 1.0000 (none)' in gc_reset
print('7. After reset, header shows "none"')

# ─── D. Comments are NOT scaled ────────────────────────────────────────
gen.set_calibration_scales(0.5, 0.5, 0.5)
out = gen._apply_calibration_to_line('; X100 Y100 Z100  ← these MUST stay')
assert 'X100' in out and 'Y100' in out and 'Z100' in out
print('8. Comments are never rescaled')

# Settings ($$ replies) are not scaled either
out = gen._apply_calibration_to_line('$100=80.000')
assert out == '$100=80.000'
print('9. $$ settings lines are never rescaled')

# G92 (set position) and $J= (jog) ARE scaled
out = gen._apply_calibration_to_line('G92 X10 Y20')
assert 'X5.000' in out and 'Y10.000' in out
out = gen._apply_calibration_to_line('$J=G91 G21 X50 F1500')
assert 'X25.000' in out
print('10. G92 and $J= jog commands ARE rescaled')

gen.set_calibration_scales(1.0, 1.0, 1.0)

# ─── E. CalibrationPanel: software-mode APPLY updates the generator ───
cp = w.calibration_panel
assert cp.sw_radio.isChecked(), 'software mode is the default'

# Capture the signal so we know it fires
signal_payloads = []
cp.software_scales_changed.connect(
    lambda x, y, z, last: signal_payloads.append((x, y, z, last))
)

row_x = cp.axes['X']
row_x.commanded_spin.setValue(50.0)
row_x.measured_spin.setValue(75.0)
row_x._update_preview()
row_x._on_apply()

# After APPLY, the row's software scale should be 50/75 = 0.667
assert abs(row_x.software_scale - (50/75)) < 1e-3
# And the generator should also have been updated via the signal
assert abs(gen.calibration_scales[0] - (50/75)) < 1e-3
print(f'11. APPLY (software): X scale = {row_x.software_scale:.4f}, '
      f'generator scale = {gen.calibration_scales[0]:.4f}')

# ─── F. JOG sends PRE-SCALED coordinates (so user can verify after fix) ──
class _Stub:
    is_connected = True
    sent = []
    def send_raw(self, c):
        self.sent.append(c); return True


stub = _Stub()
cp.serial_worker = stub
row_x._on_jog()
# Should be: G91 / G0 X<scaled> F<scaled> / G90  — both distance
# AND feed-rate are pre-multiplied by the active software scale.
assert stub.sent[0] == 'G91'
assert stub.sent[2] == 'G90'
mid = stub.sent[1]
val = float(mid.split('X')[1].split(' ')[0])
expected = 50.0 * (50/75)        # commanded × current scale
assert abs(val - expected) < 0.01, f'expected ~{expected}, got {val}'
# Feed must also be scaled: 1500 × 0.6667 ≈ 1000.
feed_val = int(mid.split('F')[1])
expected_f = round(1500 * (50/75))
assert feed_val == expected_f, f'expected F~{expected_f}, got F{feed_val}'
print(f'12. JOG sends pre-scaled coordinate + feed: {mid!r} '
      f'(expected ~X{expected:.3f} F{expected_f})')

# ─── G. RESET button ─────────────────────────────────────────────────────
stub.sent.clear()
signal_payloads.clear()
row_x._on_reset()
assert row_x.software_scale == 1.0
assert abs(gen.calibration_scales[0] - 1.0) < 1e-9
print('13. RESET: X scale back to 1.0 in panel + generator')

# ─── H. Persistence — load_from_settings round-trips correctly ─────────
saved = cp.get_settings()
assert saved['x_scale'] == 1.0
saved['x_scale'] = 0.91
saved['x_commanded'] = 100.0
saved['x_measured'] = 110.0
cp.load_from_settings(saved)
assert abs(row_x.software_scale - 0.91) < 1e-6
assert row_x.commanded_spin.value() == 100.0
assert row_x.measured_spin.value() == 110.0
print('14. load_from_settings round-trip OK')

# ─── I. Persistence — settings.json is updated on apply ──────────────
import json
with open(_tmp.name, 'r') as fh:
    on_disk_before = json.load(fh).get('calibration', {})
row_x.commanded_spin.setValue(50.0)
row_x.measured_spin.setValue(48.0)
row_x._update_preview()
row_x._on_apply()         # software apply → triggers _save_settings_to_disk
with open(_tmp.name, 'r') as fh:
    on_disk_after = json.load(fh).get('calibration', {})
assert on_disk_after.get('x_scale') != on_disk_before.get('x_scale'), \
    f'settings.json not updated:  {on_disk_before} → {on_disk_after}'
print(f'15. settings.json updated:  x_scale='
      f'{on_disk_before.get("x_scale")} → {on_disk_after.get("x_scale"):.4f}')

# ─── J. Mode switch ─────────────────────────────────────────────────────
cp.fw_radio.setChecked(True)
assert row_x.apply_btn.text() == '✓ APPLY (firmware $)'
cp.sw_radio.setChecked(True)
assert row_x.apply_btn.text() == '✓ APPLY (software)'
print('16. Mode switch toggles apply button label correctly')

# ─── K. No Qt warnings ─────────────────────────────────────────────────
assert not qt_warnings, qt_warnings[:3]
print('17. No Qt CSS parse warnings')

# ─── L. CRITICAL — jog_incremental respects calibration scale ───────────
# This is the user-reported bug:  "calibration says 10mm move = 10mm,
# but in the Jog panel a 1mm jog actually moves 10mm".
class _BackendStub:
    is_connected = True
    is_running = False
    is_paused = False
    machine_position = {'X': 0, 'Y': 0, 'Z': 0}
    active_port = ''
    active_baudrate = 0
    sent = []
    def jog_incremental(self, dx=0.0, dy=0.0, dz=0.0, da=0.0, feed=1200):
        self.sent.append({'dx': dx, 'dy': dy, 'dz': dz,
                          'da': da, 'feed': feed})
        return True


sw = w.serial_worker
# Force direct-mode for the test so we know which backend the stub
# replaces, regardless of what settings.json said.
sw._mode = 'direct'
real_backend = sw._direct
stub = _BackendStub()
sw._direct = stub                       # swap in our capture stub

# Set calibration: machine over-moves 10x on X, perfect on Y/Z
sw.set_calibration_scales(0.1, 1.0, 1.0)

# Click "jog X+1mm" in the Jog panel  (this is what _jog() calls)
sw.jog_incremental(dx=1.0, feed=1500)
last = stub.sent[-1]
assert abs(last['dx'] - 0.1) < 1e-6, \
    f"BUG STILL PRESENT: dx={last['dx']} (expected 0.1)"
print(f'18. Jog dx=1.0 with X×0.1 calibration → backend gets dx={last["dx"]:.4f}')

# Multi-axis jog
sw.jog_incremental(dx=2.0, dy=2.0, dz=2.0, feed=1500)
last = stub.sent[-1]
assert abs(last['dx'] - 0.2) < 1e-6
assert abs(last['dy'] - 2.0) < 1e-6
assert abs(last['dz'] - 2.0) < 1e-6
print(f'19. Multi-axis jog scaled per-axis: '
      f"dx={last['dx']:.3f}, dy={last['dy']:.3f}, dz={last['dz']:.3f}")

# A axis must be UNTOUCHED (rotational, not part of steps/mm)
sw.jog_incremental(da=5.0, feed=1500)
last = stub.sent[-1]
assert last['da'] == 5.0, 'A axis must NOT be scaled'
print(f'20. A-axis jog NOT scaled: da={last["da"]}')

# Reset to 1.0 — jog values should pass through untouched
sw.set_calibration_scales(1.0, 1.0, 1.0)
sw.jog_incremental(dx=1.0, feed=1500)
last = stub.sent[-1]
assert last['dx'] == 1.0
print('21. Reset to 1.0 — jog passes through untouched')

# Clamping
sw.set_calibration_scales(50.0, -1.0, 0.0)   # all unsafe
assert sw._cal_x == 10.0
assert sw._cal_y == 1.0
assert sw._cal_z == 1.0
print(f'22. SerialWorker scale clamping: {(sw._cal_x, sw._cal_y, sw._cal_z)}')

sw._direct = real_backend                # restore

# ─── M. APPLY in the panel updates BOTH generator AND serial worker ────
sw.set_calibration_scales(1.0, 1.0, 1.0)
gen.set_calibration_scales(1.0, 1.0, 1.0)
row_x = cp.axes['X']
row_x.software_scale = 1.0
cp.sw_radio.setChecked(True)
row_x.commanded_spin.setValue(50.0)
row_x.measured_spin.setValue(100.0)        # 2x over-move
row_x._update_preview()
row_x._on_apply()
assert abs(gen.calibration_scales[0] - 0.5) < 1e-6, gen.calibration_scales
assert abs(sw._cal_x - 0.5) < 1e-6, sw._cal_x
print(f'23. Panel APPLY → gen X={gen.calibration_scales[0]}, '
      f'worker X={sw._cal_x}')

# ─── N. FEED-RATE scaling — the user's "robot speed never changes" bug ─
# When firmware steps_per_mm is wrong by 10×, feed rate F is also
# wrong by 10× in physical mm/min.  Calibration must scale F too,
# otherwise the head lands on the right spot but moves at the
# old (too-fast) physical speed.
gen.set_calibration_scales(0.1, 0.1, 1.0)
src = ["G0 X10 Y20 F1500", "G1 X5 F800", "G1 Y3 F1200"]
out = gen._apply_calibration("\n".join(src)).splitlines()
# X10 → 1.000, Y20 → 2.000, F1500 → 150.0 (smallest scale = 0.1)
assert "X1.000" in out[0] and "Y2.000" in out[0], out[0]
assert "F150.0" in out[0], out[0]
# Single-axis line: X scale = 0.1, F800 → 80.0
assert "X0.500" in out[1] and "F80.0" in out[1], out[1]
# Y axis only: Y3 → 0.300, F1200 → 120.0
assert "Y0.300" in out[2] and "F120.0" in out[2], out[2]
print(f'24. G-code F scaled with distance: {out[0]!r}')

# SerialWorker.jog_incremental scales F too
sw.set_calibration_scales(0.1, 0.1, 1.0)
captured = []
class _CapBackend:
    def jog_incremental(self, **kw):
        captured.append(kw)
        return True
real_backend = sw._direct
sw._direct = _CapBackend()
sw.set_mode("direct")
sw.jog_incremental(dy=10.0, feed=1500)
sent = captured[0]
assert abs(sent["dy"] - 1.0) < 1e-6, sent              # 10 × 0.1
assert sent["feed"] == 150, sent                       # 1500 × 0.1
print(f'25. SerialWorker.jog feed scaled: dy={sent["dy"]} feed={sent["feed"]}')

# Multi-axis jog uses min scale for F
sw.set_calibration_scales(0.5, 0.1, 1.0)
captured.clear()
sw.jog_incremental(dx=10.0, dy=10.0, feed=1000)
sent = captured[0]
assert sent["feed"] == 100, sent                       # 1000 × min(0.5, 0.1)
print(f'26. Multi-axis jog uses min(scale)=0.1: feed={sent["feed"]}')

# Calibration wizard's _on_jog scales feed too
sw.set_calibration_scales(1.0, 1.0, 1.0)
gen.set_calibration_scales(1.0, 1.0, 1.0)
row_y = cp.axes['Y']
row_y.software_scale = 0.1
sent_lines = []
row_y.request_send.connect(sent_lines.append)
row_y.commanded_spin.setValue(10.0)
row_y._on_jog()
g0_line = next((l for l in sent_lines if l.startswith("G0")), "")
assert "Y1.000" in g0_line, g0_line
assert "F" in g0_line
# Default Y feed in calibration panel is 1500; 1500 × 0.1 = 150
assert "F150" in g0_line, g0_line
print(f'27. Wizard JOG scales feed: {g0_line}')

sw._direct = real_backend
sw.set_calibration_scales(1.0, 1.0, 1.0)
gen.set_calibration_scales(1.0, 1.0, 1.0)


print()
print('=' * 64)
print('SOFTWARE CALIBRATION REGRESSION GREEN — every behaviour verified')
print('=' * 64)
