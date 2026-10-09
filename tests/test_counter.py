from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from counter.config import ConfigStore, defaults, validate
from counter.controller import Counter
from counter.hardware import PCA9685, SimulatedBoard
from counter.web import create_app


class RecordingBoard(SimulatedBoard):
    def __init__(self):
        super().__init__()
        self.commands = []

    def write(self, channel, width):
        super().write(channel, width)
        self.commands.append((channel, width))


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ConfigStore(Path(self.temp.name) / "config.json")
        self.board = RecordingBoard()
        self.counter = Counter(self.store, self.board)
        self.fast_wait = patch.object(self.counter.cancel, "wait", side_effect=lambda timeout: self.counter.cancel.is_set())
        self.fast_wait.start()
        self.addCleanup(self.fast_wait.stop)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.counter.close)

    def calibrate(self, count=2):
        data = defaults()
        data["count"], data["settle_ms"] = count, 100
        data["positions"] = [[1000 + digit * 100 + channel for digit in range(10)] for channel in range(16)]
        self.store.save(data)
        self.counter.requested = "0" * count

    def finish(self):
        self.counter.worker.join(timeout=4)
        self.assertFalse(self.counter.busy)

    def test_stopped_startup_and_no_uncalibrated_motion(self):
        self.assertFalse(self.board.enabled)
        with self.assertRaises(ValueError):
            self.counter.show("42")
        self.counter.arm()
        with self.assertRaisesRegex(ValueError, "Calibrate digit 4 on channel 0"):
            self.counter.show("42")
        self.assertEqual(self.board.commands, [])
        self.assertEqual(self.counter.requested, "00")

    def test_decimal_digit_mapping_and_only_changed_channels(self):
        self.calibrate()
        self.counter.arm()
        self.counter.show("42")
        self.finish()
        self.assertEqual(self.board.commands, [(0,1400),(1,1201)])
        self.assertEqual(self.counter.digits[:2], ["4","2"])
        self.assertEqual(self.board.pulses, {})
        self.counter.step(1)
        self.finish()
        self.assertEqual(self.counter.requested, "43")
        self.assertEqual(self.board.commands[-1], (1,1301))
        self.assertEqual(len(self.board.commands), 3)

    def test_leading_zeros_and_all_sixteen_channels(self):
        self.calibrate(16)
        self.counter.arm()
        self.counter.show("1234567890123456")
        self.finish()
        self.assertEqual([ch for ch,_ in self.board.commands], list(range(16)))
        self.assertEqual("".join(self.counter.digits), "1234567890123456")
        self.counter.show("2")
        self.finish()
        self.assertEqual(self.counter.requested, "0000000000000002")

    def test_sixteen_digit_arithmetic_is_exact_and_does_not_wrap(self):
        self.calibrate(16)
        self.counter.arm()
        self.counter.requested = "9007199254740992"
        self.counter.step(1)
        self.finish()
        self.assertEqual(self.counter.requested, "9007199254740993")
        self.counter.requested = "9"*16
        with self.assertRaises(ValueError):
            self.counter.step(1)
        self.counter.requested = "0"*16
        with self.assertRaises(ValueError):
            self.counter.step(-1)

    def test_invalid_number_types_and_signs_never_move(self):
        self.calibrate()
        self.counter.arm()
        for number in [42,True,None,"-1","1.0","１"," 1","000","1e2",""]:
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.counter.show(number)
        self.assertFalse(self.counter.busy)
        self.assertEqual(self.board.commands, [])

    def test_stop_interrupts_wait_and_prevents_next_channel(self):
        self.fast_wait.stop()
        self.calibrate()
        self.store.data["settle_ms"] = 5000
        self.counter.arm()
        self.counter.show("42")
        deadline = time.monotonic() + 2
        while not self.board.commands and time.monotonic() < deadline:
            time.sleep(.001)
        before = time.monotonic()
        self.counter.stop()
        self.finish()
        self.assertLess(time.monotonic()-before, .5)
        self.assertFalse(self.board.enabled)
        self.assertEqual(len(self.board.commands), 1)
        self.assertIsNone(self.counter.digits[0])
        self.store.data["settle_ms"] = 100
        self.counter.arm()
        self.counter.show("42")
        self.finish()
        self.assertEqual(self.board.commands[-2:], [(0,1400),(1,1201)])

    def test_busy_commands_and_setup_are_rejected(self):
        self.fast_wait.stop()
        self.calibrate()
        self.store.data["settle_ms"] = 5000
        self.counter.arm()
        self.counter.show("42")
        with self.assertRaises(ValueError):
            self.counter.show("12")
        with self.assertRaises(ValueError):
            self.counter.configure({"count":3})
        self.counter.stop()

    def test_calibration_round_trip_and_setup_disarm(self):
        table = [2000-digit*100 for digit in range(10)]
        self.counter.arm()
        self.counter.calibrate(1,table)
        self.assertFalse(self.counter.armed)
        restored = ConfigStore(self.store.path)
        self.assertEqual(restored.data["positions"][1],table)
        self.assertEqual(restored.data["positions"][0],[None]*10)
        self.counter.configure({"count":1})
        self.assertEqual(self.counter.requested,"0")
        self.assertFalse(self.counter.armed)
        self.counter.configure({"count":2})
        self.assertEqual(self.store.data["positions"][1],table)

    def test_every_move_releases_even_with_legacy_hold_setting(self):
        self.calibrate()
        self.store.data["release_after_move"] = False
        self.counter.arm()
        self.counter.show("42")
        self.finish()
        self.assertEqual(self.board.pulses,{})
        with patch.object(self.board,"write",side_effect=OSError("I2C gone")):
            self.counter.show("43")
            self.finish()
        self.assertFalse(self.counter.armed)
        self.assertFalse(self.board.enabled)
        self.assertIn("I2C gone",self.counter.error)
        self.assertIsNone(self.counter.digits[1])

    def test_known_positions_advance_by_one_digit_with_a_pause(self):
        self.calibrate()
        self.counter.digits[:2] = ['1','2']
        self.counter.requested = '12'
        self.counter.arm()
        waits = []
        with patch.object(self.counter.cancel, 'wait', side_effect=lambda seconds: waits.append(seconds) or False):
            self.counter.show('43')
            self.finish()
        self.assertEqual(self.board.commands, [(0,1200),(0,1300),(0,1400),(1,1301)])
        self.assertEqual(waits, [.1,.5,.1,.5,.1,.5,.1])
        self.assertEqual(self.counter.digits[:2], ['4','3'])

    def test_reverse_tick_path_requires_intermediate_calibration_before_motion(self):
        self.calibrate()
        self.counter.digits[:2] = ['4','2']
        self.counter.requested = '42'
        self.store.data['positions'][0][2] = None
        self.counter.arm()
        with self.assertRaisesRegex(ValueError,'Calibrate digit 2'):
            self.counter.show('12')
        self.assertEqual(self.board.commands, [])
        self.assertEqual(self.counter.requested,'42')
        self.store.data['positions'][0][2] = 1200
        self.counter.show('12')
        self.finish()
        self.assertEqual(self.board.commands, [(0,1300),(0,1200),(0,1100)])

    def test_at_most_one_active_pwm_across_all_sixteen_channels(self):
        self.calibrate(16)
        original_write = self.board.write
        active = []
        def checked_write(channel,width):
            self.assertEqual(self.board.pulses,{})
            original_write(channel,width)
            active.append(len(self.board.pulses))
        with patch.object(self.board,'write',side_effect=checked_write):
            self.counter.arm()
            self.counter.show('1234567890123456')
            self.finish()
        self.assertEqual(active,[1]*16)
        self.assertEqual(self.board.pulses,{})

    def test_old_config_preserves_calibration_and_disables_hold_mode(self):
        data = defaults()
        del data['pause_ms']
        data['release_after_move'] = False
        data['positions'][0][4] = 1400
        self.store.path.write_text(json.dumps(data))
        restored = ConfigStore(self.store.path)
        self.assertEqual(restored.data['positions'][0][4],1400)
        self.assertEqual(restored.data['pause_ms'],500)
        self.assertTrue(restored.data['release_after_move'])
        with self.assertRaises(ValueError):
            self.counter.configure({'release_after_move':False})

    def test_stop_during_pause_prevents_next_tick(self):
        from threading import Event
        self.fast_wait.stop()
        self.calibrate()
        self.counter.digits[:2] = ['1','2']
        reached_pause = Event()
        original_wait = self.counter.cancel.wait
        def waiting(seconds):
            if seconds == .1:
                return False
            reached_pause.set()
            return original_wait(5)
        self.counter.arm()
        with patch.object(self.counter.cancel,'wait',side_effect=waiting):
            self.counter.show('42')
            self.assertTrue(reached_pause.wait(2))
            self.assertEqual(self.board.pulses,{})
            self.counter.stop()
            self.finish()
        self.assertEqual(self.board.commands,[(0,1200)])
        self.assertEqual(self.counter.digits[0],'2')
        self.assertFalse(self.counter.armed)

    def test_invalid_config_does_not_overwrite_calibration(self):
        self.calibrate()
        original = self.store.path.read_text()
        invalid = deepcopy(self.store.data)
        invalid["positions"][0][0] = float("nan")
        with self.assertRaises(ValueError):
            self.store.save(invalid)
        self.assertEqual(self.store.path.read_text(),original)
        for count in [0,17,True,2.0,"2"]:
            with self.subTest(count=count),self.assertRaises(ValueError):
                self.counter.configure({"count":count})
        with self.assertRaises(ValueError):
            self.counter.configure({"motor":True})


class FakeOE:
    def __init__(self): self.high=True
    def on(self): self.high=True
    def off(self): self.high=False
    def close(self): pass


class FakeBus:
    def __init__(self): self.registers={}; self.writes=[]; self.fail=False
    def read_byte_data(self,address,register):
        if self.fail: raise OSError("Board disconnected")
        return self.registers.get(register,0)
    def write_byte_data(self,address,register,value): self.registers[register]=value
    def write_i2c_block_data(self,address,register,values):
        if self.fail: raise OSError("Board disconnected")
        self.writes.append((address,register,values))
    def close(self): pass


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.bus,self.oe=FakeBus(),FakeOE()
        self.board=PCA9685(self.bus,self.oe)
        self.addCleanup(self.board.close)

    def test_init_full_off_and_correct_pwm_registers(self):
        self.assertTrue(self.oe.high)
        self.assertEqual(len(self.bus.writes),16)
        self.assertTrue(all(write[2]==[0,0,0,16] for write in self.bus.writes))
        self.assertEqual(self.bus.registers[0xFE],121)
        self.assertTrue(self.bus.registers[0]&0x20)
        self.board.enable()
        self.assertFalse(self.oe.high)
        self.board.write(15,1500)
        self.assertEqual(self.bus.writes[-1],(0x40,0x42,[0,0,51,1]))
        self.board.release(15)
        self.assertEqual(self.bus.writes[-1],(0x40,0x42,[0,0,0,16]))
        self.board.disable()
        self.assertTrue(self.oe.high)

    def test_failed_write_disables_oe(self):
        self.board.enable()
        self.bus.fail=True
        with self.assertRaises(OSError): self.board.write(0,1500)
        self.assertTrue(self.oe.high)
        self.assertFalse(self.board.enabled)

    def test_disconnect_and_chip_reset_fail_closed(self):
        self.board.enable()
        self.bus.registers[0]=0x01
        self.assertFalse(self.board.probe()["connected"])
        self.assertTrue(self.oe.high)
        self.assertFalse(self.board.initialized)

    def test_signal_bounds_reject_nan_boolean_and_invalid_channel(self):
        self.board.enable()
        for width in [599,2401,float('nan'),float('inf'),True,"1500",1500.5]:
            with self.subTest(width=width),self.assertRaises(ValueError): self.board.write(0,width)
        for channel in [-1,16,True]:
            with self.assertRaises(ValueError): self.board.write(channel,1500)


class FakeUpdates:
    def snapshot(self,refresh=False): return {"available":False,"checking":False}
    def start(self): return "Update started"


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.counter=Counter(ConfigStore(Path(self.temp.name)/'config.json'),SimulatedBoard())
        self.app=create_app(self.counter,FakeUpdates())
        self.client=self.app.test_client()
        import re
        html=self.client.get('/').get_data(as_text=True)
        self.token=re.search(r'name="counter-token" content="([^"]+)"',html)[1]
        self.headers={"X-Counter-Token":self.token}
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.counter.close)

    def post(self,path,data,**kwargs): return self.client.post(path,json=data,headers=self.headers,**kwargs)

    def test_dashboard_and_static_assets_work_offline(self):
        html=self.client.get('/').get_data(as_text=True)
        self.assertIn('Make numbers move.',html)
        for path in ['/static/counter.css','/static/counter.js','/static/fonts/inter-latin.woff2']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code,200)
        state=self.client.get('/api/state').json
        self.assertTrue(state['simulated'])
        self.assertFalse(state['armed'])

    def test_commands_require_token_and_same_origin(self):
        self.assertEqual(self.client.post('/api/arm',json={}).status_code,403)
        headers={**self.headers,'Origin':'http://unrelated.example'}
        self.assertEqual(self.client.post('/api/arm',json={},headers=headers).status_code,403)
        self.assertEqual(self.post('/api/arm',{}).status_code,200)
        self.assertTrue(self.counter.armed)

    def test_calibration_setup_and_invalid_payloads(self):
        self.assertEqual(self.post('/api/calibration',{'channel':0,'positions':[1500]*10}).status_code,200)
        self.assertEqual(self.post('/api/setup',{'count':1}).status_code,200)
        self.post('/api/arm',{})
        for payload in [{'number':4},{'number':'-1'},{'number':'1','motor':True},[],None]:
            response=self.client.post('/api/number',data=json.dumps(payload),content_type='application/json',headers=self.headers)
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.post('/api/number',{'number':'4'}).status_code,200)
        self.counter.worker.join(timeout=2)
        self.assertEqual(self.client.get('/api/state').json['digits'],['4'])

    def test_update_stops_outputs(self):
        self.counter.arm()
        self.assertEqual(self.post('/api/updates',{}).status_code,200)
        self.assertFalse(self.counter.armed)


if __name__=='__main__': unittest.main()
