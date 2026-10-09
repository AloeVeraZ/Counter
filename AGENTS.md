# Counter development

- This project controls one PCA9685, up to 16 decimal digit modules, and no DC motors.
- Keep the control wiring: physical Pi pins 1 VCC, 3 SDA, 5 SCL, 7 OE / BCM4, 9 GND.
- Hardware mode must never silently fall back to simulation.
- Start with OE disabled; never restore motion automatically after a restart.
- Keep calibration outside installed releases. Never invent saved positions.
- Numbers are decimal strings, including sixteen-digit values and leading zeros.
- Keep movements interruptible and sequential. Stop must disable OE immediately.
- Do not claim the Pi's GPIO/USB-C can power sixteen servos. Servo V+ needs a supply sized for the actual hardware.
- Run `python -m unittest discover -s tests -v` before publishing changes.
