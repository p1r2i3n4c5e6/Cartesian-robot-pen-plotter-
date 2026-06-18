"""Verify the jog-cancellation bug is fixed.

User-reported bug:
  "in jog xyz shows in mm and my robot have 60 cm of movement but in
   the panel if i move approx 60 cm then it move 90 mm"

Root cause: the Jog panel was sending GRBL ``$J=`` (interactive jog)
which CANCELS the previous jog mid-motion when a new one arrives.
12 rapid clicks of "+50 mm" (intended 600 mm total) ended up moving
only ~90 mm.

Fix: switch the regular Jog panel to ``G91 G0 ... F<feed>`` followed
by ``G90`` — the same proven sequence the calibration wizard uses.
G0 commands queue through the planner buffer so rapid clicks now
ACCUMULATE instead of cancelling each other.
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from robot_app.serial_backends.direct import DirectSerialBackend
from robot_app.serial_backends.cncjs import CncjsBackend


# ─── A. DirectSerialBackend uses G91 G0 (not $J=) ──────────────────────
b = DirectSerialBackend()
sent = []
b.is_connected = True
b.send_raw = lambda cmd: (sent.append(cmd) or True)

ok = b.jog_incremental(dx=50.0, feed=1500)
assert ok
assert sent == ['G91', 'G0 X50.000 F1500', 'G90'], sent
assert not any(c.startswith('$J=') for c in sent), \
    'BUG: backend still uses $J= which cancels in-flight jogs'
print('1. DirectSerialBackend now uses 3-line G91 / G0 / G90 (not $J=)')

# ─── B. Multi-axis encoded together in one G0 line ─────────────────────
sent.clear()
b.jog_incremental(dx=10.0, dy=20.0, dz=5.0, feed=900)
assert sent == ['G91', 'G0 X10.000 Y20.000 Z5.000 F900', 'G90'], sent
print(f'2. Multi-axis jog fits in one G0 line: {sent[1]!r}')

# ─── C. Rapid-click accumulation simulation (the user's scenario) ──────
# 12 successive 50mm jogs should issue 12 separate queued G0 moves.
sent.clear()
for _ in range(12):
    b.jog_incremental(dx=50.0, feed=1500)
g0_lines = [c for c in sent if c.startswith('G0 ')]
g91_lines = [c for c in sent if c == 'G91']
g90_lines = [c for c in sent if c == 'G90']
assert len(g0_lines) == 12, len(g0_lines)
assert len(g91_lines) == 12, len(g91_lines)
assert len(g90_lines) == 12, len(g90_lines)
total_x = sum(float(c.split('X')[1].split()[0]) for c in g0_lines)
assert abs(total_x - 600.0) < 1e-6, total_x
print(f'3. 12× rapid +50mm clicks → 12 queued G0 moves '
      f'totalling {total_x} mm (was ~90 mm with $J=)')

# ─── D. CncjsBackend got the same fix ──────────────────────────────────
c = CncjsBackend()
c.is_connected = True
c_sent = []
c.send_raw = lambda cmd: (c_sent.append(cmd) or True)
c.jog_incremental(dy=-25.0, feed=2000)
assert c_sent == ['G91', 'G0 Y-25.000 F2000', 'G90'], c_sent
print('4. CncjsBackend also uses G91 / G0 / G90 in cncjs mode')

# ─── E. Rejects when not connected ─────────────────────────────────────
b.is_connected = False
assert b.jog_incremental(dx=10.0, feed=1500) is False
print('5. jog_incremental rejects when disconnected')

# ─── F. Empty / zero deltas → no command sent ──────────────────────────
b.is_connected = True
sent.clear()
b.jog_incremental(dx=0.0, dy=0.0, dz=0.0, feed=1500)
assert sent == [], sent
print('6. Zero-delta jog sends nothing')

# ─── G. Float feed gets coerced safely ─────────────────────────────────
sent.clear()
b.jog_incremental(dx=1.0, feed='garbage')
assert any('F1200' in c for c in sent), sent
print('7. Garbage feed value falls back to 1200 mm/min')

print()
print('=' * 64)
print('JOG ACCUMULATION FIX VERIFIED — 600 mm now means 600 mm')
print('=' * 64)
