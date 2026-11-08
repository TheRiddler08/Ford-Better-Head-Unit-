"""One durable non-GPS challenge/echo over the existing encrypted radio boundary.

This adds no encryption scheme: RadioBox remains the only wire encryption and
the existing shared link key authenticates the compact diagnostic application.
Only a matching authenticated radio echo can mark a car test passed.
"""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from control.update_agent import atomic

VERSION = 4
SIZE = 34


def packet(kind, nonce, key):
    if kind not in (1, 2, 3) or len(nonce) != 16 or len(key) < 32:
        raise ValueError('Invalid diagnostic packet')
    body = bytes([VERSION, kind]) + nonce
    return body + hmac.new(key, b'car-radio-diagnostic-v1\0' + body, hashlib.sha256).digest()[:16]


def decode(raw, key):
    if len(raw) != SIZE or raw[0] != VERSION or raw[1] not in (1, 2, 3) or len(key) < 32:
        raise ValueError('Invalid diagnostic frame')
    expected = packet(raw[1], raw[2:18], key)
    if not hmac.compare_digest(expected, raw):
        raise ValueError('Diagnostic authentication failed')
    return raw[1], raw[2:18]


class Diagnostic:
    def __init__(self, path, key):
        self.path, self.key = Path(path), key

    def read(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self, doc):
        atomic(self.path, json.dumps(doc, separators=(',', ':')).encode())

    def start(self):
        doc = self.read()
        if not doc:
            doc = {'phase': 'running', 'nonce': secrets.token_hex(16),
                   'started': time.time(), 'expires': time.time() + 3*3600,
                   'message': 'Encrypted radio check running; GPS reporting remains deferred.'}
            self.save(doc)
        return self.outgoing()

    def restart(self):
        previous=self.read()
        if previous.get('phase')=='running':
            outgoing=self.outgoing()
            if outgoing is not None:return outgoing
            previous=self.read()
        if 0<=time.time()-previous.get('started',0)<300:raise ValueError('Wait five minutes between radio tests')
        self.save({'phase':'running','nonce':secrets.token_hex(16),'started':time.time(),'expires':time.time()+3*3600,
                   'message':'Encrypted radio check running; GPS reporting remains deferred.'})
        return self.outgoing()

    def outgoing(self):
        doc = self.read()
        if doc.get('phase') != 'running':
            return None
        if time.time() > doc['expires']:
            doc.update(phase='timed-out', finished=time.time(),
                       message='Encrypted radio check timed out; no matching radio echo received. GPS reporting remains deferred.')
            self.save(doc)
            return None
        return packet(1, bytes.fromhex(doc['nonce']), self.key)

    def receive(self, raw):
        kind, nonce = decode(raw, self.key)
        doc = self.read()
        if kind != 2 or not doc or doc.get('phase') != 'running':
            return False
        if time.time() > doc['expires'] or not hmac.compare_digest(nonce.hex(), doc['nonce']):
            return False
        doc.update(phase='passed', finished=time.time(),
                   elapsedSeconds=round(time.time()-doc['started'], 1),
                   message='Encrypted LoRaWAN round trip passed: Tiny to home and home to Tiny, with authenticated matching reply. GPS reporting remains deferred.')
        self.save(doc)
        print(doc['message'], flush=True)
        return True


class Responder:
    def __init__(self, path, key):
        self.path, self.key = Path(path), key

    def receive(self, raw):
        kind, nonce = decode(raw, self.key)
        if kind != 1:
            return
        previous = json.loads(self.path.read_text()) if self.path.exists() else {}
        if previous.get('nonce') == nonce.hex():
            return
        atomic(self.path, json.dumps({'phase': 'echo-pending', 'nonce': nonce.hex(),
              'received': time.time(), 'expires': time.time()+3*3600,
              'message': 'Authenticated encrypted car challenge received; echo pending. Car receipt not yet confirmed.'}).encode())

    def outgoing(self):
        if not self.path.exists():
            return None
        doc = json.loads(self.path.read_text())
        report=self.path.with_name('diagnostic-result.json')
        if report.exists():
            result=json.loads(report.read_text())
            if result.get('phase')=='passed' and result.get('nonce')==doc['nonce']:
                return None
        if time.time() > doc['expires']:
            return None
        # Repeat within the existing persistent downlink budget until expiry:
        # broker TX and even gateway TX do not prove receipt by the car.
        return packet(2, bytes.fromhex(doc['nonce']), self.key)


def record_report(path, report):
    """Authenticated IP carries a result report, never the test challenge/echo."""
    import re
    if not isinstance(report,dict) or report.get('phase') not in ('running','passed','timed-out'):
        raise ValueError('Invalid diagnostic result')
    nonce=report.get('nonce')
    if not isinstance(nonce,str) or not re.fullmatch('[0-9a-f]{32}',nonce):
        raise ValueError('Invalid diagnostic identity')
    allowed={'phase','nonce','started','expires','finished','elapsedSeconds','message'}
    if set(report)-allowed or not isinstance(report.get('message'),str) or len(report['message'])>350:
        raise ValueError('Invalid diagnostic report fields')
    for name in ('started','expires','finished','elapsedSeconds'):
        if name in report and (type(report[name]) not in (int,float) or not 0<=report[name]<1e12):
            raise ValueError('Invalid diagnostic result time')
    value=dict(report,reportedAt=time.time(),source='authenticated-car-report')
    atomic(path,json.dumps(value).encode())


def main():
    import argparse
    import sys
    parser=argparse.ArgumentParser(description='Show the automatic encrypted radio test result; never transmits directly.')
    parser.add_argument('--state',type=Path,default=Path.home()/'.local/state/drive-console/software/radio-test-result.json')
    parser.add_argument('--wait',action='store_true',help='Wait for the role service result, printing only changes')
    parser.add_argument('--timeout',type=int,default=10800)
    args=parser.parse_args()
    if not 1<=args.timeout<=86400:parser.error('timeout must be between 1 and 86400 seconds')
    end=time.monotonic()+args.timeout;previous=None
    while True:
        result=json.loads(args.state.read_text()) if args.state.exists() else {
            'phase':'pending',
            'message':'Waiting for configured Tiny radio, pinned encryption keys and the home ChirpStack application bridge. The test has not run.'}
        text=json.dumps(result,sort_keys=True)
        if text!=previous:print(text,flush=True);previous=text
        if result.get('phase')=='passed':return
        if result.get('phase')=='timed-out':sys.exit(2)
        if not args.wait:sys.exit(1)
        if time.monotonic()>=end:
            print('No completed radio test result before the watcher deadline.',flush=True)
            sys.exit(2)
        time.sleep(min(5,max(0,end-time.monotonic())))


if __name__=='__main__':main()
