"""Real serial I/O against a separate PTY RUI stand-in, never vehicle hardware."""
import os
from pathlib import Path
import pty
import select
import tempfile
import threading
import time
import unittest

from control.rak3172 import RUI, adapter_command


class Modem:
    def __init__(self, version='RUI_4.2.4_RAK3172-E', joined='1', finish=True):
        self.master, self.slave = pty.openpty()
        self.output, self.commands = [], []
        self.version, self.joined, self.finish = version, joined, finish
        self.stopped = threading.Event()
        self.radio = RUI(os.ttyname(self.slave), self.output.append, timeout=.5)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def write(self, data):
        os.write(self.master, data.encode())

    def run(self):
        buffer = b''
        values = {'VER': self.version, 'NJS': self.joined, 'NWM': '1',
                  'BAND': '5', 'MASK': '0002', 'NJM': '1', 'CLASS': 'A',
                  'DEVEUI': 'AC1F09FFFE29D8B2'}
        while not self.stopped.is_set():
            if not select.select([self.master], [], [], .05)[0]:
                continue
            buffer += os.read(self.master, 4096)
            while b'\n' in buffer:
                command, buffer = buffer.split(b'\n', 1)
                command = command.decode().strip()
                if not command:
                    continue
                self.commands.append(command)
                if command.startswith('AT+SEND='):
                    self.write('OK\r\n')
                    if self.finish:
                        self.write('+EVT:TX_DONE\r\n')
                elif command.endswith('=?') and command[3:-2] in values:
                    key = command[3:-2]
                    self.write(f'AT+{key}={values[key]}\r\nOK\r\n')
                else:
                    self.write('AT_COMMAND_NOT_FOUND\r\n')

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)
        self.radio.close()
        os.close(self.slave)
        os.close(self.master)


class AdapterTests(unittest.TestCase):
    def modem(self, **kwargs):
        modem = Modem(**kwargs)
        self.addCleanup(modem.close)
        return modem

    def test_probe_only_queries_no_keys_join_or_configuration(self):
        modem = self.modem()
        status = modem.radio.probe()
        self.assertEqual(status['mask'], '0002')
        self.assertEqual(status['njs'], '1')
        self.assertEqual(status['firmware'], ['AT+VER=RUI_4.2.4_RAK3172-E'])
        self.assertTrue(all(command.endswith('=?') for command in modem.commands))
        self.assertFalse(any('KEY' in command or 'JOIN' in command for command in modem.commands))

    def test_wrong_explicit_device_fails_after_version_query(self):
        modem = self.modem(version='ELM327')
        with self.assertRaisesRegex(ValueError, 'not a recognized'):
            modem.radio.probe()
        self.assertEqual(modem.commands, ['AT+VER=?'])

    def test_plaintext_and_oversize_never_reach_serial(self):
        modem = self.modem()
        for value in ('48656c6c6f', '03' + '00' * 50, 'not-hex', '3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                modem.radio.request({'id': 'test', 'uplink': value}, 11)
        self.assertEqual(modem.commands, [])

    def test_not_joined_does_not_send_or_auto_join(self):
        modem = self.modem(joined='0')
        with self.assertRaisesRegex(ValueError, 'not joined'):
            modem.radio.request({'id': 'test', 'uplink': '03' + '00' * 7 + '01aa'}, 11)
        self.assertEqual(modem.commands, ['AT+NJS=?'])
        self.assertEqual(modem.output, [])

    def test_ok_is_not_tx_receipt_and_rx_can_arrive_while_waiting(self):
        modem = self.modem(finish=False)
        errors = []
        def request():
            try:
                modem.radio.request({'id': 'attempt', 'uplink': '03' + '00' * 7 + '01aa'}, 11)
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=request)
        worker.start()
        deadline = time.monotonic() + 2
        while not any(row.startswith('AT+SEND=') for row in modem.commands) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(worker.is_alive())
        self.assertEqual(modem.output, [])
        # RX may precede TX_DONE; complete lines can arrive in separate reads.
        modem.write('+EVT:RX_1:-48:8:UNICAST:10:03')
        modem.write('abcd\r\n+EVT:TX_DONE\r\n')
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(modem.output, [{'downlink': '03abcd'}, {'sent': 'attempt'}])

    def test_poll_does_not_send_plaintext_or_open_receive_window(self):
        modem = self.modem()
        modem.radio.request({'poll': True}, 11)
        self.assertEqual(modem.commands, ['AT+NJS=?'])
        self.assertNotIn('sent', modem.output[0])

    def test_rx_wrong_port_and_invalid_hex_ignored(self):
        modem = self.modem()
        for line in ('+EVT:RX_1:-48:8:UNICAST:2:abcd',
                     '+EVT:RX_1:-48:8:UNICAST:10:abc',
                     '+EVT:RX_1:-48:8:UNICAST:10:zzzz'):
            modem.radio.event(line)
        self.assertEqual(modem.output, [])
        modem.radio.event('+EVT:RX_2:-48:-2:UNICAST:10:ABCD')
        self.assertEqual(modem.output, [{'downlink': 'abcd'}])

    def test_busy_device_not_shared(self):
        modem = self.modem()
        with self.assertRaises(OSError):
            RUI(os.ttyname(modem.slave), lambda _: None)

    def test_normal_role_adapter_config_explicit_and_compatible(self):
        self.assertIsNone(adapter_command({}))
        explicit = ['/usr/local/bin/existing-adapter']
        self.assertEqual(adapter_command({'radio_adapter': explicit}), explicit)
        options = {'radio_model': 'RAK3172', 'radio_device': '/dev/serial/by-id/ch340'}
        command = adapter_command(options)
        self.assertEqual(command[:4], ['/usr/bin/python3', '-m', 'control.rak3172', '--device'])
        self.assertEqual(command[command.index('--max-app-bytes') + 1], '11')
        for change in ({'radio_device': '/dev/ttyUSB0'}, {'radio_fport': 0},
                       {'max_app_bytes': True}, {'radio_device': '/dev/serial/by-id/../ttyUSB0'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter_command(dict(options, **change))

    def test_sealed_round_trip_over_actual_adapter_io(self):
        try:
            from control.radio_crypto import RadioBox, identity, pin
        except ModuleNotFoundError as exc:
            if exc.name != 'nacl':
                raise
            self.skipTest('PyNaCl required for encryption test')
        modem = self.modem()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home_key, car_key = identity(root/'home.key'), identity(root/'car.key')
            pin(root/'home.pub', bytes(home_key.public_key).hex())
            pin(root/'car.pub', bytes(car_key.public_key).hex())
            car = RadioBox(root/'car.key', root/'home.pub', root/'car.db', b'U', 11)
            home = RadioBox(root/'home.key', root/'car.pub', root/'home.db', b'D', 11)
            plaintext = b'non-GPS diagnostic message'
            received = None
            frames = car.frames(plaintext)[1]
            for index, frame in enumerate(frames):
                modem.radio.request({'id': str(index), 'uplink': frame.hex()}, 11)
                actual = bytes.fromhex(modem.commands[-1].split(':', 1)[1])
                received = home.accept(actual)
            self.assertEqual(received, plaintext)
            self.assertEqual(len([row for row in modem.output if 'sent' in row]), len(frames))
            reply = None
            for frame in home.frames(b'acknowledged')[1]:
                modem.radio.event('+EVT:RX_1:-48:8:UNICAST:10:' + frame.hex())
                reply = car.accept(bytes.fromhex(modem.output[-1]['downlink']))
            self.assertEqual(reply, b'acknowledged')
