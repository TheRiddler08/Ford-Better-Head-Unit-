"""Car-side LoRaWAN application agent; connect its hex-line I/O to an AT/modem bridge.

Receives authenticated small application frames, persists partial transfers,
applies configuration revisions atomically and emits authenticated ACK/missing
requests as outgoing frame lines. Never sends ECU commands.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

from control.config_core import apply_changes, read_config, write_config
from control.lorawan_protocol import Assembler, split_message

ROOT = Path(os.getenv("CAR_RADIO_STATE",str(Path.home()/".local/state/car-dashboard/radio")))
CONFIG = Path(os.getenv("CAR_UI_CONFIG",str(Path.home()/".config/car-dashboard/ui-config.json")))
SECRET_HEX = os.getenv("CAR_LINK_KEY","")

def load_agent_state(path):
    try:
        result=json.loads(path.read_text(encoding="utf-8"))
        if isinstance(result,dict):
            return result
    except (OSError,ValueError):
        pass
    return {"last_sequence":0,"parts":[]}

def save_state(path, assembler):
    path.parent.mkdir(parents=True,exist_ok=True)
    state={"last_sequence":assembler.last_sequence,"parts":[
        {"sequence":seq,"kind":kind,"count":count,"digest":digest.hex(),
         "chunks":{str(i):v.hex() for i,v in chunks.items()}}
        for (seq,kind,count,digest),chunks in assembler.pending.items()]}
    tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state,separators=(",",":")),encoding="utf-8")
    os.chmod(tmp,0o600)
    os.replace(tmp,path)

def new_assembler(secret, state):
    agent=Assembler(secret,int(state.get("last_sequence",0)))
    for row in state.get("parts",[]):
        try:
            ident=(int(row["sequence"]),int(row["kind"]),int(row["count"]),bytes.fromhex(row["digest"]))
            if ident[0]<=agent.last_sequence:continue
            parts={int(i):bytes.fromhex(v) for i,v in row["chunks"].items()}
            if len(parts)>1024:continue
            agent.pending[ident]=parts
        except (KeyError,TypeError,ValueError):
            continue
    return agent

def apply_message(message, path=CONFIG):
    kind=message["kind"]
    payload=message["payload"]
    if kind=="configuration":
        if not isinstance(payload,dict) or set(payload)!={"revision","changes"}:
            raise ValueError("Malformed config delta")
        current=read_config(path)
        rev=payload["revision"]
        if rev!=current["revision"]+1:
            raise ValueError("Configuration dependency missing; request an earlier revision")
        changed=apply_changes(current,rev,payload["changes"])
        write_config(path,changed)
        return {"revision":rev,"applied":True,"kind":"configuration"}
    if kind in ("ui-patch","recorder-patch"):
        # Fail closed until the independent code-signature verifier and
        # code slot activation service are installed and configured.
        raise ValueError("Signed code patch deployment is not configured; no code executed")
    return {"kind":kind,"status":"received"}

def process_frame(agent, frame, state_path, config_path, secret):
    result=agent.accept(frame)
    save_state(state_path,agent)
    if result is None:
        return []
    try:
        response=apply_message(result,config_path)
    except (ValueError,TypeError,OSError) as exc:
        response={"revision":result["sequence"],"applied":False,"error":str(exc)[:140]}
    # Agent state persists the last authenticated sequence, even after a failed
    # apply; home can send a new sequence with full configuration when needed.
    outgoing=split_message("ack",max(1,result["sequence"]),response,secret)
    return outgoing

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--receive-hex",help="Single LoRaWAN application frame as hex, for integration testing")
    parser.add_argument("--report-missing",type=int,help="Request any not-yet-received frame indices")
    args=parser.parse_args()
    try:
        secret=bytes.fromhex(SECRET_HEX)
    except ValueError:
        secret=b""
    if len(secret)<32:
        raise SystemExit("Set CAR_LINK_KEY to at least 32 bytes of random hex in the agent environment")
    state_file=ROOT/"state.json"
    agent=new_assembler(secret,load_agent_state(state_file))
    if args.report_missing is not None:
        requested=agent.missing(args.report_missing)
        for frame in split_message("chunk-request",max(1,int(time.time())%(2**32)),requested or
                                   {"sequence":args.report_missing,"missing":[]},secret):
            print(frame.hex())
        return
    def handle(line):
        line=line.strip()
        if not line:return
        try:
            frames=process_frame(agent,bytes.fromhex(line),state_file,CONFIG,secret)
            for frame in frames:
                print(frame.hex(),flush=True)
        except (ValueError,TypeError,OSError) as exc:
            print(f"Rejected radio frame: {exc}",file=sys.stderr,flush=True)
    if args.receive_hex:
        handle(args.receive_hex)
    else:
        for line in sys.stdin:
            handle(line)

if __name__=="__main__":
    main()
