# Counter

A Raspberry Pi dashboard for **1–16 mechanical digit displays**. Each display
has one position-controlled servo that reveals a digit from 0–9. One PCA9685
board provides all sixteen channels. No DC motor, camera, Arduino, or drive
logic is included.

The dashboard follows MotionModule's dark panels, steel-blue controls, and
self-hosted Barlow Condensed / Inter typography. The display preview resembles
the individual windowed digit modules. It works offline after installation.

## Use it on your PC

Python 3.11 or newer:

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\counter --simulate
```

Open **http://127.0.0.1:8080**. Simulation never touches GPIO or I²C. Its
calibration is saved in `~/.counter/config.json`; use `--config PATH` to choose
another file. Hardware mode never silently falls back to simulation.

## Install on Raspberry Pi OS

Use 64-bit Raspberry Pi OS with Python 3.11+, your existing Wi-Fi/Ethernet
connection, and the standard 40-pin GPIO header.

```bash
git clone https://github.com/AloeVeraZ/Counter.git
cd Counter
bash install.sh
```

The installer enables I²C, installs a `counter` service, and serves the UI on
**http://YOUR-PI-HOSTNAME.local:8080** (or the Pi's IP address). It does not change
the hostname or Wi-Fi, install a hotspot, or modify MotionModule's files. If
MotionModule is installed on this same Pi, stop its runtime before running
Counter: **two programs must never control this PCA9685 / OE pin together**.
Reboot once if I²C was previously disabled. The installer is shipped and tested
for syntax, but has not been executed on a physical Pi in this development run.

Counter's releases live in `/opt/counter/releases`; calibration lives in
`/var/lib/counter/config.json`, outside the release. The service starts with
outputs stopped. Startup, service restarts, and updates do not restore motion.

```bash
sudo systemctl status counter
journalctl -u counter -n 60
```

## Wire the board

This keeps the PCA9685 control wiring from MotionModule:

| Pi physical pin | PCA9685 |
| --- | --- |
| 1 · 3.3 V | VCC (logic power only) |
| 3 · GPIO2 | SDA |
| 5 · GPIO3 | SCL |
| 7 · GPIO4 | OE (active-low output enable) |
| 9 · GND | GND |

The software expects **I²C1, address 0x40, and 50 Hz**. Add an approximately
10 kΩ pull-up from OE to **3.3 V**, so outputs stay disabled before the service
starts. Connect digit modules left to right to channels **0, 1, …, count−1**.
For example, `42` on two modules sends digit 4 to channel 0 and digit 2 to
channel 1. Unconfigured channels receive no position commands.

### Servo power

USB-C powers the Pi; VCC powers only the board's logic. The PCA9685 does not
generate power for servos. Use a **separate regulated servo V+ supply** at the
voltage required by your servo model, sized for the servos' current (including
stall current), and connect its ground to the board/Pi ground. Do not connect
servo V+ to the Pi's 3.3 V or 5 V header pins. Adafruit explicitly advises
against powering servos from the Pi's 5 V rail because it can brown out the Pi:
[PCA9685 power guidance](https://learn.adafruit.com/16-channel-pwm-servo-driver?view=all).

Sixteen configured channels do **not** mean sixteen servos can safely draw
power from the Pi's USB-C input. Sequential movement and releasing PWM reduce
overlapping movement/holding loads, but do not establish a safe power budget.
Releasing PWM does not cut power, guarantee zero current, or hold a digit in
place. Stop outputs is a signal stop, not a power disconnect; use a physical
power disconnect where needed.

## Calibrate your digit mechanism

1. In **Display → Your setup**, choose the number of modules (1–16). Save.
2. In **Calibration**, select a display and choose **Enable servo control**
   (**Enable preview controls** in PC simulation).
3. Gently test/adjust a pulse to align a digit in the window. Enter that pulse
   in its matching 0–9 row. Repeat for each digit, then save.
4. Repeat for each display. Blank rows remain uncalibrated. Suggested input
   placeholders are **not saved calibration**, and the app refuses to show a
   digit with no saved position.
5. Enable servo control and enter a number. Leading zeros fill unused places.

Enabling control allows movement commands; it does not itself move a servo.
**Stop outputs** disables control again. Nothing enables itself on startup.

Each module has its own ten pulse widths, so spacing can be uneven or reversed.
The accepted envelope is 600–2400 µs; that is a software limit, **not a statement
that your servo can safely travel that far**. Use the servo's datasheet and the
mechanism's clearance to choose a narrower actual range. Default settling time
is 500 ms; adjust it to your mechanism. Positions are commanded without
feedback, so the UI cannot confirm physical alignment or detect a stalled or
unplugged servo.

A wheel needing a complete revolution requires a servo with position control
over sufficient travel (or mechanical gearing). A standard 180° servo cannot
directly reach a full 360° wheel. A continuous-rotation servo controls speed and
direction, not absolute position; it needs feedback and a different controller.
The supplied CAD screenshot alone does not establish the required travel.

The goBILDA standard-size category includes several different servo families.
Its regular 2000 Series Dual Mode servos have 300° of position-controlled
travel; the 5-Turn versions have 1800°. Continuous-rotation mode uses PWM to
command speed/direction, not a digit position. Confirm the exact SKU and mode
before choosing pulse limits, travel or settling time. Counter's manual
calibration does not program a servo's operating mode. Sources:
[regular Dual Mode specifications](https://www.gobilda.com/2000-series-dual-mode-servo-25-4-super-speed/),
[5-Turn specifications](https://www.gobilda.com/2000-series-5-turn-dual-mode-servo-25-2-torque/).

The +/− buttons count without wrapping at the maximum. Up to sixteen decimal
digits are kept as strings; no JavaScript floating-point rounding occurs.
Auto count runs while this dashboard is open and visible, waiting for each
move to complete. It pauses on Stop, errors, tab hiding, or range overflow.
The hardware worker always moves sequentially and Stop interrupts its wait.
Changing calibration/setup stops outputs. An interrupted move is marked unknown
and is sent again when requested after enabling control again. To reduce idle
traffic, the dashboard polls every three seconds with control disabled, every
second with control enabled, and every half second during movement/counting.
A hidden, disabled dashboard polls every fifteen seconds; foregrounding it
refreshes immediately. Stop commands are sent immediately, independently of
the polling interval.

## Updates

**System → Check for updates** compares the installed commit with this
repository's `main` branch. Checks run in the background with a fifteen-minute
cache; the button refreshes it. **Update now** stops outputs, asks a fixed
root-owned helper to install `main`, and restarts Counter. This is not an
automatic installer; updates happen only when you request them.

The installer builds/tests a new release before activating it, preserves
calibration, and restores the previous release if the service health check
fails. An I²C wiring failure is shown in the dashboard instead of falling back
to simulated hardware. The narrow sudo rule allows only the Counter update
helper with **no arguments**. It does not permit arbitrary shell commands,
repositories, or branches from the dashboard. Keep write access to `main`
restricted to people you trust to install code on the Pi.

```bash
journalctl -u counter-update -n 100
```

Use the dashboard on a **trusted local network**. It has no account login;
anyone who can reach the Pi can operate it. Same-origin command tokens prevent
drive-by commands from unrelated webpages, not access by another LAN user.
Do not forward port 8080 to the public internet.

## Development checks

```bash
python -m unittest discover -s tests -v
```

Tests cover digit mapping, sixteen-digit arithmetic, calibration persistence,
input bounds, interrupted movement, I²C register writes, fail-safe OE behavior,
and dashboard command validation. Hardware and real servo/power performance
still need verification on the actual mechanism.

The bundled fonts retain their SIL Open Font License notices in
`core/counter/static/fonts/`.
