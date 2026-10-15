"""Tiny FTP and WebDAV servers for tests (standard library only)."""
import os
import re
import socket
import socketserver
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class FtpServer:
    """MLSD / RETR / PASV subset. Serves `root` read-only."""

    def __init__(self, root: Path, user="u", password="p"):
        self.root, self.user, self.password = Path(root), user, password
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                self.cwd, self.pasv, ok = "/", None, False

                def send(s):
                    self.wfile.write((s + "\r\n").encode())

                def real(p):
                    p = os.path.normpath("/" + (p if p.startswith("/") else self.cwd.rstrip("/") + "/" + p)).replace("\\", "/")
                    return p, (outer.root / p.lstrip("/"))
                send("220 fake ftp")
                while True:
                    line = self.rfile.readline().decode("utf-8", "replace").strip()
                    if not line:
                        return
                    cmd, _, arg = line.partition(" ")
                    cmd = cmd.upper()
                    if cmd == "USER":
                        self.u = arg
                        send("331 pw")
                    elif cmd == "PASS":
                        ok = self.u == outer.user and arg == outer.password
                        send("230 ok" if ok else "530 no")
                    elif not ok:
                        send("530 login first")
                    elif cmd in ("TYPE", "OPTS", "MODE", "STRU"):
                        send("200 ok")
                    elif cmd == "PWD":
                        send(f'257 "{self.cwd}"')
                    elif cmd == "CWD":
                        _, p = real(arg)
                        if p.is_dir():
                            self.cwd = real(arg)[0]
                            send("250 ok")
                        else:
                            send("550 no")
                    elif cmd == "PASV":
                        s = socket.socket()
                        s.bind(("127.0.0.1", 0))
                        s.listen(1)
                        self.pasv = s
                        port = s.getsockname()[1]
                        send(f"227 Entering Passive Mode (127,0,0,1,{port >> 8},{port & 255})")
                    elif cmd in ("MLSD", "RETR", "NLST"):
                        _, p = real(arg or ".")
                        if not self.pasv:
                            send("425 no pasv")
                            continue
                        if cmd == "RETR" and not p.is_file() or cmd in ("MLSD", "NLST") and not p.is_dir():
                            send("550 no such")
                            continue
                        send("150 go")
                        conn, _ = self.pasv.accept()
                        if cmd == "RETR":
                            conn.sendall(p.read_bytes())
                        else:
                            lines = []
                            for c in sorted(p.iterdir()):
                                if cmd == "MLSD":
                                    t = "dir" if c.is_dir() else "file"
                                    lines.append(f"type={t};size={c.stat().st_size};modify=20260101000000; {c.name}")
                                else:
                                    lines.append(c.name)
                            conn.sendall(("\r\n".join(lines) + "\r\n").encode())
                        conn.close()
                        self.pasv.close()
                        self.pasv = None
                        send("226 done")
                    elif cmd == "QUIT":
                        send("221 bye")
                        return
                    else:
                        send("502 not implemented")
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class DavServer:
    """PROPFIND (Depth 0/1) + GET over `root`, Basic auth."""

    def __init__(self, root: Path, user="u", password="p"):
        import base64
        self.root = Path(root)
        want = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        outer = self

        class H(BaseHTTPRequestHandler):
            def _auth(self):
                if self.headers.get("Authorization") != want:
                    self.send_response(401)
                    self.end_headers()
                    return False
                return True

            def _path(self):
                return outer.root / urllib.parse.unquote(urllib.parse.urlparse(self.path).path).lstrip("/")

            def do_PROPFIND(self):
                if not self._auth():
                    return
                p = self._path()
                if not p.exists():
                    self.send_response(404)
                    self.end_headers()
                    return
                items = [p] + (sorted(p.iterdir()) if p.is_dir() and self.headers.get("Depth", "1") != "0" else [])
                xml = '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                for it in items:
                    href = "/" + str(it.relative_to(outer.root)).replace(os.sep, "/") if it != outer.root else "/"
                    href = urllib.parse.quote(href) + ("/" if it.is_dir() and not href.endswith("/") else "")
                    xml += f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
                    xml += "<d:resourcetype><d:collection/></d:resourcetype>" if it.is_dir() else f"<d:resourcetype/><d:getcontentlength>{it.stat().st_size}</d:getcontentlength><d:getlastmodified>Thu, 01 Jan 2026 00:00:00 GMT</d:getlastmodified>"
                    xml += "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
                xml += "</d:multistatus>"
                b = xml.encode()
                self.send_response(207)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if not self._auth():
                    return
                p = self._path()
                if not p.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                b = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class ImapServer:
    """IMAP4rev1 subset over plain TCP: LOGIN, SELECT/EXAMINE, UID SEARCH (UNSEEN|ALL), UID FETCH BODY.PEEK[], UID STORE, UID COPY, EXPUNGE."""

    def __init__(self, messages, user="u", password="p"):
        self.boxes = {"INBOX": [{"uid": i + 1, "raw": raw, "flags": set()} for i, raw in enumerate(messages)], "Processed": []}
        self.user, self.password = user, password
        self.lock = threading.Lock()
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda s: self.wfile.write(s if isinstance(s, bytes) else s.encode())  # noqa: E731
                w("* OK fake imap\r\n")
                box, ok, ro = None, False, False
                while True:
                    line = self.rfile.readline().decode("utf-8", "replace").rstrip("\r\n")
                    if not line:
                        return
                    tag, _, rest = line.partition(" ")
                    cmd, _, arg = rest.partition(" ")
                    cmd = cmd.upper()
                    if cmd == "CAPABILITY":
                        w(f"* CAPABILITY IMAP4rev1\r\n{tag} OK done\r\n")
                    elif cmd == "LOGIN":
                        u, p = [x.strip('"') for x in arg.split(" ", 1)]
                        ok = u == outer.user and p == outer.password
                        w(f"{tag} OK logged in\r\n" if ok else f"{tag} NO [AUTHENTICATIONFAILED] bad credentials\r\n")
                    elif not ok:
                        w(f"{tag} BAD login first\r\n")
                    elif cmd in ("SELECT", "EXAMINE"):
                        name = arg.strip('"')
                        if name not in outer.boxes:
                            w(f"{tag} NO no such mailbox\r\n")
                            continue
                        box, ro = name, cmd == "EXAMINE"
                        w(f"* {len(outer.boxes[box])} EXISTS\r\n{tag} OK [{'READ-ONLY' if ro else 'READ-WRITE'}] selected\r\n")
                    elif cmd == "UID":
                        sub, _, a2 = arg.partition(" ")
                        sub = sub.upper()
                        with outer.lock:
                            msgs = outer.boxes[box]
                            if sub == "SEARCH":
                                crit = a2.strip().upper()
                                hit = [m["uid"] for m in msgs if crit == "ALL" or "\\Seen" not in m["flags"]]
                                w("* SEARCH " + " ".join(map(str, hit)) + f"\r\n{tag} OK done\r\n")
                            elif sub == "FETCH":
                                uid = int(a2.split()[0])
                                m = next((x for x in msgs if x["uid"] == uid), None)
                                if not m:
                                    w(f"{tag} OK done\r\n")
                                    continue
                                w(f"* {msgs.index(m) + 1} FETCH (UID {uid} BODY[] {{{len(m['raw'])}}}\r\n")
                                w(m["raw"])
                                w(f")\r\n{tag} OK done\r\n")
                            elif sub == "STORE":
                                if ro:
                                    w(f"{tag} NO read-only\r\n")
                                    continue
                                uid = int(a2.split()[0])
                                flags = set(re.findall(r"\\\w+", a2))
                                for m in msgs:
                                    if m["uid"] == uid:
                                        m["flags"] |= flags
                                w(f"{tag} OK done\r\n")
                            elif sub == "COPY":
                                uid, dest = a2.split()[0], a2.split(" ", 1)[1].strip('"')
                                for m in msgs:
                                    if m["uid"] == int(uid) and dest in outer.boxes:
                                        outer.boxes[dest].append({"uid": len(outer.boxes[dest]) + 1, "raw": m["raw"], "flags": set()})
                                w(f"{tag} OK copied\r\n")
                            else:
                                w(f"{tag} BAD\r\n")
                    elif cmd == "EXPUNGE":
                        with outer.lock:
                            outer.boxes[box][:] = [m for m in outer.boxes[box] if "\\Deleted" not in m["flags"]]
                        w(f"{tag} OK done\r\n")
                    elif cmd == "CLOSE" or cmd == "NOOP":
                        w(f"{tag} OK done\r\n")
                    elif cmd == "LOGOUT":
                        w(f"* BYE\r\n{tag} OK bye\r\n")
                        return
                    else:
                        w(f"{tag} BAD unknown\r\n")
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
