"""Validated, atomically saved mechanical calibration."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

# The board never sends a pulse outside this envelope, whatever range is configured.
LIMIT_MIN_PULSE = 300
LIMIT_MAX_PULSE = 3000
# The configurable pulse range starts here; widen it in Calibrate for servos that travel further.
DEFAULT_MIN_PULSE = 600
DEFAULT_MAX_PULSE = 2400
RANGE_KEYS = {"min_pulse": DEFAULT_MIN_PULSE, "max_pulse": DEFAULT_MAX_PULSE}


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}.")
    return value


def pulse(value, minimum=LIMIT_MIN_PULSE, maximum=LIMIT_MAX_PULSE):
    return integer(value, minimum, maximum, "Pulse width (µs)")


def defaults():
    return {"count": 2, "settle_ms": 500, "pause_ms": 500, "release_after_move": True,
            **RANGE_KEYS, "positions": [[None] * 10 for _ in range(16)]}


def validate(data):
    if not isinstance(data, dict) or set(data) != set(defaults()):
        raise ValueError("Configuration has missing or unknown fields.")
    integer(data["count"], 1, 16, "Display count")
    integer(data["settle_ms"], 100, 5000, "Settling time (ms)")
    integer(data["pause_ms"], 100, 5000, "Pause between moves (ms)")
    if data["release_after_move"] is not True:
        raise ValueError("Signals must be released between digit moves.")
    low = integer(data["min_pulse"], LIMIT_MIN_PULSE, LIMIT_MAX_PULSE, "Minimum pulse (µs)")
    high = integer(data["max_pulse"], LIMIT_MIN_PULSE, LIMIT_MAX_PULSE, "Maximum pulse (µs)")
    if high - low < 100:
        raise ValueError("The maximum pulse must be at least 100 µs above the minimum.")
    positions = data["positions"]
    if not isinstance(positions, list) or len(positions) != 16:
        raise ValueError("Exactly sixteen channel calibration tables are required.")
    for channel, row in enumerate(positions):
        if not isinstance(row, list) or len(row) != 10:
            raise ValueError("Each display needs ten digit positions.")
        for digit, value in enumerate(row):
            if value is not None and (type(value) is not int or not low <= value <= high):
                raise ValueError(f"Servo {channel}, number {digit} is {value} µs, outside the pulse range "
                                 f"{low}–{high} µs. Widen the range or change that pulse first.")
    return deepcopy(data)


class ConfigStore:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            self.data = defaults()
            return
        data = json.loads(self.path.read_text("utf-8"))
        # Preserve every calibration when upgrading the earlier configuration.
        if isinstance(data, dict):
            data.setdefault("pause_ms", 500)
            for key, value in RANGE_KEYS.items():
                data.setdefault(key, value)
            if data.get("release_after_move") is False:
                data["release_after_move"] = True
        self.data = validate(data)

    def save(self, data):
        candidate = validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        filename = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             delete=False, suffix=".tmp") as handle:
                filename = handle.name
                # Leave the default range out so releases without range support can still read the file.
                written = {key: value for key, value in candidate.items() if RANGE_KEYS.get(key, object()) != value}
                json.dump(written, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)
        self.data = candidate
