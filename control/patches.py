"""Small signed code deltas for UI and recorder, never the privileged updater.

Install 'python3-cryptography' on home/car hosts. A pinned Ed25519
PUBLIC key verifies patches on the car; the PRIVATE key stays at home.
"""
from __future__ import annotations
import base64
import difflib
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from control.lorawan_protocol import canonical, MAX_SOURCE_BYTES

UI = {"app/web/cockpit.html", "app/web/cockpit.css", "app/web/cockpit.js",
      "app/web/index.html", "app/web/styles.css", "app/web/app.js"}
RECORDER = {"recorder/obd_full_recorder.py"}
ALLOWED = UI | RECORDER

def digest(data):
    return hashlib.sha256(data).hexdigest()

def make_delta(path, original, modified):
    if path not in ALLOWED:
        raise ValueError("Path is not approved for radio updates")
    if not isinstance(original,str) or not isinstance(modified,str):
        raise ValueError("Code sources must be UTF-8 text")
    # Character-level edits transmit changed fragments, not the entire source.
    engine=difflib.SequenceMatcher(a=original,b=modified,autojunk=False)
    hunks=[[a0,a1,modified[b0:b1]] for tag,a0,a1,b0,b1 in engine.get_opcodes() if tag!="equal"]
    obj={"v":1,"component":"recorder" if path in RECORDER else "ui","path":path,
         "base":digest(original.encode("utf-8")),
         "target":digest(modified.encode("utf-8")),"hunks":hunks}
    if len(canonical(obj))>MAX_SOURCE_BYTES-1000:
        raise ValueError("Delta too large for LoRaWAN: use GitHub/IP connectivity")
    return obj

def apply_hunks(source,hunks):
    if not isinstance(hunks,list) or len(hunks)>1000:
        raise ValueError("Invalid change count")
    last=0
    out=[]
    for item in hunks:
        if not isinstance(item,list) or len(item)!=3:
            raise ValueError("Malformed edit")
        start,end,new=item
        if type(start) is not int or type(end) is not int or not isinstance(new,str):
            raise ValueError("Invalid edit")
        if not last<=start<=end<=len(source):
            raise ValueError("Overlapping/out-of-bounds patch")
        out.extend((source[last:start],new))
        last=end
    out.append(source[last:])
    return "".join(out)

def sign_delta(delta, private_pem):
    from cryptography.hazmat.primitives import serialization
    key=serialization.load_pem_private_key(Path(private_pem).read_bytes(),password=None)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError("Signing key must be Ed25519")
    if "signature" in delta:raise ValueError("Already signed")
    doc=dict(delta)
    doc["signature"]=base64.b64encode(key.sign(canonical(delta))).decode("ascii")
    if len(canonical(doc))>MAX_SOURCE_BYTES:
        raise ValueError("Signed patch too large for radio delivery")
    return doc

def verify_delta(doc, public_pem):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(doc,dict) or set(doc)!={"v","component","path","base","target","hunks","signature"}:
        raise ValueError("Invalid signed patch")
    if doc["v"]!=1 or doc["path"] not in ALLOWED or doc["component"]!=("recorder" if doc["path"] in RECORDER else "ui"):
        raise ValueError("Invalid code update target")
    public=serialization.load_pem_public_key(Path(public_pem).read_bytes())
    if not isinstance(public,Ed25519PublicKey):
        raise ValueError("Trusted public key must be Ed25519")
    signed={k:v for k,v in doc.items() if k!="signature"}
    try:
        signature=base64.b64decode(doc["signature"],validate=True)
        public.verify(signature,canonical(signed))
    except (ValueError,TypeError) as exc:
        raise ValueError("Invalid package signature encoding") from exc
    return signed

def install_patch(doc, repo, overrides, public_pem):
    """Verify signature, base digest and target digest; atomic overlay install."""
    try:
        verified=verify_delta(doc,public_pem)
    except ImportError as exc:
        raise RuntimeError("Install python3-cryptography before accepting code updates") from exc
    rel=verified["path"]
    repo=Path(repo).resolve()
    overrides=Path(overrides).resolve()
    source=overrides/rel
    if not source.is_file():
        source=repo/rel
    if not source.is_file() or source.is_symlink():
        raise ValueError("Patch source is missing or symlinked")
    original=source.read_bytes()
    if digest(original)!=verified["base"]:
        raise ValueError("Base revision mismatch; request dependency/repair chunk")
    try:
        changed=apply_hunks(original.decode("utf-8"),verified["hunks"])
        updated=changed.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Patch text must be valid UTF-8") from exc
    if digest(updated)!=verified["target"]:
        raise ValueError("Patched file checksum mismatch")
    target=overrides/rel
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.is_symlink():raise ValueError("Override path is symlinked")
    # Always syntax-check code before activation. HTML/CSS hashes are still verified.
    if rel.endswith(".py"):
        compile(changed,str(target),"exec")
    if rel.endswith(".js") and subprocess.call(["which","node"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)==0:
        with tempfile.NamedTemporaryFile("wb",suffix=".js") as check:
            check.write(updated);check.flush()
            subprocess.run(["node","--check",check.name],check=True,timeout=10,stdout=subprocess.DEVNULL)
    fd,temp=tempfile.mkstemp(prefix=".radio-patch-",dir=target.parent)
    try:
        with os.fdopen(fd,"wb") as out:
            os.fchmod(out.fileno(),0o600)
            out.write(updated);out.flush();os.fsync(out.fileno())
        backup=target.with_suffix(target.suffix+".previous")
        if target.is_file():
            if backup.is_symlink():raise ValueError("Backup path is symlinked")
            backup.write_bytes(target.read_bytes())
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return {"component":verified["component"],"path":rel,"sha256":verified["target"]}
