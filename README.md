# KNK Cartesian Robot Pen Plotter

A professional-grade Cartesian pen plotter control system featuring dual-motor Y-axis auto-squaring, an RC servo-driven gripper tool (A axis), and a modular PyQt5 control client.

This repository contains both the **ESP32 FluidNC firmware configuration** and the **Python desktop controller application** (with direct serial and CNCjs Socket.IO support).

---

## 📂 Repository Structure

```
.
├── config.yaml               # ESP32 FluidNC firmware configuration
├── README.md                 # Main project documentation (this file)
└── robot with python/        # PyQt5 Desktop controller application & scripts
    ├── robot.py              # Launcher script
    ├── robot1.py             # Main GUI codebase (V1)
    ├── robot2.py             # Main GUI codebase (V2)
    ├── robot_app/            # Modularized control app package
    ├── requirements.txt      # Python dependencies
    ├── settings.json         # Persisted local GUI preferences (auto-generated)
    ├── launch_both.sh        # Startup script for both CNCjs and GUI
    ├── run_robot.sh          # Launcher script for python controller
    ├── run_robot1.sh         # Launcher script variant
    ├── gcode/                # Streamed G-code jobs history
    └── logs/                 # Rolling execution logs
```

---

## ⚡ ESP32 FluidNC Firmware Configuration (`config.yaml`)

The plotter is powered by an ESP32 microcontroller running **FluidNC**, configured for Cartesian kinematics with dual Y-axis motors and an RC servo pen gripper.

### Axis Mappings & Hardware Pinouts

| Axis | Function | Pinout Configuration | Limits / Switches |
| :--- | :--- | :--- | :--- |
| **X** | Carriage Horizontal | Step: `GPIO 12`<br>Dir: `GPIO 14` | Neg: `GPIO 13`<br>Pos: `GPIO 15` |
| **Y** | Dual-Motor Gantry | Motor 0 Step: `GPIO 26`<br>Motor 0 Dir: `GPIO 27`<br>Motor 1 Step: `GPIO 25`<br>Motor 1 Dir: `GPIO 33` | Motor 0 Neg: `GPIO 18`<br>Motor 0 Pos: `GPIO 19` |
| **Z** | Pen Lift Mechanism | Step: `GPIO 32`<br>Dir: `GPIO 4` | Neg: `GPIO 21`<br>Pos: `GPIO 22` |
| **A** | Servo Gripper | PWM Output: `GPIO 23` | RC Servo (min 500µs, max 2500µs) |

> [!IMPORTANT]
> **X-Axis Limit Pin Rewiring Alert:**
> The X-axis limit switch pins have been remapped from `GPIO 16 / 17` to `GPIO 13 / 15`. This is because GPIO 16 and 17 are reserved for external PSRAM on ESP32-WROVER modules. You **must** wire your X limits to `GPIO 13` (neg) and `GPIO 15` (pos).

### Homing Cycle Sequence
* **Cycle 1**: Homing Z axis (`GPIO 21 / 22`).
* **Cycle 2**: Homing X axis (`GPIO 13 / 15`) and Y axis (`GPIO 18 / 19`) simultaneously.
* **A Axis**: Direct RC Servo control mapped to 50Hz PWM. No homing cycle is configured (Cycle -1).

---

## 🖥️ PyQt5 Desktop Controller Application

The control application (under `/robot with python /`) provides an advanced, responsive GUI to interface directly with the ESP32 hardware or relay commands via a CNCjs instance.

### Key Features
* **Dual-Mode Connectivity**: Choose between **Direct Serial** communication (pyserial in background thread) or **cncjs Socket.IO bridge**.
* **Auto-Port Detection**: Smart scanning using USB Vendor/Product IDs to automatically locate ESP32 and Arduino boards on your system.
* **Canvas Preview & Text Engine**: Real-time canvas representing the machine workspace. Supports entering text and converting QFonts into sampleable G-code toolpaths.
* **Automatic Pen Rack Changer**: Automates the pickup and park routine of physical pens using custom macro sequences with safe-Z clearance and dwells.

### Running the Application

1. **Navigate to the application folder:**
   ```bash
   cd "robot with python "
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Start the GUI:**
   ```bash
   python3 robot.py
   ```
   *Alternatively, start the modular package directly:*
   ```bash
   python3 -m robot_app
   ```

---

## 🔧 Troubleshooting & Setup

### 1. Serial Port Permission on Linux
If you receive a "Permission denied" error when connecting to `/dev/ttyUSB0` or `/dev/ttyACM0`, add your user to the `dialout` group:
```bash
sudo usermod -aG dialout $USER
newgrp dialout
```
*Note: You may need to log out and log back in for changes to take effect.*

### 2. Device Busy Errors
Ensure that no other software (such as the Arduino IDE Serial Monitor, LaserGRBL, or standard CNCjs browser instances) is currently occupying the serial port. Only one application can open the connection at a time.

### 3. Log Tracking
All events, status updates, alarms, and G-code streams are logged automatically inside `robot with python /logs/robot.log` for diagnostics.
# Cartesian-robot-pen-plotter-
