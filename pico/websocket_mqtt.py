"""
WebSocket wrapper for MQTT on MicroPython.

Allows umqtt.simple to connect through wss:// (WebSocket over TLS)
which is required for mqtt.heylua.ai — the broker is behind an ALB
that only exposes WebSocket on port 443.

Implements the socket-like interface umqtt.simple expects:
  - write(data) / write(data, length)
  - read(n)
  - setblocking(flag)
  - settimeout(timeout)
  - close()
"""

import socket
import ssl
import struct
import binascii
import os


class WebSocketMQTT:
    """Socket-like object speaking WebSocket binary frames for umqtt."""

    def __init__(self, host, port=443, path="/mqtt"):
        self._host = host
        self._port = port
        self._path = path
        self._sock = None
        self._buf = b""
        self._blocking = True

    def connect(self):
        """Perform TCP + TLS + WebSocket upgrade handshake."""
        addr = socket.getaddrinfo(self._host, self._port)[0][-1]
        raw = socket.socket()
        raw.connect(addr)

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_NONE  # Pico has no CA store
        self._sock = ctx.wrap_socket(raw, server_hostname=self._host)

        # WebSocket upgrade
        key = binascii.b2a_base64(os.urandom(16)).strip()
        req = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: {}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: mqtt\r\n"
            "\r\n"
        ).format(self._path, self._host, key.decode())

        self._sock.write(req.encode())

        # Read HTTP response headers
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.read(1)
            if not chunk:
                raise OSError("WS handshake: connection closed")
            response += chunk

        status_line = response.split(b"\r\n")[0]
        if b"101" not in status_line:
            raise OSError("WS handshake failed: " + status_line.decode())

    def write(self, data, length=None):
        """Send data as a WebSocket binary frame. Supports write(data, n) form."""
        if isinstance(data, str):
            data = data.encode()
        if length is not None:
            data = data[:length]
        if isinstance(data, memoryview):
            data = bytes(data)

        frame = bytearray()
        frame.append(0x82)  # FIN + binary opcode

        dlen = len(data)
        if dlen < 126:
            frame.append(0x80 | dlen)
        elif dlen < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", dlen))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", dlen))

        mask = os.urandom(4)
        frame.extend(mask)

        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]
        frame.extend(masked)

        self._sock.write(bytes(frame))
        return dlen

    def read(self, n):
        """
        Read n bytes, unwrapping WebSocket frames as needed.
        In non-blocking mode, returns None if no data available.
        """
        if len(self._buf) >= n:
            result = self._buf[:n]
            self._buf = self._buf[n:]
            return result

        try:
            payload = self._read_frame()
            if payload is None:
                return self.read(n)
            self._buf += payload
        except OSError as e:
            if not self._blocking:
                if len(self._buf) > 0:
                    result = self._buf[:n]
                    self._buf = self._buf[n:]
                    return result
                return None
            raise

        if len(self._buf) >= n:
            result = self._buf[:n]
            self._buf = self._buf[n:]
            return result

        return self.read(n)

    def _read_frame(self):
        """Read one WebSocket frame, return payload or None for control frames."""
        header = self._read_exact(2)
        opcode = header[0] & 0x0F
        is_masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]

        mask = self._read_exact(4) if is_masked else None
        payload = self._read_exact(length) if length > 0 else b""

        if is_masked and length > 0:
            payload = bytearray(payload)
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]
            payload = bytes(payload)

        if opcode == 0x08:  # Close
            raise OSError("WebSocket closed by server")
        elif opcode == 0x09:  # Ping -> Pong
            self._send_pong(payload)
            return None
        elif opcode == 0x0A:  # Pong
            return None

        return payload

    def _send_pong(self, data):
        """Respond to WebSocket ping."""
        frame = bytearray()
        frame.append(0x8A)  # FIN + pong
        dlen = len(data)
        frame.append(0x80 | dlen)
        mask = os.urandom(4)
        frame.extend(mask)
        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]
        frame.extend(masked)
        self._sock.write(bytes(frame))

    def _read_exact(self, n):
        """Read exactly n bytes from the underlying TLS socket."""
        data = b""
        while len(data) < n:
            chunk = self._sock.read(n - len(data))
            if chunk is None:
                if not self._blocking:
                    raise OSError("EAGAIN")
                continue
            if len(chunk) == 0:
                raise OSError("WebSocket: connection closed")
            data += chunk
        return data

    def setblocking(self, flag):
        self._blocking = flag
        if self._sock:
            try:
                self._sock.setblocking(flag)
            except Exception:
                pass

    def settimeout(self, timeout):
        if self._sock:
            try:
                self._sock.settimeout(timeout)
            except Exception:
                pass

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
