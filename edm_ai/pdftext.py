"""Pure-Python text extraction for PDFs with a text layer (no poppler / pypdf needed - e.g. on a plain Windows PC).

Handles: classic and object-stream PDFs, Flate / ASCII85 / ASCIIHex filters, page tree order, Type0 (CID) and simple fonts with
ToUnicode CMaps, TJ spacing, and line breaks from text positioning. Not handled: encrypted PDFs, scanned pages (use OCR),
exotic encodings without ToUnicode (those characters are skipped). Defensive: bounded sizes, no recursion on hostile trees."""
from __future__ import annotations

import base64
import re
import zlib

MAX_STREAM = 80 * 1024 * 1024
MAX_PAGES = 5000


class PdfError(Exception):
    pass


class Name(str):
    pass


class Ref:
    __slots__ = ("n",)

    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return f"Ref({self.n})"


_WS = b" \t\r\n\x0c\x00"
_DELIM = b"()<>[]{}/%"
_NUM = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")


class Parser:
    def __init__(self, data: bytes, pos: int = 0):
        self.d, self.i, self.n = data, pos, len(data)

    def ws(self):
        d, n = self.d, self.n
        while self.i < n:
            c = d[self.i]
            if c in _WS:
                self.i += 1
            elif c == 0x25:                                   # comment
                while self.i < n and d[self.i] not in b"\r\n":
                    self.i += 1
            else:
                break

    def parse(self, depth=0):
        if depth > 60:
            raise PdfError("nesting too deep")
        self.ws()
        if self.i >= self.n:
            raise PdfError("eof")
        d, c = self.d, self.d[self.i]
        if c == 0x2F:                                         # /Name
            self.i += 1
            j = self.i
            while j < self.n and d[j] not in _WS and d[j] not in _DELIM:
                j += 1
            raw = d[self.i:j]
            self.i = j
            return Name(re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw).decode("latin-1"))
        if c == 0x28:
            return self._string()
        if d.startswith(b"<<", self.i):
            self.i += 2
            out = {}
            while True:
                self.ws()
                if d.startswith(b">>", self.i):
                    self.i += 2
                    return out
                k = self.parse(depth + 1)
                if not isinstance(k, Name):
                    raise PdfError("bad dict key")
                out[str(k)] = self.parse(depth + 1)
        if c == 0x3C:                                         # <hex>
            j = d.index(b">", self.i)
            h = re.sub(rb"\s", b"", d[self.i + 1:j])
            self.i = j + 1
            return bytes.fromhex((h + (b"0" if len(h) % 2 else b"")).decode("ascii", "ignore"))
        if c == 0x5B:
            self.i += 1
            out = []
            while True:
                self.ws()
                if self.i >= self.n:
                    raise PdfError("eof in array")
                if d[self.i] == 0x5D:
                    self.i += 1
                    return out
                out.append(self.parse(depth + 1))
        m = _NUM.match(d, self.i)
        if m:
            end = m.end()
            txt = m.group()
            # indirect reference:  n g R
            m2 = re.compile(rb"\s+(\d+)\s+R(?![A-Za-z])").match(d, end)
            if m2 and b"." not in txt and txt[:1] not in b"+-":
                self.i = m2.end()
                return Ref(int(txt))
            self.i = end
            return float(txt) if b"." in txt else int(txt)
        j = self.i
        while j < self.n and d[j] not in _WS and d[j] not in _DELIM:
            j += 1
        word = d[self.i:j]
        if j == self.i:
            self.i += 1
            return None
        self.i = j
        return {b"true": True, b"false": False, b"null": None}.get(word, ("kw", word.decode("latin-1")))

    def _string(self) -> bytes:
        d, i, out, depth = self.d, self.i + 1, bytearray(), 1
        while i < self.n and depth:
            c = d[i]
            if c == 0x5C:
                i += 1
                e = d[i:i + 1]
                if e in b"nrtbf":
                    out.append({b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12}[e])
                elif e.isdigit():
                    j = i
                    while j < i + 3 and d[j:j + 1].isdigit():
                        j += 1
                    out.append(int(d[i:j], 8) & 0xFF)
                    i = j - 1
                elif e in b"\r\n":
                    if e == b"\r" and d[i + 1:i + 2] == b"\n":
                        i += 1
                else:
                    out += e
            elif c == 0x28:
                depth += 1
                out.append(c)
            elif c == 0x29:
                depth -= 1
                if depth:
                    out.append(c)
            else:
                out.append(c)
            i += 1
        self.i = i
        return bytes(out)


def _decode_stream(raw: bytes, d: dict) -> bytes:
    filt = d.get("Filter")
    filters = filt if isinstance(filt, list) else ([filt] if filt else [])
    for f in filters:
        f = str(f)
        if f in ("FlateDecode", "Fl"):
            try:
                raw = zlib.decompressobj().decompress(raw, MAX_STREAM)
            except zlib.error:
                try:
                    raw = zlib.decompressobj(-15).decompress(raw[2:], MAX_STREAM)
                except zlib.error:
                    return b""
        elif f in ("ASCIIHexDecode", "AHx"):
            raw = bytes.fromhex(re.sub(rb"[\s>]", b"", raw).decode("ascii", "ignore"))
        elif f in ("ASCII85Decode", "A85"):
            raw = base64.a85decode(raw.strip(), adobe=raw.strip().startswith(b"<~"))
        else:
            return b""                                       # DCT/JPX/LZW/CCITT: no text in those
    return raw


class Doc:
    def __init__(self, data: bytes):
        if not data.startswith(b"%PDF-"):
            if b"%PDF-" not in data[:1024]:
                raise PdfError("not a PDF")
        self.d = data
        self.objs: dict[int, object] = {}
        self.streams: dict[int, tuple[dict, bytes]] = {}
        self._scan()
        if any(isinstance(v, dict) and "Encrypt" in v for v in self.objs.values()) or re.search(rb"/Encrypt\s", data[-4096:]):
            raise PdfError("encrypted PDF")
        self._objstms()

    def _scan(self):
        d = self.d
        for m in re.finditer(rb"(?<![0-9])(\d+)\s+(\d+)\s+obj\b", d):
            num = int(m.group(1))
            p = Parser(d, m.end())
            try:
                val = p.parse()
            except (PdfError, ValueError, IndexError):
                continue
            self.objs[num] = val
            if isinstance(val, dict):
                p.ws()
                if d.startswith(b"stream", p.i):
                    i = p.i + 6
                    if d[i:i + 2] == b"\r\n":
                        i += 2
                    elif d[i:i + 1] in b"\r\n":
                        i += 1
                    ln = val.get("Length")
                    end = None
                    if isinstance(ln, int) and d[i + ln:i + ln + 12].lstrip().startswith(b"endstream"):
                        end = i + ln
                    if end is None:
                        end = d.find(b"endstream", i)
                        if end < 0:
                            continue
                        while end > i and d[end - 1:end] in b"\r\n":
                            end -= 1
                    self.streams[num] = (val, d[i:end])

    def get(self, v, depth=0):
        while isinstance(v, Ref) and depth < 20:
            v = self.objs.get(v.n)
            depth += 1
        return v

    def stream(self, v) -> bytes:
        if isinstance(v, Ref) and v.n in self.streams:
            dic, raw = self.streams[v.n]
            return _decode_stream(raw, {k: self.get(x) for k, x in dic.items()})
        return b""

    def _objstms(self):
        for num, (dic, raw) in list(self.streams.items()):
            if str(dic.get("Type")) != "ObjStm":
                continue
            data = _decode_stream(raw, {k: self.get(x) for k, x in dic.items()})
            n, first = self.get(dic.get("N")), self.get(dic.get("First"))
            if not isinstance(n, int) or not isinstance(first, int):
                continue
            head = re.findall(rb"\d+", data[:first])
            for k in range(min(n, len(head) // 2)):
                onum, off = int(head[2 * k]), int(head[2 * k + 1])
                try:
                    self.objs.setdefault(onum, Parser(data, first + off).parse())
                except (PdfError, ValueError, IndexError):
                    continue

    # ---------- page tree ----------
    def pages(self):
        root = next((v for v in self.objs.values() if isinstance(v, dict) and str(v.get("Type")) == "Catalog"), None)
        order = []
        if root and root.get("Pages") is not None:
            seen: set = set()

            def walk(node, inherited, depth=0):
                if depth > 40 or len(order) > MAX_PAGES:
                    return
                key = id(node)
                if key in seen:
                    return
                seen.add(key)
                dic = self.get(node)
                if not isinstance(dic, dict):
                    return
                inh = dict(inherited)
                if "Resources" in dic:
                    inh["Resources"] = dic["Resources"]
                kids = self.get(dic.get("Kids"))
                if isinstance(kids, list) and str(dic.get("Type")) != "Page":
                    for k in kids:
                        walk(k, inh, depth + 1)
                else:
                    order.append((dic, inh))
            walk(root["Pages"], {})
        if not order:                                           # damaged tree: fall back to any page objects in file order
            order = [(v, {}) for _, v in sorted(self.objs.items()) if isinstance(v, dict) and str(v.get("Type")) == "Page"]
        return order[:MAX_PAGES]


# ---------- fonts / CMaps ----------
_BFCHAR = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
_BFRANGE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)


def _u(h: bytes) -> str:
    b = bytes.fromhex(h.decode())
    if len(b) % 2:
        b = b"\x00" + b
    return b.decode("utf-16-be", "replace")


def parse_cmap(data: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for a, b in _BFCHAR.findall(blk):
            out[int(a, 16)] = _u(b)
    for blk in _BFRANGE.findall(data):
        for m in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", blk):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            if hi < lo or hi - lo > 65535:
                continue
            tgt = m.group(3)
            if tgt.startswith(b"["):
                for k, h in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", tgt)[: hi - lo + 1]):
                    out[lo + k] = _u(h)
            else:
                base = _u(tgt[1:-1])
                if base:
                    for k in range(hi - lo + 1):
                        out[lo + k] = base[:-1] + chr(ord(base[-1]) + k)
    return out


class Font:
    def __init__(self, doc: Doc, ref):
        f = doc.get(ref) if ref is not None else {}
        f = f if isinstance(f, dict) else {}
        self.two = str(doc.get(f.get("Subtype"))) == "Type0"
        tu = f.get("ToUnicode")
        self.cmap = parse_cmap(doc.stream(tu)) if tu is not None else {}
        enc = doc.get(f.get("Encoding"))
        self.latin = not self.two and not self.cmap

    def decode(self, s: bytes) -> str:
        if self.two:
            out = []
            for i in range(0, len(s) - 1, 2):
                code = (s[i] << 8) | s[i + 1]
                out.append(self.cmap.get(code, ""))
            return "".join(out)
        if self.cmap:
            return "".join(self.cmap.get(b, chr(b) if 32 <= b < 127 else "") for b in s)
        return s.decode("cp1252", "replace") if self.latin else ""


# ---------- content stream ----------
def page_text(doc: Doc, page: dict, inherited: dict, fonts_cache: dict) -> str:
    res = doc.get(page.get("Resources", inherited.get("Resources")))
    res = res if isinstance(res, dict) else {}
    fdict = doc.get(res.get("Font"))
    fdict = fdict if isinstance(fdict, dict) else {}

    def font(name):
        ref = fdict.get(name)
        key = ref.n if isinstance(ref, Ref) else id(ref)
        if key not in fonts_cache:
            fonts_cache[key] = Font(doc, ref)
        return fonts_cache[key]

    cont = page.get("Contents")
    cont = doc.get(cont) if not isinstance(cont, Ref) or cont.n not in doc.streams else cont
    refs = cont if isinstance(cont, list) else [page.get("Contents")]
    data = b"\n".join(doc.stream(r) if isinstance(r, Ref) else b"" for r in refs)
    p = Parser(data)
    stack, out, cur, line = [], [], None, []
    size, ty, tx, last_y, last_x = 10.0, 0.0, 0.0, None, None
    ctm_stack: list = []

    def newline():
        nonlocal last_x
        if line:
            out.append("".join(line))
            line.clear()
        last_x = None

    def show(txt: str):
        """Start a new line when the baseline moved by a meaningful fraction of the font size; join runs on one baseline."""
        nonlocal last_y, last_x
        if last_y is not None and abs(ty - last_y) > max(1.0, size * 0.45):
            newline()
        elif line and last_x is not None and tx - last_x > size * 3.0 and not line[-1].endswith(" ") and not txt.startswith(" "):
            line.append(" ")
        last_y = ty
        line.append(txt)
        last_x = tx

    guard = 0
    while p.i < p.n and guard < 3_000_000:
        guard += 1
        try:
            tok = p.parse()
        except (PdfError, ValueError, IndexError):
            p.i += 1
            continue
        if not (isinstance(tok, tuple) and tok and tok[0] == "kw"):
            stack.append(tok)
            if len(stack) > 200:
                stack = stack[-100:]
            continue
        op = tok[1]
        num = lambda k: stack[k] if len(stack) >= -k and isinstance(stack[k], (int, float)) else 0.0   # noqa: E731
        if op == "BT":
            tx = ty = 0.0
        elif op == "Tf" and len(stack) >= 2 and isinstance(stack[-2], Name):
            cur = font(str(stack[-2]))
            size = abs(num(-1)) or size
        elif op in ("Tj", "'", '"') and stack and isinstance(stack[-1], bytes):
            if op != "Tj":
                ty -= size * 1.2
            show(cur.decode(stack[-1]) if cur else stack[-1].decode("latin-1"))
        elif op == "TJ" and stack and isinstance(stack[-1], list):
            for it in stack[-1]:
                if isinstance(it, bytes):
                    show(cur.decode(it) if cur else it.decode("latin-1"))
                elif isinstance(it, (int, float)) and it < -250 and line and not line[-1].endswith(" "):
                    line.append(" ")
        elif op in ("Td", "TD") and len(stack) >= 2:
            tx += num(-2)
            ty += num(-1)
        elif op == "T*":
            ty -= size * 1.2
        elif op == "Tm" and len(stack) >= 6:
            tx, ty = num(-2), num(-1)
        stack = []
    newline()
    text = "\n".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(data: bytes) -> str:
    if len(data) > 200 * 1024 * 1024:
        raise PdfError("PDF too large")
    doc = Doc(data)
    cache: dict = {}
    parts = []
    for page, inh in doc.pages():
        t = page_text(doc, page, inh, cache)
        if t:
            parts.append(t)
    return "\n\n".join(parts)
