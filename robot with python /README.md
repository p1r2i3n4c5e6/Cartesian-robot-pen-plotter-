# CNCjs Plotter Bridge — Industrial Edition

Modular PyQt5 controller for a GRBL-based pen plotter (ESP32 / Arduino /
Teensy / etc.).  Talks to the controller either directly with
`pyserial` or through a local **cncjs** server, with a full automatic
pen-changer pipeline that emits real, ready-to-stream G-code.

---

## What changed vs. `robot2.py`

| Area | Before | Now |
|------|--------|-----|
| Connectivity | cncjs Socket.IO **only** — broken if `python-socketio` was missing or cncjs wasn't installed | Two switchable backends: **Direct serial** (default) and **cncjs Socket.IO**.  Direct mode works out of the box on every Linux/Pi without cncjs. |
| Port autodetect | First USB only | Scored detection that prefers known ESP32 / Arduino / FTDI / CH340 VIDs and `/dev/serial/by-id/` symlinks |
| Text plotting | Emitted only G-code comments — text never drew | `QFont` is converted to `QPainterPath` glyphs and sampled like every other stroke |
| Pen changer | Used `G53` (machine coords) → alarm on un-homed machines | Stays in `G90` work coords, raises Z to `safe_z` first, dwell after each engagement, falls back to `M0` pause when a slot is missing |
| White-on-white text | Tool buttons / pen rack labels became invisible because per-widget styles only set the background | Every per-widget style sets **both** background and foreground; `TextPropertiesDialog` preview is dark-aware |
| GUI thread blocking | `time.sleep` ran inside Socket.IO callback / GUI thread | `QThread`-based reader, character-counting stream protocol, timer-based heartbeat |
| Subprocess cleanup | `terminate()` only — left zombie cncjs processes | Kills the whole process group via `os.killpg` |
| Logging | Just `print()` | Rotating file in `logs/robot.log` plus console |
| Architecture | One 3 600-line `.py` file | `robot_app/` package, every module under ~500 lines |

---

## Folder layout

```
robot.py                    # tiny launcher — keeps existing scripts working
requirements.txt
settings.json               # user preferences (auto-generated)
gcode/                      # last 10 streamed jobs
logs/robot.log              # rotating debug log

robot_app/
    __init__.py             # version constants
    __main__.py             # `python -m robot_app`
    app.py                  # main()
    config.py               # presets, alarm tables, paths, env hygiene
    settings.py             # JSON load/save
    logging_setup.py        # file + console logger
    styles.py               # dark theme (no white-on-white)
    text_dialog.py          # advanced text input
    canvas.py               # drawing area
    main_window.py          # docks, menus, toolbar
    serial_worker.py        # facade choosing direct vs cncjs
    serial_backends/
        __init__.py
        direct.py           # pyserial QThread
        cncjs.py            # cncjs Socket.IO bridge
    gcode/
        __init__.py
        text_render.py      # QFont -> paths
        tool_changer.py     # automatic pen-rack G-code
        generator.py        # main G-code emitter
    panels/
        __init__.py
        tool_panel.py
        color_palette.py
        console.py
        jog.py
        pen_changer.py
        safety.py
        serial_panel.py
        settings_panel.py
```

---

## How to run

```bash
cd "robot with python "
pip install -r requirements.txt    # only PyQt5 + pyserial are mandatory
python3 robot.py
```

`launch_both.sh`, `run_robot.sh` and `run_robot1.sh` continue to work
unchanged — they all still execute `python3 robot.py`.

You can also launch the package directly:

```bash
python3 -m robot_app
```

---

## Connection modes

The **Connect** tab has a `Connection Mode` dropdown:

* **Direct serial (recommended)** — talks to the controller via
  `pyserial` in a background `QThread`.  Live DRO via 250 ms `?`
  heartbeat, character-counting GRBL streaming protocol, automatic
  reconnection on USB unplug, friendly permission/busy errors.
* **Through cncjs (Socket.IO)** — auto-starts a local `cncjs`
  process if needed, authenticates, and relays everything through it.
  Lets you also see the job in the cncjs browser UI.

The same physical USB port can only be opened by **one** application at
a time — pick a mode and stick with it for that session.

---

## Automatic pen changer

Tab: **Pen Rack** (bottom dock).

1. Tick **Enable automatic pen changer**.
2. Set the approach axis (`X` or `Y`), direction
   (forward `+` or backward `-` grabs the pen) and approach distance
   (default 100 mm = 10 cm).
3. Jog to each pen's *resting position in the holder* and click that
   pen's **Save** button.  X / Y / Z are stored in `settings.json`.
4. **Test Pickup Sequence** runs the cycle on the first saved slot so
   you can verify the geometry before plotting.

When a stroke colour change happens during a job, the generator emits:

```
G90
M5
G0 Z<safe_z>
G0 X<approach_x> Y<approach_y> F<travel_feed>
G0 Z<slot_z>     F<travel_feed>
G1 X<slot_x> Y<slot_y> F<pickup_feed>   ; engage clamp
G4 P<dwell>
G1 X<approach_x> Y<approach_y>          ; retract leaving the pen
G0 Z<safe_z>
```

If a colour has no saved slot, the generator inserts an `M0` pause and
prompts the user to swap the pen by hand.

---

## Diagnosing connection issues

* **"No serial port available"** — plug the controller in, then click
  the ↻ refresh button.  USB ports appear as `/dev/ttyUSB*` or
  `/dev/ttyACM*` on Linux.  Persistent IDs from
  `/dev/serial/by-id/...` are also listed and are preferred when
  available.
* **"Permission denied opening …"** — the user is not in the
  `dialout` group:

  ```bash
  sudo usermod -aG dialout $USER
  newgrp dialout      # or log out and back in
  ```
* **"… is busy"** — another app already owns the port.  Close the
  Arduino IDE / cncjs / a stale instance and try again.
* **Direct mode shows alarms after `$H`** — make sure soft/hard limits
  are configured for your machine in the **Safety** tab.

---

## Logs

Every session is appended to `logs/robot.log` (rotating, 2 MB × 5
backups).  Send this file along with bug reports — every connect /
disconnect / stream / alarm event is recorded with timestamps.
