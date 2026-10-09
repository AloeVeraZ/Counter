"""Validated, atomically saved mechanical calibration."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

MIN_PULSE = 600
MAX_PULSE = 2400


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}.")
    return value


def pulse(value):
    return integer(value, MIN_PULSE, MAX_PULSE, "Pulse width (µs)")


def defaults():
    return {"count": 2, "settle_ms": 500, "pause_ms": 500, "release_after_move": True,
            "positions": [[None] * 10 for _ in range(16)]}


def validate(data):
    if not isinstance(data, dict) or set(data) != set(defaults()):
        raise ValueError("Configuration has missing or unknown fields.")
    integer(data["count"], 1, 16, "Display count")
    integer(data["settle_ms"], 100, 5000, "Settling time (ms)")
    integer(data["pause_ms"], 100, 5000, "Pause between moves (ms)")
    if data["release_after_move"] is not True:
        raise ValueError("Signals must be released between digit moves.")
    positions = data["positions"]
    if not isinstance(positions, list) or len(positions) != 16:
        raise ValueError("Exactly sixteen channel calibration tables are required.")
    for row in positions:
        if not isinstance(row, list) or len(row) != 10:
            raise ValueError("Each display needs ten digit positions.")
        for value in row:
            if value is not None:
                pulse(value)
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
                json.dump(candidate, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)
        self.data = candidate
