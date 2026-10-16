# Counter development

- Push development changes to `testing` for the repository owner's review. Do not push directly to or merge into `main`; the owner reviews and merges approved changes.
- Keep Counter self-contained. Describe its own behavior without references to the owner's other projects. Preserve required third-party license notices and technical sources.
- This project controls one PCA9685, up to 16 decimal digit modules, and no DC motors.
- Keep the control wiring: physical Pi pins 1 VCC, 3 SDA, 5 SCL, 7 OE / BCM4, 9 GND.
- Hardware mode must never silently fall back to simulation.
- Initialize with OE disabled and clear stale PWM, then start ready to accept commands by default (owner-requested exception). Never restore positions or motion automatically after a restart. Stop remains stopped until explicitly started again; `--start-stopped` disables the startup-ready default.
- Keep calibration outside installed releases. Never invent saved positions.
- Numbers are decimal strings, including sixteen-digit values and leading zeros.
- Keep movements interruptible and sequential. Stop must disable OE immediately.
- From a known digit, visit each neighboring saved digit before the target. Validate the entire path before moving.
- Exception chosen by the owner: Display → Test configuration counts each servo 0 up to 9 through every digit, then returns directly to 0.
- Exception chosen by the owner: a servo marked as wrapping (a 360° wheel) treats 9 and 0 as neighbours and takes the shorter way round, forward on a tie, still one digit per move.
- Always release PWM after each move, then pause. Never keep earlier channels holding while another moves.
- An unknown starting position cannot guarantee a one-digit physical move without feedback; document that limit.
- Do not claim the Pi's GPIO/USB-C can power sixteen servos. Servo V+ needs a supply sized for the actual hardware.
- Run `python -m unittest discover -s tests -v` before publishing changes.
