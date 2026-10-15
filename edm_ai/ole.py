"""Minimal read-only reader for OLE2 / Compound File Binary containers (used by legacy Office and Hangul .hwp files).
Pure standard library; defensive against corrupt or hostile files (bounded sector chains, bounded sizes)."""
from __future__ import annotations

import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREESECT, ENDOFCHAIN, FATSECT, DIFSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
MAX_STREAM = 200 * 1024 * 1024


class OleError(Exception):
    pass


class Ole:
    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != MAGIC:
            raise OleError("not an OLE2 file")
        self.d = data
        ver, _, byte_order, ssh, msh = struct.unpack_from("<HHHHH", data, 0x18)
        if byte_order != 0xFFFE or ssh not in (9, 12) or msh != 6:
            raise OleError("unsupported OLE header")
        self.ss, self.ms = 1 << ssh, 1 << msh
        n_fat, first_dir, _, cutoff, first_mini, n_mini, first_dif, n_dif = struct.unpack_from("<IIIIIIII", data, 0x2C)
        self.cutoff = cutoff or 4096
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        sec = first_dif
        for _ in range(min(n_dif, 4096)):
            if sec in (FREESECT, ENDOFCHAIN):
                break
            blk = self._sector(sec)
            vals = struct.unpack("<%dI" % (self.ss // 4), blk)
            difat += list(vals[:-1])
            sec = vals[-1]
        self.fat: list[int] = []
        for s in difat[:n_fat]:
            if s in (FREESECT, ENDOFCHAIN):
                continue
            self.fat += struct.unpack("<%dI" % (self.ss // 4), self._sector(s))
        self.dirs = self._read_dirs(first_dir)
        self.mini_fat: list[int] = []
        if n_mini and first_mini not in (FREESECT, ENDOFCHAIN):
            raw = self._chain(first_mini)
            self.mini_fat = list(struct.unpack("<%dI" % (len(raw) // 4), raw[: len(raw) // 4 * 4]))
        root = self.dirs[0]
        self.mini_stream = self._chain(root["start"], root["size"]) if root["size"] else b""

    def _sector(self, n: int) -> bytes:
        off = (n + 1) * self.ss
        if off + self.ss > len(self.d) or n >= 0xFFFFFFFA:
            raise OleError("sector out of range")
        return self.d[off: off + self.ss]

    def _chain(self, start: int, size: int | None = None) -> bytes:
        out, seen, s = [], 0, start
        limit = len(self.d) // self.ss + 2
        while s not in (ENDOFCHAIN, FREESECT) and seen <= limit:
            out.append(self._sector(s))
            if s >= len(self.fat):
                raise OleError("broken FAT chain")
            s = self.fat[s]
            seen += 1
        raw = b"".join(out)
        return raw if size is None else raw[:size]

    def _read_dirs(self, first: int) -> list[dict]:
        raw = self._chain(first)
        dirs = []
        for i in range(0, len(raw) - 127, 128):
            e = raw[i: i + 128]
            nlen = struct.unpack_from("<H", e, 64)[0]
            name = e[: max(0, min(nlen, 64) - 2)].decode("utf-16-le", "replace")
            typ = e[66]
            left, right, child = struct.unpack_from("<III", e, 68)
            start = struct.unpack_from("<I", e, 116)[0]
            size = struct.unpack_from("<Q", e, 120)[0] & 0xFFFFFFFF if self.ss == 512 else struct.unpack_from("<Q", e, 120)[0]
            dirs.append({"name": name, "type": typ, "left": left, "right": right, "child": child, "start": start, "size": size})
        if not dirs:
            raise OleError("empty directory")
        return dirs

    def _walk(self, idx: int, prefix: str, out: dict, depth=0):
        if idx == 0xFFFFFFFF or idx >= len(self.dirs) or depth > 2000:
            return
        e = self.dirs[idx]
        self._walk(e["left"], prefix, out, depth + 1)
        path = prefix + e["name"]
        out[path] = e
        if e["type"] in (1, 5):
            self._walk(e["child"], path + "/", out, depth + 1)
        self._walk(e["right"], prefix, out, depth + 1)

    def entries(self) -> dict[str, dict]:
        out: dict = {}
        self._walk(self.dirs[0]["child"], "", out)
        return out

    def read(self, path: str) -> bytes:
        e = self.entries().get(path)
        if not e or e["type"] != 2:
            raise OleError(f"stream not found: {path}")
        if e["size"] > MAX_STREAM:
            raise OleError("stream too large")
        if e["size"] < self.cutoff:                               # small streams live in the mini stream
            out, s, seen = [], e["start"], 0
            while s not in (ENDOFCHAIN, FREESECT) and seen <= len(self.mini_fat) + 1:
                off = s * self.ms
                out.append(self.mini_stream[off: off + self.ms])
                if s >= len(self.mini_fat):
                    raise OleError("broken mini FAT chain")
                s = self.mini_fat[s]
                seen += 1
            return b"".join(out)[: e["size"]]
        return self._chain(e["start"], e["size"])
