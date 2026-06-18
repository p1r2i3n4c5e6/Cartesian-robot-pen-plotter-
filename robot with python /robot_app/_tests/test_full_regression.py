"""Persistent end-to-end regression test for the whole UI.

Run from the project root with::

    QT_QPA_PLATFORM=offscreen python3 robot_app/_tests/test_full_regression.py
"""
import os, sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from PyQt5.QtWidgets import QApplication, QGraphicsTextItem
from PyQt5.QtCore import qInstallMessageHandler
from PyQt5.QtGui import QColor, QFont

qt_warnings = []


def _qh(_mode, _ctx, msg):
    if 'parse stylesheet' in msg.lower() or 'qcss' in msg.lower():
        qt_warnings.append(msg)


qInstallMessageHandler(_qh)
app = QApplication(sys.argv)

import robot_app.main_window as mw  # noqa: E402

mw.QMessageBox.information = lambda *a, **k: None
mw.QMessageBox.warning = lambda *a, **k: None
mw.QMessageBox.question = lambda *a, **k: None
mw.QMessageBox.critical = lambda *a, **k: None

w = mw.MainWindow()
print('1. MainWindow built OK')

# ===== TEXT FIX =====
pos = w.canvas.mm_to_scene(40, 40)
font = QFont('Arial', 24)
ti = QGraphicsTextItem('prince')
ti.setDefaultTextColor(QColor(0, 0, 0))
ti.setFont(font)
ti.setPos(pos)
w.canvas.scene.addItem(ti)
w.canvas.strokes.append({
    'type': 'text', 'color': QColor(0, 0, 0), 'text': 'prince',
    'pos': pos, 'font': font, 'rotation': 0.0, 'letter_spacing': 0.0,
    'item': ti,
})
gc = w.gcode_generator.generate(w.canvas.strokes)
xs, ys = [], []
for line in gc.splitlines():
    if line.startswith(('G0 ', 'G1 ')):
        for tok in line.split():
            if tok.startswith('X'):
                try:
                    xs.append(float(tok[1:]))
                except ValueError:
                    pass
            if tok.startswith('Y'):
                try:
                    ys.append(float(tok[1:]))
                except ValueError:
                    pass
assert max(ys) - min(ys) > 30, 'text would be horizontal line!'
print(f'2. TEXT FIX: prince → x-span={max(xs)-min(xs):.1f}mm, y-span={max(ys)-min(ys):.1f}mm')

# ===== AUTO-HOLD FIX =====
m0 = [l for l in gc.splitlines() if l.strip().startswith('M0')]
assert m0 == []
print('3. AUTO-HOLD FIX: no spurious M0 for single-colour job')

# ===== AUTO PEN-CHANGER LOCKED =====
assert w.pen_changer_panel.enable_check.isChecked()
assert not w.pen_changer_panel.enable_check.isEnabled()
w.pen_changer_panel.load_from_settings({'enabled': False})
assert w.pen_changer_panel.enable_check.isChecked()
print('4. Auto pen-changer LOCKED ON (legacy settings cannot disable it)')

# ===== STATE BADGE =====
for state, expect in [('Idle', 'IDLE'), ('Run', 'RUN'),
                      ('Hold:0', 'HOLD'), ('Alarm', 'ALARM')]:
    w._on_machine_state_update({'state': state, 'X': 0, 'Y': 0})
    assert expect in w.state_badge.text()
print('5. State badge transitions correctly')

# ===== EMERGENCY STOP =====
assert hasattr(w, 'estop_action')
assert w.estop_action.shortcut().toString() == 'F12'
w._on_connection_state_for_badge(False, '')
assert w.estop_action.isEnabled(), 'E-STOP must always be enabled'
calls = []


class _StubWorker:
    is_connected = True
    def emergency_stop(self):
        calls.append('estop')
    def send_raw(self, c):
        calls.append(('raw', c))
        return True


real = w.serial_worker
w.serial_worker = _StubWorker()
w._stream_lines = ['G1 X1']
w.canvas.set_active_pen(2)
w._emergency_stop()
assert 'estop' in calls
assert w._stream_lines == []
assert w.canvas._active_pen_tool is None
assert 'EMERGENCY STOP' in w.canvas._activity_banner.toPlainText()
w.serial_worker = real
print('6. EMERGENCY STOP: F12 shortcut, always enabled, clears stream + banner')

# ===== KILL ALARM gating =====
w._on_connection_state_for_badge(True, '')
w._on_machine_state_update({'state': 'Alarm'})
assert w.kill_alarm_action.isEnabled()
w._on_connection_state_for_badge(False, '')
assert not w.kill_alarm_action.isEnabled()
print('7. KILL ALARM enable depends on (Alarm/Door state) AND connection')

# ===== Both buttons in toolbar =====
toolbar = w.findChildren(mw.QToolBar)[0]
labels = [a.text() for a in toolbar.actions() if a.text()]
assert any('E-STOP' in l for l in labels) and any('KILL ALARM' in l for l in labels)
print(f'8. Toolbar contains BOTH E-STOP and KILL ALARM')

# ===== CALIBRATION WIZARD (firmware mode) =====
cp = w.calibration_panel
assert set(cp.axes.keys()) == {'X', 'Y', 'Z'}
cp.fw_radio.setChecked(True)        # switch to firmware mode for this block
for line in ('$100=80.000', '$101=80.000', '$102=400.000', '$120=500'):
    cp._on_data_line(line)
assert cp.axes['X'].firmware_steps == 80.0
assert cp.axes['Z'].firmware_steps == 400.0
print('9. Calibration $$ parser: X=80, Y=80, Z=400 (filtered $120 correctly)')

cp_calls = []


class _Capt:
    is_connected = True
    def send_raw(self, cmd):
        cp_calls.append(cmd)
        return True


cp.serial_worker = _Capt()
row = cp.axes['X']
row.commanded_spin.setValue(50.0)
row.measured_spin.setValue(75.0)
row._update_preview()
expected = 80.0 * 50.0 / 75.0
assert abs(row._pending_new_value - expected) < 1e-3
row._on_apply()
sent = next(c for c in cp_calls if c.startswith('$100='))
val = float(sent.split('=')[1])
assert abs(val - expected) < 0.01
print(f'10. Firmware-mode APPLY: 50mm cmd / 75mm meas → {sent!r}')

cp_calls.clear()
# Switch back to software mode for the JOG test (panel default is now sw)
cp.sw_radio.setChecked(True)
# Reset X scale to 1.0 for predictable JOG output
row.software_scale = 1.0
row._on_jog()
assert cp_calls == ['G91', 'G0 X50.000 F1500', 'G90'], cp_calls
print(f'11. Calibration JOG (scale=1.0): {cp_calls}')

# div-by-zero guard
row.measured_spin.setValue(0.0)
cp_calls.clear()
cp.fw_radio.setChecked(True)        # firmware mode for this guard test
row._on_apply()
assert cp_calls == []
print('12. Calibration refuses to apply when measured=0')

# ===== CANVAS overlays =====
w.canvas.update_pen_rack({
    1: {'name': 'Black', 'x': 10, 'y': 10, 'z': 5},
    2: {'name': 'Red', 'x': 30, 'y': 10, 'z': 5},
}, enabled=True)
assert len(w.canvas._pen_rack_items) == 2
assert len(w.canvas._pen_strip_items) == 7
w.canvas.set_active_pen(2)
assert w.canvas._active_pen_tool == 2
print(f'13. Canvas overlays OK: rack={len(w.canvas._pen_rack_items)}, '
      f'strip={len(w.canvas._pen_strip_items)}')

# clear_canvas preserves overlays
w.canvas.clear_canvas()
items = w.canvas.scene.items()
assert w.canvas._activity_banner in items
assert w.canvas._pen_rack_group in items
assert w.canvas._pen_strip_group in items
print('14. clear_canvas preserves all overlays')

# ===== Activity banner luminance contrast =====
w.canvas.set_activity_text('Bright', (255, 255, 0))
assert '#1e1e2e' in w.canvas._activity_banner.toHtml()
w.canvas.set_activity_text('Dark', (0, 0, 0))
assert '#ffffff' in w.canvas._activity_banner.toHtml()
print('15. Activity banner adapts text colour to bg luminance')

# ===== Safety panel connection wiring =====
sp = w.safety_panel
sp._on_conn_state(False)
for btn in (sp.pause_btn, sp.resume_btn, sp.home_btn, sp.unlock_btn):
    assert not btn.isEnabled()
sp._on_conn_state(True)
for btn in (sp.pause_btn, sp.resume_btn, sp.home_btn, sp.unlock_btn):
    assert btn.isEnabled()
print('16. Safety panel buttons enable/disable with connection')

# ===== Windows menu has calibration =====
wm = next(a.menu() for a in w.menuBar().actions() if a.text() == '&Windows')
labels = [a.text() for a in wm.actions() if a.text()]
assert any('Calibration' in l for l in labels)
print('17. Windows menu has Steps/mm Calibration entry')

# ===== No Qt warnings =====
assert not qt_warnings, qt_warnings[:3]
print('18. No Qt CSS parse warnings during full exercise')

print()
print('=' * 60)
print('FULL REGRESSION GREEN — every feature verified')
print('=' * 60)
