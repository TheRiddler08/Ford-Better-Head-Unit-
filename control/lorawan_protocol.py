"""LoRaWAN transport-independent envelope fragmentation; not a radio driver.

Integrate with a real regional-compliant LoRaWAN network-server uplink/downlink API.
The network server owns airtime limits, MAC framing, downlink scheduling and ACKs.
"""
import base64
import hashlib
import hmac
import json

VERSION = 1
MAX_FRAGMENT = 32
MAX_PAYLOAD = 32768
MAX_FRAGMENTS = 1024
KINDS = {"configuration", "ui-patch", "recorder-patch", "chunk-request", "ack", "status"}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def split_message(kind, sequence, payload, secret, chunk_size=MAX_FRAGMENT):
    if kind not in KINDS or type(sequence) is not int or sequence < 1:
        raise ValueError("Invalid message metadata")
    if not isinstance(payload, (dict, list)) or not 8 <= chunk_size <= MAX_FRAGMENT:
        raise ValueError("Invalid payload or fragment size")
    content = canonical(payload)
    if len(content) > MAX_PAYLOAD:
        raise ValueError("Message too large for low-bandwidth transport")
    digest = hashlib.sha256(content).hexdigest()
    mac = hmac.new(secret, canonical({"v":VERSION, "kind":kind, "sequence":sequence,
                                    "sha256":digest}), hashlib.sha256).hexdigest()
    count=max(1,(len(content)+chunk_size-1)//chunk_size)
    if count > MAX_FRAGMENTS:
        raise ValueError("Too many fragments")
    return [{"v":VERSION,"k":kind,"s":sequence,"i":index,"n":count,
             "d":base64.b64encode(content[index*chunk_size:(index+1)*chunk_size]).decode(),
             "h":digest[:16], "a":mac[:24]} for index in range(count)]

class Assembler:
    def __init__(self,secret,last_sequence=0):
        self.secret=secret
        self.last_sequence=last_sequence
        self.pending={}

    def accept(self,fragment):
        if not isinstance(fragment,dict) or set(fragment) != {"v","k","s","i","n","d","h","a"}:
            raise ValueError("Malformed fragment")
        if fragment["v"] != VERSION or fragment["k"] not in KINDS:
            raise ValueError("Unsupported version or kind")
        seq,index,count=fragment["s"],fragment["i"],fragment["n"]
        if any(type(x) is not int for x in (seq,index,count)) or seq <= self.last_sequence or not 0 <= index < count <= MAX_FRAGMENTS:
            raise ValueError("Replay or invalid fragment index")
        ident=(seq,fragment["k"],fragment["h"],fragment["a"],count)
        # Bound incomplete messages: never accumulate unlimited packets in a device.
        if ident not in self.pending and len(self.pending) >= 4:
            self.pending.pop(next(iter(self.pending)))
        entry=self.pending.setdefault(ident,{})
        data=base64.b64decode(fragment["d"],validate=True)
        if len(data) > MAX_FRAGMENT:
            raise ValueError("Oversized data fragment")
        if index in entry and entry[index] != data:
            raise ValueError("Conflicting fragment")
        entry[index]=data
        if len(entry)<count:
            return None
        content=b"".join(entry[i] for i in range(count))
        del self.pending[ident]
        if len(content) > MAX_PAYLOAD:
            raise ValueError("Oversized reconstructed message")
        digest=hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(digest[:16],fragment["h"]):
            raise ValueError("Payload checksum mismatch")
        mac=hmac.new(self.secret,canonical({"v":VERSION,"kind":fragment["k"],"sequence":seq,
                                          "sha256":digest}),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac[:24],fragment["a"]):
            raise ValueError("Authentication failed")
        payload=json.loads(content)
        self.last_sequence=seq
        return {"kind":fragment["k"],"sequence":seq,"payload":payload}

    def missing(self,sequence):
        for (seq,kind,digest,mac,count),parts in self.pending.items():
            if seq==sequence:
                return {"kind":"chunk-request","sequence":seq,"missing":[i for i in range(count) if i not in parts]}
        return None
