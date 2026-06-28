#!/usr/bin/env python3
"""
Upload a PNG/JPEG image to the ESP32-S3 display over USB serial.

Usage:
    # Direct upload (CLI mode)
    python3 upload_image.py <image_file> [--port PORT] [--baud BAUD]

    # Start persistent daemon
    python3 upload_image.py --daemon [--port PORT] [--baud BAUD] [--socket SOCKET]

    # Send via daemon
    python3 upload_image.py --send <image_file> [--socket SOCKET] [--quality Q]

The script converts the image to JPEG, sends it to the device,
and waits for an "OK" acknowledgment.

Use --dry-run to process the image without sending.
"""

import argparse
import io
import json
import os
import signal
import socket
import struct
import sys
import time

import serial
from PIL import Image

MAGIC = b"IMG!"
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 480
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUD = 3000000
DEFAULT_SOCKET = "/tmp/esp32-upload.sock"
JPEG_QUALITY = 85

# ──────────────────────────────────────────────
# Image processing (shared between CLI and daemon)
# ──────────────────────────────────────────────


def process_image(image_path, quality=JPEG_QUALITY):
    """Load, resize, composite onto black background, and JPEG-encode.
    Returns (jpeg_data, timing_dict).  Raises on file/format errors."""
    timing = {}

    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)

    t1 = time.time()
    src = Image.open(image_path)
    orig_w, orig_h = src.size

    if src.mode not in ("RGBA", "RGBa", "LA", "PA"):
        src = src.convert("RGBA")

    if orig_w > DISPLAY_WIDTH or orig_h > DISPLAY_HEIGHT:
        scale = min(DISPLAY_WIDTH / orig_w, DISPLAY_HEIGHT / orig_h)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        src = src.resize((new_w, new_h), Image.LANCZOS)
    timing["load_resize_ms"] = (time.time() - t1) * 1000

    # Composite onto black
    t2 = time.time()
    img = Image.new("RGB", src.size, (0, 0, 0))
    if src.mode in ("RGBA", "RGBa", "LA", "PA"):
        img.paste(src, mask=src.split()[-1])
    else:
        img.paste(src)
    timing["composite_ms"] = (time.time() - t2) * 1000

    # JPEG encode
    t3 = time.time()
    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG", quality=quality)
    jpeg_data = jpeg_buf.getvalue()
    timing["encode_ms"] = (time.time() - t3) * 1000
    timing["jpeg_size"] = len(jpeg_data)

    return jpeg_data, timing


# ──────────────────────────────────────────────
# Serial communication
# ──────────────────────────────────────────────


def open_serial(port, baud, timeout=30):
    """Open and return a serial connection."""
    return serial.Serial(port, baudrate=baud, timeout=timeout)


def send_over_serial(ser, jpeg_data, baud=DEFAULT_BAUD):
    """Send MAGIC+header+JPEG over an already-open serial port.
    Returns (ok, decode_us, draw_us, device_ms, send_ms)."""
    header = MAGIC + struct.pack("<I", len(jpeg_data))
    jpeg_size = len(jpeg_data)

    t0 = time.time()
    ser.write(header)
    ser.write(jpeg_data)
    ser.flush()
    send_ms = (time.time() - t0) * 1000

    # Wait for OK
    ok_received = False
    decode_us = draw_us = 0
    deadline = time.time() + 35
    while time.time() < deadline:
        line = ser.readline().decode("ascii", errors="replace").strip()
        if line:
            if line.startswith("OK"):
                parts = line.split()
                if len(parts) >= 3:
                    decode_us = int(parts[1])
                    draw_us = int(parts[2])
                ok_received = True
                break
    device_ms = (time.time() - t0) * 1000

    return ok_received, decode_us, draw_us, device_ms, send_ms


# ──────────────────────────────────────────────
# CLI mode
# ──────────────────────────────────────────────


def cli_mode(args):
    t0 = time.time()

    # Process
    try:
        jpeg_data, proc_timing = process_image(args.image, args.quality)
    except FileNotFoundError:
        print(f"ERROR: File not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    if args.save_jpeg:
        with open(args.save_jpeg, "wb") as f:
            f.write(jpeg_data)

    if args.dry_run:
        print(f"  load+resize: {proc_timing['load_resize_ms']:.0f}ms  "
              f"composite: {proc_timing['composite_ms']:.0f}ms  "
              f"encode: {proc_timing['encode_ms']:.0f}ms")
        print("Dry run complete. Image NOT sent to device.")
        return

    jpeg_size = proc_timing["jpeg_size"]

    # Open serial
    t_serial_open = time.time()
    try:
        ser = open_serial(args.port, args.baud)
    except serial.SerialException as e:
        print(f"ERROR: Could not open {args.port}: {e}", file=sys.stderr)
        sys.exit(1)
    serial_open_ms = (time.time() - t_serial_open) * 1000

    # Send
    ok, decode_us, draw_us, device_ms, send_ms = send_over_serial(ser, jpeg_data, args.baud)
    ser.close()

    # Report
    _print_report(args, proc_timing, serial_open_ms, send_ms, device_ms, decode_us, draw_us, ok, t0)


def _print_report(args, proc_timing, serial_open_ms, send_ms, device_ms, decode_us, draw_us, ok, t_start):
    jpeg_size = proc_timing["jpeg_size"]
    wire_time = jpeg_size * 10 / args.baud  # 10 bits per byte (8N1)

    print(f"  load+resize: {proc_timing['load_resize_ms']:5.0f}ms")
    print(f"  composite:   {proc_timing['composite_ms']:5.0f}ms")
    print(f"  jpeg encode: {proc_timing['encode_ms']:5.0f}ms  ({jpeg_size/1024:.1f} KB)")
    print(f"  serial open: {serial_open_ms:5.0f}ms")
    print(f"  serial send: {send_ms:5.0f}ms  ({jpeg_size} B @ {args.baud} baud, wire min {wire_time*1000:.0f}ms)")
    print(f"  device:     {device_ms:5.0f}ms  (decode {decode_us/1000:.0f}ms + draw {draw_us/1000:.0f}ms "
          f"+ transfer tail {device_ms - (decode_us+draw_us)/1000:.0f}ms)")
    print(f"  ─────────────────────")
    print(f"  total:       {(time.time()-t_start)*1000:5.0f}ms")

    if ok:
        print("SUCCESS")
    else:
        print("WARNING: Did not receive OK")


# ──────────────────────────────────────────────
# Daemon
# ──────────────────────────────────────────────


class UploadDaemon:
    """Persistent daemon that keeps the serial port open across uploads."""

    def __init__(self, port, baud, socket_path, quality=JPEG_QUALITY):
        self.port = port
        self.baud = baud
        self.socket_path = socket_path
        self.quality = quality
        self.ser = None
        self.server_sock = None
        self._running = False

    def start(self):
        # Open serial once
        try:
            self.ser = open_serial(self.port, self.baud)
        except serial.SerialException as e:
            print(f"ERROR: Could not open {self.port}: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Daemon: serial port {self.port} @ {self.baud} baud opened")

        # Remove stale socket file
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

        # Create Unix socket server
        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(self.socket_path)
        self.server_sock.listen(5)
        self.server_sock.settimeout(1.0)  # so we can check _running periodically
        print(f"Daemon: listening on {self.socket_path}")

        # Handle signals gracefully
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

        self._running = True
        print(f"Daemon: ready (PID {os.getpid()})")

        while self._running:
            try:
                conn, addr = self.server_sock.accept()
            except socket.timeout:
                continue  # check _running
            except OSError:
                break  # shutting down
            self._handle_client(conn)

        self._cleanup()

    def _handle_client(self, conn):
        """Serve one client request."""
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            request = json.loads(data.decode("utf-8"))
            image_path = request["path"]
            quality = request.get("quality", self.quality)
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError, ValueError) as e:
            resp = {"status": "error", "message": f"Bad request: {e}"}
            _send_response(conn, resp)
            conn.close()
            return

        # Process image
        try:
            jpeg_data, proc_timing = process_image(image_path, quality)
        except FileNotFoundError:
            resp = {"status": "error", "message": f"File not found: {image_path}"}
            _send_response(conn, resp)
            conn.close()
            return
        except Exception as e:
            resp = {"status": "error", "message": f"Processing error: {e}"}
            _send_response(conn, resp)
            conn.close()
            return

        # Send over serial
        try:
            ok, decode_us, draw_us, device_ms, send_ms = send_over_serial(self.ser, jpeg_data, self.baud)
        except serial.SerialException as e:
            resp = {"status": "error", "message": f"Serial error: {e}"}
            _send_response(conn, resp)
            conn.close()
            # Try to reopen serial for future requests
            self._try_reopen_serial()
            return

        resp = {
            "status": "ok" if ok else "no_ack",
            "jpeg_size": proc_timing["jpeg_size"],
            "timing": {
                "load_resize_ms": round(proc_timing["load_resize_ms"], 1),
                "composite_ms": round(proc_timing["composite_ms"], 1),
                "encode_ms": round(proc_timing["encode_ms"], 1),
                "send_ms": round(send_ms, 1),
                "device_ms": round(device_ms, 1),
                "decode_us": decode_us,
                "draw_us": draw_us,
            },
        }
        _send_response(conn, resp)
        conn.close()

    def _try_reopen_serial(self):
        """Attempt to reopen the serial port if it was lost."""
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        for attempt in range(3):
            try:
                self.ser = open_serial(self.port, self.baud)
                print(f"Daemon: serial port {self.port} reconnected", file=sys.stderr)
                return
            except serial.SerialException:
                time.sleep(1)
        print(f"Daemon: failed to reconnect serial port {self.port}", file=sys.stderr)

    def _on_signal(self, signum, frame):
        del signum, frame
        print("\nDaemon: shutting down...")
        self._running = False

    def _cleanup(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("Daemon: serial port closed")
        if self.server_sock:
            self.server_sock.close()
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        print("Daemon: stopped")


def _send_response(conn, obj):
    """Send a JSON response followed by newline."""
    try:
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))
    except Exception:
        pass


# ──────────────────────────────────────────────
# Client mode (send via daemon)
# ──────────────────────────────────────────────


def client_mode(args):
    """Connect to a running daemon and ask it to upload an image."""
    if not os.path.exists(args.image):
        print(f"ERROR: File not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(args.socket)
    except (FileNotFoundError, ConnectionRefusedError):
        print(f"ERROR: Daemon not running on {args.socket}", file=sys.stderr)
        sys.exit(1)

    request = json.dumps({
        "path": os.path.abspath(args.image),
        "quality": args.quality,
    }).encode("utf-8") + b"\n"

    sock.sendall(request)

    # Read response
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if b"\n" in data:
            break
    sock.close()

    try:
        resp = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        print("ERROR: Invalid response from daemon", file=sys.stderr)
        sys.exit(1)

    if resp["status"] == "error":
        print(f"ERROR: {resp['message']}", file=sys.stderr)
        sys.exit(1)

    timing = resp["timing"]
    jpeg_size = resp["jpeg_size"]
    wire_time = jpeg_size * 10 / args.baud

    print(f"  load+resize: {timing['load_resize_ms']:5.1f}ms")
    print(f"  composite:   {timing['composite_ms']:5.1f}ms")
    print(f"  jpeg encode: {timing['encode_ms']:5.1f}ms  ({jpeg_size/1024:.1f} KB)")
    print(f"  serial send: {timing['send_ms']:5.1f}ms  ({jpeg_size} B @ {args.baud} baud, wire min {wire_time*1000:.0f}ms)")
    device_ms = timing["device_ms"]
    decode_us = timing["decode_us"]
    draw_us = timing["draw_us"]
    print(f"  device:     {device_ms:5.1f}ms  (decode {decode_us/1000:.0f}ms + draw {draw_us/1000:.0f}ms "
          f"+ transfer tail {device_ms - (decode_us+draw_us)/1000:.0f}ms)")

    if resp["status"] == "no_ack":
        print("WARNING: Did not receive OK")
    else:
        print("SUCCESS")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Upload image to ESP32-S3 display")
    parser.add_argument("image", nargs="?", help="Path to PNG or JPEG image file")
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY,
                        help=f"JPEG quality 1-100 (default: {JPEG_QUALITY})")
    parser.add_argument("--dry-run", action="store_true", help="Process image but don't send over serial")
    parser.add_argument("--save-jpeg", metavar="FILE", help="Save converted JPEG to file for inspection")

    # Daemon / client mode
    parser.add_argument("--daemon", action="store_true", help="Start persistent daemon (keeps serial port open)")
    parser.add_argument("--send", action="store_true", help="Send image via a running daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET,
                        help=f"Unix socket path for daemon/client (default: {DEFAULT_SOCKET})")

    args = parser.parse_args()

    if args.daemon:
        if args.image:
            print("ERROR: --daemon does not take an image argument", file=sys.stderr)
            sys.exit(1)
        daemon = UploadDaemon(args.port, args.baud, args.socket, args.quality)
        daemon.start()
    elif args.send:
        if not args.image:
            print("ERROR: --send requires an image path", file=sys.stderr)
            sys.exit(1)
        client_mode(args)
    else:
        if not args.image:
            parser.print_help()
            sys.exit(1)
        cli_mode(args)


if __name__ == "__main__":
    main()
