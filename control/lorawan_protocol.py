"""Small authenticated application frames for a real LoRaWAN network server.

Frame format: version(1),kind(1),sequence(4),index(2),count(2),
             full-message digest prefix(8), per-frame HMAC(8), data(<=20).
Default frame size <=46 bytes, suitable for many (not every) DR/region.
Network server must enforce regional payload/airtime/downlink constraints.
This is NOT a radio driver nor the LoRaWAN MAC-layer frame.
"""
import hashlib
import hmac
import json
import struct
import zlib

VERSION = 1
HEADER = struct.Struct(">BBIHH8s8s")
AUTH_HEADER = struct.Struct(">BBIHH8s")
CHUNK_BYTES = 20
MAX_APP_BYTES = 46
MAX_FRAGMENTS = 1024
MAX_SOURCE_BYTES = 16384
KINDS = {"configuration":1, "ui-patch":2, "recorder-patch":3,
         "chunk-request":4, "ack":5, "status":6}
REVERSE = {v:k for k,v in KINDS.items()}

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

def split_message(kind, sequence, payload, secret, chunk_size=CHUNK_BYTES):
    if kind not in KINDS or type(sequence) is not int or not 0 < sequence < 2**32:
        raise ValueError("Bad kind or sequence")
    if not isinstance(payload, (dict,list)) or not isinstance(secret,bytes) or len(secret) < 32:
        raise ValueError("Invalid message or key")
    if type(chunk_size) is not int or not 4 <= chunk_size <= CHUNK_BYTES:
        raise ValueError("Frame size must respect smallest configured regional data rate")
    raw = canonical(payload)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Update payload too large for LoRaWAN")
    zipped = zlib.compress(raw, 9)
    body = (b"z"+zipped) if len(zipped)+1 < len(raw) else (b"j"+raw)
    digest = hashlib.sha256(body).digest()[:8]
    count = max(1,(len(body)+chunk_size-1)//chunk_size)
    if count > MAX_FRAGMENTS:
        raise ValueError("Too many fragments")
    result=[]
    for index in range(count):
        data=body[index*chunk_size:(index+1)*chunk_size]
        header=AUTH_HEADER.pack(VERSION,KINDS[kind],sequence,index,count,digest)
        tag=hmac.new(secret,header+data,hashlib.sha256).digest()[:8]
        frame=header+tag+data
        if len(frame) > MAX_APP_BYTES:
            raise AssertionError("Internal framing budget exceeded")
        result.append(frame)
    return result

class Assembler:
    def __init__(self, secret, last_sequence=0):
        if not isinstance(secret,bytes) or len(secret)<32:raise ValueError("Key must have >=32 bytes")
        self.secret=secret
        self.last_sequence=last_sequence
        self.pending={}

    def accept(self, frame):
        if not isinstance(frame,bytes) or len(frame) < HEADER.size or len(frame)>MAX_APP_BYTES:
            raise ValueError("Invalid frame size")
        version,kind,seq,index,count,digest,tag=HEADER.unpack(frame[:HEADER.size])
        data=frame[HEADER.size:]
        if version!=VERSION or kind not in REVERSE or seq<=self.last_sequence or not 0<=index<count<=MAX_FRAGMENTS:
            raise ValueError("Unsupported, replayed, or malformed fragment")
        expected=hmac.new(self.secret,frame[:AUTH_HEADER.size]+data,hashlib.sha256).digest()[:8]
        if not hmac.compare_digest(tag,expected):
            raise ValueError("Frame authentication failed")
        ident=(seq,kind,count,digest)
        if ident not in self.pending and len(self.pending)>=4:
            self.pending.pop(next(iter(self.pending)))
        parts=self.pending.setdefault(ident,{})
        if index in parts and parts[index]!=data:
            raise ValueError("Conflicting fragment")
        parts[index]=data
        if len(parts)<count:
            return None
        body=b"".join(parts[i] for i in range(count))
        del self.pending[ident]
        if len(body)>MAX_SOURCE_BYTES+1 or hashlib.sha256(body).digest()[:8]!=digest:
            raise ValueError("Corrupt reassembled message")
        if body[:1]==b"z":
            dec=zlib.decompressobj()
            raw=dec.decompress(body[1:],MAX_SOURCE_BYTES+1)
            if dec.unconsumed_tail or not dec.eof:
                raise ValueError("Compressed message exceeds size limit or is incomplete")
            raw+=dec.flush()
        elif body[:1]==b"j":
            raw=body[1:]
        else:
            raise ValueError("Unknown encoding")
        if len(raw)>MAX_SOURCE_BYTES:
            raise ValueError("Message too large")
        payload=json.loads(raw)
        if not isinstance(payload,(dict,list)):
            raise ValueError("Unsupported payload shape")
        self.last_sequence=seq
        return {"kind":REVERSE[kind],"sequence":seq,"payload":payload}

    def missing(self,sequence):
        for (seq,kind,count,digest),parts in self.pending.items():
            if seq==sequence:
                return {"sequence":seq,"kind":"chunk-request",
                        "missing":[i for i in range(count) if i not in parts]}
        return None
