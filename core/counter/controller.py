"""Decimal digit positioning, with interruptible sequential movements."""
from copy import deepcopy
import re
from threading import Event, RLock, Thread
from .config import integer, pulse


class Counter:
    def __init__(self, store, board):
        self.store, self.board = store, board
        self.lock = RLock()
        self.cancel = Event()
        self.worker = None
        self.armed = False
        self.busy = False
        self.requested = "0".zfill(store.data["count"])
        self.digits = [None] * 16
        self.moving_channel = None
        self.error = ""
        self.board.disable()

    def snapshot(self):
        with self.lock:
            board = self.board.probe()
            if not board["connected"]:
                self.cancel.set()
                self.armed = False
            return {"config": deepcopy(self.store.data), "number": self.requested,
                    "digits": self.digits[:self.store.data["count"]], "armed": self.armed,
                    "busy": self.busy, "moving_channel": self.moving_channel,
                    "error": self.error, "board": board, "simulated": self.board.simulated}

    def arm(self):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the current movement to stop.")
            self.board.enable()
            self.cancel.clear()
            self.armed = True
            self.error = ""

    def stop(self):
        with self.lock:
            self.cancel.set()
            self.armed = False
            if self.moving_channel is not None:
                self.digits[self.moving_channel] = None
            self.board.disable()

    def _ready(self):
        if self.busy:
            raise ValueError("A display is moving. Wait or press Stop.")
        if not self.armed:
            raise ValueError("The servos are off. Turn them on in Calibrate first.")

    def show(self, number):
        with self.lock:
            self._ready()
            count = self.store.data["count"]
            if not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,16}", number) or len(number) > count:
                raise ValueError(f"Enter 1–{count} decimal digits, without a sign or decimal point.")
            number = number.zfill(count)
            moves = []
            for channel, digit in enumerate(number):
                current = self.digits[channel]
                if current == digit:
                    continue
                target = int(digit)
                if current is None:
                    # No encoder: the first explicit command establishes a reference.
                    ticks = [target]
                else:
                    previous = int(current)
                    direction = 1 if target > previous else -1
                    ticks = range(previous + direction, target + direction, direction)
                for tick in ticks:
                    width = self.store.data["positions"][channel][tick]
                    if width is None:
                        raise ValueError(f"Number {tick} isn't set on display {channel + 1} yet. Line it up in Calibrate first.")
                    moves.append((channel, width, str(tick)))
            self.requested = number
            self._start(moves)

    def step(self, delta):
        integer(delta, -10000, 10000, "Step")
        with self.lock:
            value = int(self.requested) + delta
            if not 0 <= value < 10 ** self.store.data["count"]:
                raise ValueError("That step exceeds the display range.")
            self.show(str(value))

    def preview(self, channel, width):
        with self.lock:
            self._ready()
            integer(channel, 0, self.store.data["count"] - 1, "Channel")
            pulse(width)
            self._start([(channel, width, None)])

    def _start(self, moves):
        if not moves:
            return
        self.cancel.clear()
        self.busy = True
        self.error = ""
        settings = deepcopy(self.store.data)
        self.worker = Thread(target=self._move, args=(moves, settings), name="counter-servo", daemon=True)
        self.worker.start()

    def _move(self, moves, settings):
        try:
            for index, (channel, width, digit) in enumerate(moves):
                with self.lock:
                    if self.cancel.is_set():
                        break
                    self.moving_channel = channel
                    self.digits[channel] = None
                    self.board.write(channel, width)
                if self.cancel.wait(settings["settle_ms"] / 1000):
                    break
                with self.lock:
                    if self.cancel.is_set():
                        break
                    # One active PWM channel, including between ticks on the same servo.
                    self.board.release(channel)
                    self.digits[channel] = digit
                    self.moving_channel = None
                if index < len(moves) - 1 and self.cancel.wait(settings["pause_ms"] / 1000):
                    break
        except Exception as error:
            with self.lock:
                self.error = f"Movement failed: {error}"
                self.armed = False
                self.cancel.set()
                try:
                    self.board.disable()
                except Exception as stop_error:
                    self.error += f"; output disable failed: {stop_error}"
        finally:
            with self.lock:
                self.busy = False
                self.moving_channel = None

    def configure(self, fields):
        with self.lock:
            if self.busy:
                raise ValueError("Stop movement and wait before changing configuration.")
            if not isinstance(fields, dict) or not fields or set(fields) - {"count", "settle_ms", "pause_ms", "release_after_move"}:
                raise ValueError("Unknown or missing setup fields.")
            data = {**deepcopy(self.store.data), **fields}
            from .config import validate
            validate(data)
            self.stop()
            self.store.save(data)
            self.requested = "0".zfill(data["count"])
            self.digits = [None] * 16

    def calibrate(self, channel, positions):
        with self.lock:
            if self.busy:
                raise ValueError("Wait for the test movement to finish before saving.")
            integer(channel, 0, self.store.data["count"] - 1, "Channel")
            if not isinstance(positions, list) or len(positions) != 10:
                raise ValueError("Supply ten digit positions.")
            for width in positions:
                if width is not None:
                    pulse(width)
            data = deepcopy(self.store.data)
            data["positions"][channel] = positions
            self.stop()
            self.store.save(data)
            self.digits[channel] = None

    def close(self):
        self.stop()
        if self.worker:
            self.worker.join(timeout=6)
        self.board.close()
