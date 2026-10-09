"""One PCA9685 at 0x40 on I²C1. OE is active-low on BCM4."""
import time
from .config import integer, pulse


class SimulatedBoard:
    simulated = True

    def __init__(self):
        self.enabled = False
        self.pulses = {}

    def probe(self):
        return {"connected": True, "message": "Simulation · no hardware commands"}

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False
        self.pulses.clear()

    def write(self, channel, width):
        integer(channel, 0, 15, "Channel")
        pulse(width)
        if not self.enabled:
            raise RuntimeError("Outputs are disabled.")
        self.pulses[channel] = width

    def release(self, channel):
        self.pulses.pop(channel, None)

    def close(self):
        self.disable()


class PCA9685:
    simulated = False
    address = 0x40
    frequency = 50

    def __init__(self, bus=None, oe=None):
        self.bus = bus
        self.oe = oe
        self.initialized = False
        self.enabled = False
        self.error = ""
        try:
            if self.oe is None:
                from gpiozero import DigitalOutputDevice
                self.oe = DigitalOutputDevice(4, active_high=True, initial_value=True)
            self.oe.on()  # High disables PWM before opening or initializing I²C.
            if self.bus is None:
                from smbus2 import SMBus
                self.bus = SMBus(1)
            self._initialize()
        except Exception as error:
            self.error = str(error)
            self.disable()

    def _initialize(self):
        if self.oe is None or self.bus is None:
            raise RuntimeError(self.error or "GPIO4 / I²C1 is unavailable.")
        self.oe.on()
        self.enabled = False
        self.bus.write_byte_data(self.address, 0x00, 0x30)  # AI + sleep
        self.bus.write_byte_data(self.address, 0x01, 0x04)  # totem-pole
        self.bus.write_byte_data(self.address, 0xFE, round(25_000_000 / (4096 * 50)) - 1)
        self.bus.write_byte_data(self.address, 0x00, 0x20)
        time.sleep(0.005)
        self.bus.write_byte_data(self.address, 0x00, 0xA0)
        for channel in range(16):
            self.release(channel)
        self.initialized = True
        self.error = ""

    def probe(self):
        try:
            if not self.initialized:
                raise RuntimeError(self.error or "Board initialization failed; check wiring and restart Counter.")
            mode = self.bus.read_byte_data(self.address, 0x00)
            if mode & 0x10 or not mode & 0x20:
                self.initialized = False
                raise RuntimeError("Board reset detected; restart Counter before arming.")
            return {"connected": True, "message": "PCA9685 · 0x40 · I²C1 · 50 Hz"}
        except Exception as error:
            self.error = str(error)
            self.disable()
            return {"connected": False, "message": self.error}

    def enable(self):
        if not self.probe()["connected"]:
            raise RuntimeError(self.error)
        # Clear all stale pulses while OE is high, then allow signals.
        for channel in range(16):
            self.release(channel)
        self.oe.off()
        self.enabled = True

    def disable(self):
        self.enabled = False
        if self.oe is not None:
            try:
                self.oe.on()
            except Exception as error:
                self.error = f"Could not disable GPIO4 OE: {error}"
                raise RuntimeError(self.error) from error

    def write(self, channel, width):
        integer(channel, 0, 15, "Channel")
        pulse(width)
        if not self.enabled:
            raise RuntimeError("Outputs are disabled.")
        counts = round(width * self.frequency * 4096 / 1_000_000)
        try:
            self.bus.write_i2c_block_data(self.address, 0x06 + 4 * channel,
                                         [0, 0, counts & 255, counts >> 8])
        except Exception:
            self.disable()
            raise

    def release(self, channel):
        integer(channel, 0, 15, "Channel")
        self.bus.write_i2c_block_data(self.address, 0x06 + 4 * channel, [0, 0, 0, 0x10])

    def close(self):
        self.disable()
        if self.bus is not None:
            self.bus.close()
        if self.oe is not None:
            self.oe.close()
