"""
Lua Device Client for MicroPython (Raspberry Pi Pico W)

Connects to the Lua AI platform via MQTT, enabling agents to send
commands to and receive triggers from constrained IoT devices.

Usage:
    from lua_device import LuaDevice

    device = LuaDevice(
        agent_id="baseAgent_agent_123",
        api_key="api_your_key",
        device_name="pico-sensor",
        server="mqtt.heylua.ai",
    )

    @device.command("read_sensor")
    def read_sensor(payload):
        return {"temperature": 22.5, "humidity": 60}

    device.connect()
    device.run()  # blocks, handles commands
"""

import json
import time
import ssl
import socket
import struct
import binascii
import os

try:
    from umqtt.robust import MQTTClient
except ImportError:
    from umqtt.simple import MQTTClient


class _WebSocketMQTT:
    """Socket-like wrapper that speaks WebSocket binary frames for umqtt.

    Allows umqtt.simple to connect through wss:// (WebSocket over TLS),
    which is required when the MQTT broker is behind a load balancer that
    only exposes WebSocket on port 443.
    """

    def __init__(self, host, port=443, path="/mqtt"):
        self._host = host
        self._port = port
        self._path = path
        self._sock = None
        self._buf = b""
        self._blocking = True

    def connect(self):
        addr = socket.getaddrinfo(self._host, self._port)[0][-1]
        raw = socket.socket()
        raw.connect(addr)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_NONE
        self._sock = ctx.wrap_socket(raw, server_hostname=self._host)

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

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.read(1)
            if not chunk:
                raise OSError("WS handshake: connection closed")
            response += chunk
        if b"101" not in response.split(b"\r\n")[0]:
            raise OSError("WS handshake failed: " + response.split(b"\r\n")[0].decode())

    def write(self, data, length=None):
        if isinstance(data, str):
            data = data.encode()
        if length is not None:
            data = data[:length]
        if isinstance(data, memoryview):
            data = bytes(data)

        frame = bytearray()
        frame.append(0x82)
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
        if len(self._buf) >= n:
            result = self._buf[:n]
            self._buf = self._buf[n:]
            return result
        try:
            payload = self._read_frame()
            if payload is None:
                return self.read(n)
            self._buf += payload
        except OSError:
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
        if opcode == 0x08:
            raise OSError("WebSocket closed by server")
        elif opcode == 0x09:
            self._send_pong(payload)
            return None
        elif opcode == 0x0A:
            return None
        return payload

    def _send_pong(self, data):
        frame = bytearray()
        frame.append(0x8A)
        frame.append(0x80 | len(data))
        mask = os.urandom(4)
        frame.extend(mask)
        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]
        frame.extend(masked)
        self._sock.write(bytes(frame))

    def _read_exact(self, n):
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


class LuaDevice:
    def __init__(self, agent_id, api_key, device_name, server="mqtt.heylua.ai",
                 port=443, group=None, use_ssl=True, websocket=True):
        self.agent_id = agent_id
        self.api_key = api_key
        self.device_name = device_name
        self.server = server
        self.port = port
        self.group = group
        self.use_ssl = use_ssl
        self.websocket = websocket

        self._commands = {}
        self._connected = False
        self._client = None
        self._last_heartbeat = 0
        self._heartbeat_interval = 30  # seconds
        self._seen_ids = {}  # simple dedup: commandId → timestamp
        self._dedup_ttl = 300  # 5 minutes

        # MQTT identifiers
        self._client_id = "lua-{}-{}".format(agent_id, device_name)
        self._username = "{}:{}".format(agent_id, device_name)
        self._topic_prefix = "lua/devices/{}/{}/".format(agent_id, device_name)

    def command(self, name, description=None, inputSchema=None):
        """Decorator to register a command handler with optional description and schema."""
        def decorator(fn):
            self._commands[name] = {
                "handler": fn,
                "description": description or name,
            }
            if inputSchema:
                self._commands[name]["inputSchema"] = inputSchema
            return fn
        return decorator

    def on_command(self, name, handler, description=None, inputSchema=None):
        """Register a command handler (non-decorator style)."""
        self._commands[name] = {
            "handler": handler,
            "description": description or name,
        }
        if inputSchema:
            self._commands[name]["inputSchema"] = inputSchema

    def connect(self):
        """Connect to the MQTT broker with LWT for disconnect detection."""
        will_topic = self._topic_prefix + "status"
        will_msg = json.dumps({"status": "offline", "timestamp": str(time.time())})

        if self.websocket:
            self._connect_websocket(will_topic, will_msg)
        else:
            self._connect_tcp(will_topic, will_msg)

        self._connected = True

        # Subscribe to server → device topics
        for suffix in ("command", "connected", "trigger_ack",
                       "trigger_result", "error", "pong"):
            topic = self._topic_prefix + suffix
            self._client.subscribe(topic, qos=1)

        # Publish online status (retained — no secrets)
        online_payload = {
            "status": "online",
            "timestamp": str(time.time()),
        }
        if self.group:
            online_payload["group"] = self.group

        self._client.publish(
            self._topic_prefix + "status",
            json.dumps(online_payload),
            retain=True,
            qos=1,
        )

        # Build command manifest from registered handlers
        commands = []
        for name, meta in self._commands.items():
            cmd_def = {"name": name}
            if isinstance(meta, dict):
                cmd_def["description"] = meta.get("description", name)
                if "inputSchema" in meta:
                    cmd_def["inputSchema"] = meta["inputSchema"]
            else:
                cmd_def["description"] = name
            commands.append(cmd_def)

        # Send API key + command manifest (non-retained) for server-side auth
        self._client.publish(
            self._topic_prefix + "status",
            json.dumps({
                "status": "online",
                "apiKey": self.api_key,
                "group": self.group,
                "commands": commands,
            }),
            retain=False,
            qos=1,
        )

        print("[lua-device] Connected as", self.device_name)

    def _connect_tcp(self, will_topic, will_msg):
        """Connect via raw MQTT over TCP/TLS (port 8883)."""
        ssl_params = {}
        if self.use_ssl:
            ssl_params = {"server_hostname": self.server}

        self._client = MQTTClient(
            client_id=self._client_id,
            server=self.server,
            port=self.port,
            user=self._username,
            password=self.api_key,
            keepalive=60,
            ssl=self.use_ssl,
            ssl_params=ssl_params,
        )
        self._client.set_last_will(will_topic, will_msg, retain=True, qos=1)
        self._client.set_callback(self._on_message)
        self._client.connect()

    def _connect_websocket(self, will_topic, will_msg):
        """Connect via MQTT over WebSocket (port 443)."""
        print("[lua-device] Connecting via WebSocket to wss://{}:{}...".format(
            self.server, self.port))
        ws = _WebSocketMQTT(self.server, self.port, "/mqtt")
        ws.connect()

        self._client = MQTTClient(
            client_id=self._client_id,
            server=self.server,
            port=self.port,
            user=self._username,
            password=self.api_key,
            keepalive=60,
        )
        self._client.set_last_will(will_topic, will_msg, retain=True, qos=1)
        self._client.set_callback(self._on_message)

        # Replace umqtt's TCP socket with our WebSocket wrapper
        self._client.sock = ws

        # Send MQTT CONNECT packet through WebSocket
        proto_name = b"\x00\x04MQTT"
        proto_level = b"\x04"
        connect_flags = 0b11101110
        keep_alive = struct.pack(">H", 60)

        def encode_str(s):
            encoded = s.encode() if isinstance(s, str) else s
            return struct.pack(">H", len(encoded)) + encoded

        payload = encode_str(self._client_id)
        payload += encode_str(will_topic)
        payload += encode_str(will_msg)
        payload += encode_str(self._username)
        payload += encode_str(self.api_key)

        var_header = proto_name + proto_level + bytes([connect_flags]) + keep_alive
        remaining = var_header + payload

        pkt = bytearray()
        pkt.append(0x10)
        rl = len(remaining)
        while True:
            byte = rl % 128
            rl = rl // 128
            if rl > 0:
                byte |= 0x80
            pkt.append(byte)
            if rl == 0:
                break
        pkt.extend(remaining)

        self._client.sock.write(bytes(pkt))

        resp = self._client.sock.read(4)
        if resp[0] != 0x20:
            raise OSError("Expected CONNACK, got 0x{:02x}".format(resp[0]))
        if resp[3] != 0:
            raise OSError("MQTT CONNACK error: rc={}".format(resp[3]))

        print("[lua-device] WebSocket connected")

    def disconnect(self):
        """Gracefully disconnect — publish offline status first."""
        if self._client and self._connected:
            self._client.publish(
                self._topic_prefix + "status",
                json.dumps({"status": "offline", "timestamp": str(time.time())}),
                retain=True,
                qos=1,
            )
            self._client.disconnect()
            self._connected = False
            print("[lua-device] Disconnected")

    def trigger(self, name, payload=None):
        """Fire a trigger to the agent."""
        if not self._connected:
            raise RuntimeError("Not connected")

        msg = json.dumps({
            "triggerName": name,
            "payload": payload or {},
        })
        self._client.publish(
            self._topic_prefix + "trigger",
            msg,
            qos=1,
        )

    def run(self, check_interval_ms=100):
        """
        Main loop — checks for incoming messages and sends heartbeats.
        Blocks forever. Call this after connect() and registering commands.
        """
        print("[lua-device] Listening for commands...")
        while self._connected:
            try:
                # Check for incoming messages (non-blocking)
                self._client.check_msg()

                # Send heartbeat every 30s
                now = time.time()
                if now - self._last_heartbeat >= self._heartbeat_interval:
                    self._client.publish(
                        self._topic_prefix + "heartbeat",
                        b"",
                        qos=0,
                    )
                    self._last_heartbeat = now

                # Clean expired dedup entries periodically
                if len(self._seen_ids) > 100:
                    self._clean_dedup()

                time.sleep_ms(check_interval_ms)

            except OSError as e:
                print("[lua-device] Connection lost:", e)
                self._reconnect()
            except Exception as e:
                print("[lua-device] Error:", e)
                time.sleep(1)

    def _on_message(self, topic, msg):
        """Callback for incoming MQTT messages."""
        try:
            topic_str = topic.decode() if isinstance(topic, bytes) else topic
            suffix = topic_str.replace(self._topic_prefix, "")

            if suffix == "command":
                self._handle_command(msg)
            elif suffix == "connected":
                data = json.loads(msg)
                print("[lua-device] Server confirmed connection:", data.get("message", ""))
            elif suffix == "trigger_ack":
                data = json.loads(msg)
                print("[lua-device] Trigger ACK:", data.get("triggerId", ""))
            elif suffix == "error":
                data = json.loads(msg)
                print("[lua-device] Error:", data.get("code", ""), data.get("message", ""))
            elif suffix == "pong":
                pass  # latency check response

        except Exception as e:
            print("[lua-device] Message handler error:", e)

    def _handle_command(self, msg):
        """Process an incoming command and publish the response."""
        try:
            data = json.loads(msg)
            command_id = data.get("commandId")
            command = data.get("command")
            payload = data.get("payload", {})

            if not command_id or not command:
                return

            # Idempotency check
            if command_id in self._seen_ids:
                return
            self._seen_ids[command_id] = time.time()

            cmd_entry = self._commands.get(command)
            if not cmd_entry:
                self._publish_response(command_id, False, error="Unknown command: " + command)
                return

            handler = cmd_entry["handler"] if isinstance(cmd_entry, dict) else cmd_entry

            # Execute handler
            try:
                result = handler(payload)
                self._publish_response(command_id, True, data=result)
            except Exception as e:
                self._publish_response(command_id, False, error=str(e))

        except Exception as e:
            print("[lua-device] Command error:", e)

    def _publish_response(self, command_id, success, data=None, error=None):
        """Publish command response."""
        response = {
            "commandId": command_id,
            "success": success,
        }
        if data is not None:
            response["data"] = data
        if error is not None:
            response["error"] = error

        self._client.publish(
            self._topic_prefix + "response",
            json.dumps(response),
            qos=1,
        )

    def _reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        self._connected = False
        delay = 1
        max_delay = 30
        attempts = 0
        while not self._connected:
            try:
                print("[lua-device] Reconnecting in", delay, "s...")
                time.sleep(delay)
                will_topic = self._topic_prefix + "status"
                will_msg = json.dumps({"status": "offline", "timestamp": str(time.time())})
                if self.websocket:
                    self._connect_websocket(will_topic, will_msg)
                else:
                    self._connect_tcp(will_topic, will_msg)
                self._connected = True
                self.connect()  # Re-subscribe and re-publish status
                print("[lua-device] Reconnected")
            except Exception as e:
                attempts += 1
                print("[lua-device] Reconnect failed:", e)
                delay = min(delay * 2, max_delay)
                if attempts >= 10:
                    import machine
                    print("[lua-device] Too many failures, resetting...")
                    machine.reset()

    def _clean_dedup(self):
        """Remove expired command IDs from dedup cache."""
        now = time.time()
        expired = [k for k, v in self._seen_ids.items() if now - v > self._dedup_ttl]
        for k in expired:
            del self._seen_ids[k]
