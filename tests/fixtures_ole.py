"""Builds OLE2 / HWP test files (so the readers are tested against bytes we control, including the mini-stream path)."""
import struct
import zlib

SS, MS = 512, 64
FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD


def build_ole(streams: dict) -> bytes:
    """streams: {"FileHeader": bytes, "BodyText/Section0": bytes}. Streams < 4096 bytes go to the mini stream."""
    names = []                                                   # directory entries: (path, type, data)
    storages = sorted({p.rsplit("/", 1)[0] for p in streams if "/" in p})
    for st in storages:
        names.append((st, 1, b""))
    for p, d in streams.items():
        names.append((p, 2, d))
    mini, mini_fat, big = bytearray(), [], []
    placed = {}
    for p, t, d in names:
        if t == 2 and len(d) < 4096:
            start = len(mini) // MS
            n = max(1, -(-len(d) // MS))
            mini += d + b"\x00" * (n * MS - len(d))
            for i in range(n):
                mini_fat.append(start + i + 1 if i < n - 1 else END)
            placed[p] = (start, len(d))
        elif t == 2:
            big.append((p, d))
    sectors: list[bytes] = []
    fat: list[int] = []

    def add_chain(data: bytes) -> int:
        n = max(1, -(-len(data) // SS))
        first = len(sectors)
        for i in range(n):
            sectors.append(data[i * SS:(i + 1) * SS].ljust(SS, b"\x00"))
            fat.append(first + i + 1 if i < n - 1 else END)
        return first

    mini_stream_start = add_chain(bytes(mini)) if mini else END
    mini_fat_bytes = b"".join(struct.pack("<I", x) for x in mini_fat)
    mini_fat_start = add_chain(mini_fat_bytes) if mini_fat else END
    for p, d in big:
        placed[p] = (add_chain(d), len(d))

    entries = [("Root Entry", 5, END if not mini else mini_stream_start, len(mini))]
    dirents = []
    for i, (p, t, d) in enumerate(names):
        start, size = placed.get(p, (END, 0))
        dirents.append((p.rsplit("/", 1)[-1], t, start, size, p))
    idx = {p: i + 1 for i, (_, _, _, _, p) in enumerate(dirents)}
    raw = bytearray()

    def ent(name, typ, start, size, left=FREE, right=FREE, child=FREE):
        e = bytearray(128)
        nm = name.encode("utf-16-le")
        e[:len(nm)] = nm
        struct.pack_into("<H", e, 64, len(nm) + 2)
        e[66], e[67] = typ, 1
        struct.pack_into("<III", e, 68, left, right, child)
        struct.pack_into("<I", e, 116, start)
        struct.pack_into("<Q", e, 120, size)
        return bytes(e)

    top = [d for d in dirents if "/" not in d[4]]
    # chain siblings with right pointers; root.child -> first top entry; storage.child -> first member
    def sib(group):
        return {d[4]: (idx[group[i + 1][4]] if i + 1 < len(group) else FREE) for i, d in enumerate(group)}
    top_r = sib(top)
    root_child = idx[top[0][4]] if top else FREE
    raw += ent("Root Entry", 5, entries[0][2], len(mini), child=root_child)
    for name, typ, start, size, p in dirents:
        if typ == 1:
            members = [d for d in dirents if d[4].startswith(p + "/") and d[4].count("/") == p.count("/") + 1]
            r = sib(members)
            child = idx[members[0][4]] if members else FREE
            raw += ent(name, 1, END, 0, right=top_r.get(p, FREE), child=child)
            for m in members:
                pass
        else:
            parent = p.rsplit("/", 1)[0] if "/" in p else None
            if parent:
                members = [d for d in dirents if d[4].startswith(parent + "/") and d[4].count("/") == parent.count("/") + 1]
                right = sib(members)[p]
            else:
                right = top_r[p]
            raw += ent(name, 2, start, size, right=right)
    dir_start = add_chain(bytes(raw))
    n_fat = 1
    while True:                                                  # FAT sectors describe themselves
        total = len(sectors) + n_fat
        if n_fat * (SS // 4) >= total:
            break
        n_fat += 1
    fat_start = len(sectors)
    full_fat = fat + [FATSECT] * n_fat
    full_fat += [FREE] * (n_fat * (SS // 4) - len(full_fat))
    fat_bytes = b"".join(struct.pack("<I", x) for x in full_fat)
    for i in range(n_fat):
        sectors.append(fat_bytes[i * SS:(i + 1) * SS])
    hdr = bytearray(512)
    hdr[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", hdr, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIII", hdr, 0x2C, n_fat, dir_start, 0, 4096, mini_fat_start if mini_fat else END, -(-len(mini_fat_bytes) // SS) if mini_fat else 0, END, 0)
    difat = [fat_start + i for i in range(n_fat)] + [FREE] * (109 - n_fat)
    struct.pack_into("<109I", hdr, 0x4C, *difat)
    return bytes(hdr) + b"".join(sectors)


def para_text_record(text: str, controls=True) -> bytes:
    body = b""
    if controls:
        body += struct.pack("<H", 2) + b"\x00" * 14             # an extended control (section/column definition) in front of the text
    body += text.encode("utf-16-le") + struct.pack("<H", 13)
    return struct.pack("<I", 0x43 | (0 << 10) | (len(body) << 20)) + body


def other_record(tag: int, payload: bytes) -> bytes:
    return struct.pack("<I", tag | (len(payload) << 20)) + payload


def build_hwp(paragraphs, compressed=True, flags_extra=0, pad_paragraphs=0) -> bytes:
    section = b"".join(other_record(0x42, b"\x00" * 22) + para_text_record(t) for t in paragraphs)
    for i in range(pad_paragraphs):
        section += para_text_record(f"채움 문단 {i} " + "가나다라마바사 " * 20, controls=False)
    body = zlib.compress(section)[2:-4] if compressed else section        # raw deflate, as HWP stores it
    header = b"HWP Document File".ljust(32, b"\x00") + struct.pack("<I", 0x05010100) + struct.pack("<I", (1 if compressed else 0) | flags_extra) + b"\x00" * 216
    return build_ole({"FileHeader": header, "BodyText/Section0": body})
