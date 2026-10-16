"""Validated, atomically saved mechanical calibration."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

# The board never sends a pulse outside this envelope, whatever range is configured.
LIMIT_MIN_PULSE = 300
LIMIT_MAX_PULSE = 4000
# Custom channels (1-15) start here; channel 0 uses the model profile below.
DEFAULT_MIN_PULSE = 600
DEFAULT_MAX_PULSE = 2400
# Channel 0 now uses the owner's FEETECH FT6335M. The PCA9685 frequency is
# shared by all sixteen outputs. Source: FEETECH specification, sections 7/11:
# https://www.feetechrc.com/Data/feetechrc/upload/file/20210805/6376375592116931139738128.pdf
SERVO_FREQUENCY_HZ = 50
FT6335M = {"model": "FEETECH FT6335M", "min_pulse": 500, "max_pulse": 2500,
           "center_pulse": 1500, "frequency_hz": SERVO_FREQUENCY_HZ, "travel_degrees": 360}
# Settings added after the first release. While they hold these defaults they are left
# out of config.json to preserve its format. Older releases still need a range
# covering any channel-0 endpoint calibration saved using the model profile.
OPTIONAL_KEYS = {"min_pulse": DEFAULT_MIN_PULSE, "max_pulse": DEFAULT_MAX_PULSE,
                 # Per servo: its wheel wraps (a 360° servo), so 9 and 0 are neighbours.
                 "wrap": [False] * 16}


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}.")
    return value


def pulse(value, minimum=LIMIT_MIN_PULSE, maximum=LIMIT_MAX_PULSE):
    return integer(value, minimum, maximum, "Pulse width (µs)")


def channel_spec(channel, data=None):
    integer(channel, 0, 15, "Channel")
    if channel == 0:
        return dict(FT6335M)
    return {"model": None, "min_pulse": data["min_pulse"] if data else LIMIT_MIN_PULSE,
            "max_pulse": data["max_pulse"] if data else LIMIT_MAX_PULSE,
            "center_pulse": None, "frequency_hz": SERVO_FREQUENCY_HZ, "travel_degrees": None}


def channel_pulse(channel, width, data=None):
    spec = channel_spec(channel, data)
    low, high = spec["min_pulse"], spec["max_pulse"]
    if type(width) is not int or not low <= width <= high:
        model = f" ({spec['model']})" if spec["model"] else ""
        raise ValueError(f"Choose a pulse from {low} to {high} µs for servo {channel}{model}.")
    return pulse(width)


def defaults():
    return {"count": 2, "settle_ms": 500, "pause_ms": 500, "release_after_move": True,
            **deepcopy(OPTIONAL_KEYS), "positions": [[None] * 10 for _ in range(16)]}


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
    wrap = data["wrap"]
    if not isinstance(wrap, list) or len(wrap) != 16 or any(type(value) is not bool for value in wrap):
        raise ValueError("Wrap-around needs a true or false setting for each of the sixteen servos.")
    positions = data["positions"]
    if not isinstance(positions, list) or len(positions) != 16:
        raise ValueError("Exactly sixteen channel calibration tables are required.")
    for channel, row in enumerate(positions):
        if not isinstance(row, list) or len(row) != 10:
            raise ValueError("Each display needs ten digit positions.")
        # Retain old channel-0 calibration verbatim, including obsolete wider
        # pulses. Motion and new calibration enforce the FT6335M range; an
        # upgrade must not discard positions or prevent the dashboard opening.
        row_low, row_high = (LIMIT_MIN_PULSE, LIMIT_MAX_PULSE) if channel == 0 else (low, high)
        for digit, value in enumerate(row):
            if value is not None and (type(value) is not int or not row_low <= value <= row_high):
                raise ValueError(f"Servo {channel}, number {digit} is {value} µs, outside the pulse range "
                                 f"{row_low}–{row_high} µs. Widen the range or change that pulse first.")
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
            for key, value in OPTIONAL_KEYS.items():
                data.setdefault(key, deepcopy(value))
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
                written = {key: value for key, value in candidate.items() if OPTIONAL_KEYS.get(key, object()) != value}
                json.dump(written, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)
        self.data = candidate
