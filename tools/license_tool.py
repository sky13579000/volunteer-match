#!/usr/bin/env python3
"""Vendor-side license tool. Run on YOUR OWN machine; the private key must never be copied to a customer server or committed.

  python tools/license_tool.py keygen  vendor.key           # writes the private seed (hex) -> keep safe; prints the public key to embed
  python tools/license_tool.py sign    vendor.key  --customer "제주국제컨벤션센터" --edition standard --max-users 300 --expires 2027-12-31 --out license.json
  python tools/license_tool.py verify  license.json  <public-key-hex>
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edm_ai import ed25519  # noqa: E402
from edm_ai.license import canonical  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("keyfile")
    s = sub.add_parser("sign")
    s.add_argument("keyfile")
    s.add_argument("--customer", required=True)
    s.add_argument("--edition", default="standard")
    s.add_argument("--max-users", type=int, default=0)
    s.add_argument("--expires", required=True)
    s.add_argument("--features", default="")
    s.add_argument("--out", default="license.json")
    v = sub.add_parser("verify")
    v.add_argument("license")
    v.add_argument("pubkey")
    a = ap.parse_args()
    if a.cmd == "keygen":
        p = Path(a.keyfile)
        if p.exists():
            sys.exit("refusing to overwrite an existing key file")
        seed = os.urandom(32)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(seed.hex())
        print("public key (put in edm_ai/license.py VENDOR_PUBKEY or config license.public_key):\n" + ed25519.public_key(seed).hex())
    elif a.cmd == "sign":
        seed = bytes.fromhex(Path(a.keyfile).read_text().strip())
        from datetime import date
        payload = {"customer": a.customer, "edition": a.edition, "max_users": a.max_users, "issued": date.today().isoformat(), "expires": a.expires,
                   "features": [x for x in a.features.split(",") if x]}
        doc = {"payload": payload, "signature": base64.b64encode(ed25519.sign(seed, canonical(payload))).decode()}
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), "utf-8")
        print("written", a.out)
    else:
        doc = json.loads(Path(a.license).read_text("utf-8"))
        ok = ed25519.verify(bytes.fromhex(a.pubkey), canonical(doc["payload"]), base64.b64decode(doc["signature"]))
        print("VALID" if ok else "INVALID")
        sys.exit(0 if ok else 1)


main()
