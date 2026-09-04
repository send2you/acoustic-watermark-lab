"""Local web server for hiding a message inside a file you bring.

Serves the GUI on 127.0.0.1 and does the encode / decode in Python, so the
browser needs no crypto or DSP of its own. One job: watermark an uploaded song
or voice clip, and read it back. Every response carries a `debug` trace so that
when something does not decode, the trace says where it stopped.
"""

import base64
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import http.server
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cover

PORT = 5000
HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.abspath(os.path.join(HERE, "..", "web"))


def probe(path):
    """What ffmpeg thinks the uploaded file actually is."""
    try:
        out = subprocess.run(
            [cover.ffmpeg(), "-hide_banner", "-i", path],
            capture_output=True, text=True, errors="replace",
        )
        for line in out.stderr.splitlines():
            line = line.strip()
            if line.startswith("Stream ") or line.startswith("Duration"):
                return line
    except Exception as e:
        return f"probe failed: {e}"
    return "no stream line reported"


class Handler(http.server.SimpleHTTPRequestHandler):
    # HTTP/1.1 with an explicit Content-Length on every response: a multi-megabyte
    # body written the HTTP/1.0 way gets reset part-way on Windows.
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ------------------------------------------------------------- routing

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html"):
            self.path = "/gui.html"
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        path = urlparse(self.path).path

        debug = []
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return self.send_json({"error": f"bad request: {e}", "debug": debug}, 400)

        try:
            if path == "/api/encode":
                self.api_encode(data, debug)
            elif path == "/api/decode":
                self.api_decode(data, debug)
            else:
                self.send_json({"error": "unknown endpoint", "debug": debug}, 404)
        except Exception as e:
            import traceback
            debug.append("unhandled server error:")
            debug += ["  " + l for l in traceback.format_exc().splitlines()]
            self.send_json({"error": str(e), "debug": debug}, 500)

    # ------------------------------------------------------------ handlers

    def api_encode(self, data, debug):
        """Watermark a user-supplied file (song, voice clip) with the message.

        Nothing is appended and no marker is written to the container; the file
        keeps its full length, and only the 1.2-3.6 kHz band of the span the
        message needs is touched. Output is a normal 320k MP3.
        """
        text = (data.get("text") or "").strip()
        room = (data.get("room") or "").strip()
        password = data.get("password") or ""
        b64 = data.get("cover_b64")
        name = data.get("cover_name") or "cover"

        if not text or not room or not password:
            return self.send_json(
                {"error": "text, room and password are all required", "debug": debug}, 400)
        if not b64:
            return self.send_json(
                {"error": "choose a song or audio file to hide the message in",
                 "debug": debug}, 400)

        blob = base64.b64decode(b64)
        debug.append(f"cover: {name!r}, {len(blob)/1024/1024:.2f} MB in, "
                     f"{len(text)} chars, room {room!r}")

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, os.path.basename(name) or "cover")
            with open(src, "wb") as f:
                f.write(blob)
            debug.append(f"  container: {probe(src)}")
            out = os.path.join(td, "hidden.mp3")

            try:
                marked, total = cover.encode_cover(src, text, room, password, out, "mp3")
            except ValueError as e:
                debug.append(f"  {e}")
                return self.send_json({"error": str(e), "debug": debug}, 400)

            debug.append(f"  watermarked the first {marked:.0f}s of {total:.0f}s "
                         f"(the rest is copied through untouched)")
            data_out = open(out, "rb").read()
            debug.append(f"  output: {len(data_out)/1024/1024:.2f} MB mp3  ({probe(out)})")

            # never hand back a file that does not read its own message back
            try:
                got = cover.decode_cover(out, room, password)
                ok = got == text
                debug.append(f"  self-check: {'OK' if ok else 'MISMATCH'}")
            except Exception as e:
                ok = False
                debug.append(f"  self-check FAILED: {e}")

        self.send_json({
            "audio_b64": base64.b64encode(data_out).decode(),
            "filename": f"{os.path.splitext(os.path.basename(name))[0] or 'audio'}.mp3",
            "duration": total,
            "bytes": len(data_out),
            "verified": ok,
            "debug": debug,
        })

    def api_decode(self, data, debug):
        room = (data.get("room") or "").strip()
        password = data.get("password") or ""
        b64 = data.get("audio_b64")
        name = data.get("filename") or "upload"

        if not room or not password or not b64:
            return self.send_json(
                {"error": "room, password and an audio file are all required",
                 "debug": debug}, 400)

        blob = base64.b64decode(b64)
        debug.append(f"decode: {name!r}, {len(blob)/1024/1024:.2f} MB, room {room!r}")

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, os.path.basename(name) or "upload")
            with open(src, "wb") as f:
                f.write(blob)
            debug.append(f"  container: {probe(src)}")
            try:
                text = cover.decode_cover(src, room, password)
                debug.append("  found, decrypted OK")
                return self.send_json({"text": text, "room": room, "debug": debug})
            except Exception as e:
                debug.append(f"  -> {e}")
                return self.send_json(
                    {"error": "no hidden message here, or wrong room/password",
                     "debug": debug}, 400)

    # -------------------------------------------------------------- output

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        for i in range(0, len(payload), 1 << 16):
            self.wfile.write(payload[i:i + (1 << 16)])
        self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    # Encoding runs for seconds; a single-threaded server would let the browser's
    # other requests queue behind it and get reset mid-request.
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    print(f"ready at http://127.0.0.1:{PORT}/", flush=True)
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("stopped")
