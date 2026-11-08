"""Explicit-device RUI3 serial adapter; never changes OTAA keys or radio settings.

JSON-lines boundary for control.radio_gateway. Only version-3 sealed fragments
can be transmitted. A modem TX event is local transmission, not home delivery.
--probe performs queries only and needs exclusive access (close picocom first).
"""
import argparse
import fcntl
import json
import os
import re
import select
import sys
import termios
import time
import tty


class RUI:
    def __init__(self, device, emit, timeout=5):
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            self.previous = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[4] = settings[5] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except BaseException:
            os.close(self.fd)
            raise
        self.emit, self.timeout, self.buffer = emit, timeout, b''
        self.fport = 10
        self.auto_payload=False
        self.last_dr=None

    def close(self):
        try:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.previous)
        finally:
            try:
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            finally:
                os.close(self.fd)

    def event(self, line):
        # RUI3: +EVT:RX_1:<rssi>:<snr>:UNICAST:<fport>:<hex>
        match = re.fullmatch(r'\+EVT:RX_[12]:-?\d+:-?\d+(?:\.\d+)?:UNICAST:(\d+):([0-9a-fA-F]+)', line)
        if match:
            port, payload = int(match[1]), match[2]
            if port == self.fport and len(payload) % 2 == 0 and len(payload) <= 484:
                self.emit({'downlink': payload.lower()})

    def lines(self, timeout):
        if not select.select([self.fd], [], [], max(0, timeout))[0]:
            return []
        chunk = os.read(self.fd, 4096)
        if not chunk:
            raise OSError('Radio serial device disconnected')
        self.buffer += chunk
        if len(self.buffer) > 16384:
            raise ValueError('Radio response exceeded limit')
        parts = self.buffer.replace(b'\r', b'\n').split(b'\n')
        self.buffer = parts.pop()
        result = []
        for raw in parts:
            line = raw.decode('ascii', errors='replace').strip()
            if line:
                self.event(line)
                result.append(line)
        return result

    def command(self, command, transmit=False):
        # Drain old events first: stale TX_DONE must not acknowledge a new send.
        while self.lines(0):
            pass
        os.write(self.fd, command.encode('ascii') + b'\r\n')
        deadline = time.monotonic() + (20 if transmit else self.timeout)
        replies, accepted, transmitted = [], False, False
        while time.monotonic() < deadline:
            for line in self.lines(deadline - time.monotonic()):
                if line == command:
                    continue
                if line.startswith(('AT_', 'ERROR')) or line in ('+EVT:SEND_CONFIRMED_FAILED', '+EVT:TX_TIMEOUT'):
                    raise ValueError('Radio rejected operation: ' + line)
                if line == 'OK':
                    accepted = True
                elif line in ('+EVT:TX_DONE', '+EVT:SEND_UNCONFIRMED_OK', '+EVT:SEND_CONFIRMED_OK'):
                    transmitted = True
                elif not line.startswith('+EVT:'):
                    replies.append(line)
            if accepted and (not transmit or transmitted):
                return replies
        raise TimeoutError('No radio ' + ('TX completion' if transmit else 'command response'))

    def probe(self):
        version = self.command('AT+VER=?')
        if not any('RAK3172' in row and 'RUI_' in row for row in version):
            raise ValueError('Explicit device is not a recognized RAK3172 RUI radio')
        status = {'firmware': version}
        for field in ('NWM', 'BAND', 'MASK', 'NJM', 'CLASS', 'DEVEUI', 'NJS', 'DR'):
            replies = self.command('AT+' + field + '=?')
            values = [row.split('=', 1)[1] for row in replies if row.startswith('AT+' + field + '=')]
            status[field.lower()] = values[-1] if values else None
        return status

    def request(self, packet, max_bytes):
        if not isinstance(packet, dict):
            raise ValueError('Expected JSON object')
        if packet == {'poll': True}:
            # Query only: no unencrypted heartbeat or invented application data.
            self.emit({'status': {'joined': self.command('AT+NJS=?')}})
            return
        if set(packet) != {'id', 'uplink'} or not isinstance(packet['id'], str) or not 1 <= len(packet['id']) <= 128:
            raise ValueError('Invalid adapter request')
        value = packet['uplink']
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]+', value) or len(value) % 2:
            raise ValueError('Invalid hexadecimal payload')
        raw = bytes.fromhex(value)
        if not 9 < len(raw) <= max_bytes or raw[0] != 3 or not raw[7] < raw[8]:
            raise ValueError('Only bounded sealed radio fragments may be transmitted')
        if self.auto_payload:
            replies=self.command('AT+DR=?')
            dr=next((row.split('=',1)[1] for row in replies if row.startswith('AT+DR=')),None)
            if dr not in ('0','1','2','3','4'):raise ValueError('Unknown US915 data rate; retaining queued data')
            if dr!=self.last_dr:self.emit({'status':{'dr':dr}});self.last_dr=dr
            if len(raw)>(11 if dr=='0' else 46):raise ValueError('US915 payload capacity changed; retaining data for refragmentation')
        joined = self.command('AT+NJS=?')
        if 'AT+NJS=1' not in joined:
            raise ValueError('Radio is not joined; preserve queue and provision explicitly')
        self.command(f'AT+SEND={self.fport}:{value}', transmit=True)
        self.emit({'sent': packet['id']})


def adapter_command(options):
    """Used by the installed normal role service; opt-in and no port guessing."""
    if options.get('radio_adapter'):
        return options['radio_adapter']
    if options.get('radio_model') != 'RAK3172':
        return None
    device = options.get('radio_device')
    if not isinstance(device, str) or not device.startswith('/dev/serial/by-id/') or '..' in device.split('/'):
        raise ValueError('RAK3172 requires an explicit stable serial-by-id path')
    budget = payload_ceiling(options)
    port = options.get('radio_fport', 10)
    if type(budget) is not int or not 10 <= budget <= 242 or type(port) is not int or not 1 <= port <= 223:
        raise ValueError('Invalid radio payload budget or application port')
    return ['/usr/bin/python3', '-m', 'control.rak3172', '--device', device,
            '--max-app-bytes', str(budget), '--fport', str(port)]+(['--auto-payload'] if options.get('radio_auto_payload',True) else [])


def payload_ceiling(options):
    if type(options.get('radio_auto_payload',True)) is not bool:raise ValueError('radio_auto_payload must be a boolean')
    budget=options.get('max_app_bytes',11)
    if type(budget) is not int:raise ValueError('Invalid payload budget')
    # 11 was the commissioned conservative default. Automatic mode uses up to
    # 46 only after querying the actual US915 DR; explicit fixed mode retains it.
    return 46 if options.get('radio_auto_payload',True) and budget==11 else budget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True)
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--auto-payload',action='store_true')
    parser.add_argument('--fport', type=int, default=10, choices=range(1, 224))
    parser.add_argument('--max-app-bytes', type=int, default=11, choices=range(10, 243))
    args = parser.parse_args()
    def emit(value):
        print(json.dumps(value, separators=(',', ':')), flush=True)
    radio = RUI(args.device, emit)
    radio.fport = args.fport
    radio.auto_payload=args.auto_payload
    try:
        status = radio.probe()
        radio.last_dr=status.get('dr')
        emit({'status': status})
        if args.probe:
            return
        if status['nwm'] != '1' or status['njm'] != '1' or status['class'] != 'A':
            raise ValueError('Adapter requires explicitly provisioned LoRaWAN OTAA Class A')
        if radio.auto_payload and status['band']!='5':raise ValueError('Automatic payload sizing requires US915')
        buffer = b''
        while True:
            ready, _, _ = select.select([sys.stdin.buffer, radio.fd], [], [], 1)
            if radio.fd in ready:
                radio.lines(0)
            if sys.stdin.buffer in ready:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    return
                buffer += data
                if len(buffer) > 8192:
                    raise ValueError('Adapter input exceeded limit')
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    radio.request(json.loads(line), args.max_app_bytes)
    finally:
        radio.close()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TimeoutError) as exc:
        print('RAK3172: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
