"""Encryption of stored files at rest (stdlib only).

Construction: BLAKE2b keyed as a stream cipher (counter mode, per-file random nonce) + keyed BLAKE2b-256 MAC over header+ciphertext
(encrypt-then-MAC). Both sub-keys derive from one master key with domain separation. This is a *custom composition of standard primitives*,
not a reviewed AEAD such as AES-GCM: it has had no external cryptographic review. For regulated environments combine it with (or prefer)
volume encryption (BitLocker / LUKS) - see docs/SECURITY.md. Key loss = data loss: back the key up separately from the data."""
from __future__ import annotations

import hashlib
import hmac
import os

MAGIC = b"EDMENC1\0"
BLOCK = 64


def _keys(master: bytes) -> tuple[bytes, bytes]:
    return (hashlib.blake2b(b"enc", key=hashlib.blake2b(master, digest_size=64).digest(), digest_size=32).digest(),
            hashlib.blake2b(b"mac", key=hashlib.blake2b(master, digest_size=64).digest(), digest_size=32).digest())


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out = bytearray()
    for i in range((n + BLOCK - 1) // BLOCK):
        out += hashlib.blake2b(i.to_bytes(8, "big"), key=key, salt=nonce[:16], digest_size=BLOCK).digest()
    return bytes(out[:n])


def seal(data: bytes, master: bytes) -> bytes:
    ek, mk = _keys(master)
    nonce = os.urandom(16)
    ks = _stream(ek, nonce, len(data))
    ct = (int.from_bytes(data, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(data), "big") if data else b""
    body = MAGIC + nonce + ct
    return body + hashlib.blake2b(body, key=mk, digest_size=32).digest()


def is_sealed(blob: bytes) -> bool:
    return blob[:8] == MAGIC


def open_(blob: bytes, master: bytes) -> bytes:
    if not is_sealed(blob) or len(blob) < 8 + 16 + 32:
        raise ValueError("not an encrypted file")
    ek, mk = _keys(master)
    body, mac = blob[:-32], blob[-32:]
    if not hmac.compare_digest(hashlib.blake2b(body, key=mk, digest_size=32).digest(), mac):
        raise ValueError("authentication failed (wrong key or tampered file)")
    nonce, ct = body[8:24], body[24:]
    ks = _stream(ek, nonce, len(ct))
    return (int.from_bytes(ct, "big") ^ int.from_bytes(ks, "big")).to_bytes(len(ct), "big") if ct else b""
