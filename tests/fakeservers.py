"""Tiny FTP and WebDAV servers for tests (standard library only)."""
import os
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
