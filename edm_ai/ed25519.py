"""Ed25519 (RFC 8032) in pure Python - verification for license files, signing for the vendor tool.
Straightforward big-integer reference implementation: not constant-time, so keep SIGNING on the vendor's own machine (never on a server)."""
from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
I = pow(2, (P - 1) // 4, P)
B = None


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(D * y * y + 1)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P:
        x = x * I % P
    if x % 2:
        x = P - x
    return x


def _add(a, b):
    x1, y1 = a
    x2, y2 = b
    t = D * x1 * x2 * y1 * y2
    return ((x1 * y2 + x2 * y1) * _inv(1 + t) % P, (y1 * y2 + x1 * x2) * _inv(1 - t) % P)


def _mul(pt, e: int):
    r = (0, 1)
    while e:
        if e & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        e >>= 1
    return r


_By = 4 * _inv(5) % P
B = (_xrecover(_By), _By)


def _enc(pt) -> bytes:
    x, y = pt
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _dec(s: bytes):
    if len(s) != 32:
        raise ValueError("bad point")
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    if y >= P:
        raise ValueError("bad point")
    x = _xrecover(y)
    if x & 1 != sign:
        x = P - x
    if (-x * x + y * y - 1 - D * x * x * y * y) % P:
        raise ValueError("point not on curve")
    return (x, y)


def _h(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _clamp(h: bytes) -> int:
    a = int.from_bytes(h[:32], "little")
    return (a & ((1 << 254) - 8)) | (1 << 254)


def public_key(seed: bytes) -> bytes:
    return _enc(_mul(B, _clamp(hashlib.sha512(seed).digest())))


def sign(seed: bytes, msg: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = _clamp(h)
    pk = _enc(_mul(B, a))
    r = _h(h[32:] + msg) % L
    R = _enc(_mul(B, r))
    s = (r + _h(R + pk + msg) * a) % L
    return R + s.to_bytes(32, "little")


def verify(pk: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if len(sig) != 64 or len(pk) != 32:
            return False
        R, A = _dec(sig[:32]), _dec(pk)
        s = int.from_bytes(sig[32:], "little")
        if s >= L:
            return False
        k = _h(sig[:32] + pk + msg)
        return _mul(B, s) == _add(R, _mul(A, k))
    except ValueError:
        return False
