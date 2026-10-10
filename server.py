#!/usr/bin/env python3
"""PhoneBridge: a tiny file + clipboard server for a-Shell on iPhone.

Run it in a-Shell, open the printed URL in a browser on another device on the
same wifi, and paste in the printed token.

Python 3 standard library only.
"""

import argparse
import http.server
import json
import mimetypes
import os
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

NAME = "phonebridge"
MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB per request
CHUNK = 64 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"

CONFIG = {"root": None, "token": None, "ip": "127.0.0.1", "port": 8787}
_name_lock = threading.Lock()


def lan_ip():
    """Find the IP of the interface that would route to the internet.

    connect() on a UDP socket only picks a route; no packet is sent.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def safe_path(rel):
    """Map a client path onto the served root, refusing anything outside it."""
    root = CONFIG["root"]
    rel = (rel or "").replace("\\", "/").strip("/")
    target = os.path.realpath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise ApiError(403, "path is outside the served folder")
    return target


def rel_of(abs_path):
    rel = os.path.relpath(abs_path, CONFIG["root"])
    return "" if rel == "." else rel.replace(os.sep, "/")


def unique_path(folder, filename):
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(folder, filename)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, "%s (%d)%s" % (base, n, ext))
        n += 1
    return candidate


def clean_filename(raw):
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    name = name.replace("\x00", "")
    if name in ("", ".", ".."):
        name = "upload-%d" % int(time.time())
    return name


def run_clip(cmd, data=None):
    """Run pbcopy/pbpaste. Returns stdout text."""
    try:
        proc = subprocess.run(
            [cmd],
            input=data.encode("utf-8") if data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except FileNotFoundError:
        raise ApiError(501, "%s is not available on this device" % cmd)
    except (OSError, subprocess.SubprocessError) as e:
        raise ApiError(501, "could not run %s: %s" % (cmd, e))
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip() or "exit code %d" % proc.returncode
        raise ApiError(500, "%s failed: %s" % (cmd, msg))
    return proc.stdout.decode("utf-8", "replace")


def parse_header_params(value):
    """'form-data; name="file"; filename="a.txt"' -> ('form-data', {...})"""
    parts = []
    cur, quoted, i = "", False, 0
    while i < len(value):
        ch = value[i]
        if ch == '"':
            quoted = not quoted
        elif ch == "\\" and quoted and i + 1 < len(value):
            i += 1
            cur += value[i]
            i += 1
            continue
        elif ch == ";" and not quoted:
            parts.append(cur)
            cur = ""
            i += 1
            continue
        cur += ch
        i += 1
    parts.append(cur)
    main = parts[0].strip().lower()
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                v = v[1:-1]
            params[k.strip().lower()] = v
    if "filename*" in params:  # RFC 5987: utf-8''name
        v = params["filename*"]
        if "''" in v:
            v = v.split("''", 1)[1]
        params["filename"] = urllib.parse.unquote(v)
    return main, params


class MultipartReader:
    """Streams a multipart/form-data body without holding it all in memory.

    (The cgi module was removed in Python 3.13, so this parses by hand.)
    """

    def __init__(self, rfile, boundary, length):
        self.rfile = rfile
        self.remaining = length
        self.buf = b""
        self.delim = b"\r\n--" + boundary

    def _fill(self, want=CHUNK):
        if self.remaining <= 0:
            return False
        data = self.rfile.read(min(want, self.remaining))
        if not data:
            self.remaining = 0
            return False
        self.remaining -= len(data)
        self.buf += data
        return True

    def _read_line(self, limit=16384):
        while b"\r\n" not in self.buf:
            if len(self.buf) > limit or not self._fill():
                raise ApiError(400, "malformed multipart body")
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def parts(self):
        # The body starts with "--boundary"; prepend CRLF so every delimiter looks alike.
        self.buf = b"\r\n"
        while len(self.buf) < len(self.delim) + 2 and self._fill():
            pass
        if not self.buf.startswith(self.delim):
            raise ApiError(400, "multipart body does not start with the boundary")
        self.buf = self.buf[len(self.delim):]
        while True:
            while len(self.buf) < 2 and self._fill():
                pass
            if self.buf.startswith(b"--"):
                return  # closing delimiter
            self._read_line()  # rest of the delimiter line
            headers = {}
            while True:
                line = self._read_line()
                if not line:
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower().decode("latin-1")] = v.strip().decode("utf-8", "replace")
            yield headers, self._body_chunks()

    def _body_chunks(self):
        dlen = len(self.delim)
        while True:
            idx = self.buf.find(self.delim)
            if idx != -1:
                if idx:
                    yield self.buf[:idx]
                self.buf = self.buf[idx + dlen:]
                return
            # keep a tail in case the delimiter straddles reads
            safe = len(self.buf) - dlen
            if safe > 0:
                yield self.buf[:safe]
                self.buf = self.buf[safe:]
            if not self._fill():
                raise ApiError(400, "multipart body ended early")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PhoneBridge/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---- helpers -------------------------------------------------------
    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json(status, {"ok": False, "error": message})

    def drain_body(self):
        # Read (and drop) an unread request body so keep-alive stays in sync.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_UPLOAD:
            self.close_connection = True
            return
        while n > 0:
            chunk = self.rfile.read(min(CHUNK, n))
            if not chunk:
                break
            n -= len(chunk)

    def parsed(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        return urllib.parse.unquote(u.path), {k: v[0] for k, v in q.items()}

    def authed(self, q):
        given = q.get("token", "")
        return bool(given) and secrets.compare_digest(given, CONFIG["token"])

    def read_json_body(self, limit=10 * 1024 * 1024):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad Content-Length")
        if n > limit:
            self.close_connection = True
            raise ApiError(413, "body too large")
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "body must be JSON")

    # ---- dispatch ------------------------------------------------------
    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        path, q = self.parsed()
        try:
            if not path.startswith("/api/"):
                if method not in ("GET",):
                    self.drain_body()
                    raise ApiError(405, "method not allowed")
                return self.serve_static(path)
            if not self.authed(q):
                self.drain_body()
                raise ApiError(401, "missing or wrong token")
            route = (method, path.rstrip("/"))
            handler = {
                ("GET", "/api/ping"): self.api_ping,
                ("GET", "/api/ip"): self.api_ip,
                ("GET", "/api/files"): self.api_files,
                ("GET", "/api/file"): self.api_file_get,
                ("DELETE", "/api/file"): self.api_file_delete,
                ("POST", "/api/upload"): self.api_upload,
                ("GET", "/api/clipboard"): self.api_clip_get,
                ("POST", "/api/clipboard"): self.api_clip_set,
            }.get(route)
            if handler is None:
                self.drain_body()
                raise ApiError(404, "no such endpoint")
            handler(q)
        except ApiError as e:
            self.send_error_json(e.status, e.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # keep the server alive whatever happens
            self.log_message("error: %r", e)
            try:
                self.send_error_json(500, "server error: %s" % e)
            except Exception:
                pass

    # ---- static --------------------------------------------------------
    def serve_static(self, path):
        rel = path.lstrip("/") or "index.html"
        target = os.path.realpath(os.path.join(STATIC_DIR, rel))
        static_root = str(STATIC_DIR)
        if not target.startswith(static_root + os.sep) or not os.path.isfile(target):
            raise ApiError(404, "not found")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(target, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # ---- api -----------------------------------------------------------
    def api_ping(self, q):
        self.send_json(200, {"ok": True, "name": NAME, "time": int(time.time())})

    def api_ip(self, q):
        ip, port = CONFIG["ip"], CONFIG["port"]
        self.send_json(200, {"ip": ip, "port": port, "url": "http://%s:%d" % (ip, port)})

    def api_files(self, q):
        target = safe_path(q.get("path"))
        if not os.path.isdir(target):
            raise ApiError(404, "folder not found")
        entries = []
        with os.scandir(target) as it:
            for e in it:
                try:
                    st = e.stat()
                    is_dir = e.is_dir()
                except OSError:
                    continue
                entries.append({
                    "name": e.name,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else st.st_size,
                    "mtime": int(st.st_mtime),
                })
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        self.send_json(200, {"ok": True, "path": rel_of(target), "entries": entries})

    def api_file_get(self, q):
        target = safe_path(q.get("path"))
        if not os.path.isfile(target):
            raise ApiError(404, "file not found")
        size = os.path.getsize(target)
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        name = os.path.basename(target)
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if q.get("dl") == "1":
            ascii_name = name.encode("ascii", "replace").decode().replace('"', "'")
            self.send_header(
                "Content-Disposition",
                "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, urllib.parse.quote(name)),
            )
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile, CHUNK)

    def api_file_delete(self, q):
        self.drain_body()
        target = safe_path(q.get("path"))
        if target == CONFIG["root"]:
            raise ApiError(403, "can't delete the served folder")
        if os.path.isdir(target) and not os.path.islink(target):
            try:
                os.rmdir(target)
            except OSError:
                raise ApiError(409, "folder is not empty")
        elif os.path.lexists(target):
            os.remove(target)
        else:
            raise ApiError(404, "not found")
        self.send_json(200, {"ok": True, "deleted": rel_of(target)})

    def api_upload(self, q):
        ctype, params = parse_header_params(self.headers.get("Content-Type", ""))
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            raise ApiError(411, "Content-Length required")
        if length > MAX_UPLOAD:
            self.close_connection = True
            raise ApiError(413, "upload is bigger than 200 MB")
        if ctype != "multipart/form-data" or not params.get("boundary"):
            self.drain_body()
            raise ApiError(400, "expected multipart/form-data")
        folder = safe_path(q.get("path"))
        if not os.path.isdir(folder):
            self.drain_body()
            raise ApiError(404, "folder not found")

        reader = MultipartReader(self.rfile, params["boundary"].encode("latin-1"), length)
        saved = []
        try:
            for headers, chunks in reader.parts():
                _, disp = parse_header_params(headers.get("content-disposition", ""))
                if "filename" not in disp:
                    for _ in chunks:  # ordinary form field: skip
                        pass
                    continue
                name = clean_filename(disp.get("filename"))
                with _name_lock:
                    dest = unique_path(folder, name)
                    open(dest, "wb").close()  # reserve the name
                tmp = dest + ".part"
                size = 0
                try:
                    with open(tmp, "wb") as out:
                        for c in chunks:
                            out.write(c)
                            size += len(c)
                    os.replace(tmp, dest)
                except BaseException:
                    for p in (tmp, dest):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                    raise
                saved.append({"name": os.path.basename(dest), "path": rel_of(dest), "size": size})
        except ApiError:
            self.close_connection = True
            raise
        if not saved:
            raise ApiError(400, "no file in the upload")
        self.send_json(200, {"ok": True, "saved": saved})

    def api_clip_get(self, q):
        text = run_clip("pbpaste")
        self.send_json(200, {"ok": True, "text": text})

    def api_clip_set(self, q):
        body = self.read_json_body()
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise ApiError(400, 'body must be {"text": "..."}')
        run_clip("pbcopy", text)
        self.send_json(200, {"ok": True, "length": len(text)})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description="PhoneBridge: files + clipboard over wifi")
    ap.add_argument("--port", type=int, default=8787, help="port to listen on (default 8787)")
    ap.add_argument("--root", default=os.path.expanduser("~/Documents"),
                    help="folder to serve (default ~/Documents)")
    args = ap.parse_args()

    root = os.path.realpath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        sys.exit("root folder not found: %s" % root)
    if not (STATIC_DIR / "index.html").is_file():
        sys.exit("static/ folder not found next to server.py (%s)" % STATIC_DIR)

    CONFIG.update(root=root, token=secrets.token_urlsafe(9), ip=lan_ip(), port=args.port)
    try:
        httpd = Server(("0.0.0.0", args.port), Handler)
    except OSError as e:
        sys.exit("could not listen on port %d: %s" % (args.port, e))

    url = "http://%s:%d" % (CONFIG["ip"], args.port)
    line = "=" * 52
    print(line)
    print("  PhoneBridge running at %s" % url)
    print(line)
    print("  token:  %s" % CONFIG["token"])
    print("  serving: %s" % root)
    print("  open the URL in a browser on the same wifi,")
    print("  paste the token, done. Ctrl-C to stop.")
    print(line)
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
