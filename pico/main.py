"""
Lua AI Pico 2 W LED Controller

Controls the onboard LED via commands from a Lua AI agent.
Connects via MQTT over WebSocket to wss://mqtt.heylua.ai/mqtt

The device is self-describing: it sends its command manifest to the
agent at connect time. The agent discovers available commands as tools
automatically — no server-side configuration needed.

Commands:
  led_on    - Turn LED on
  led_off   - Turn LED off
  blink     - Blink LED N times (with configurable delay)
  status    - Get LED state + system info

Setup:
  1. Copy config.example.py to config.py and fill in your credentials
  2. Upload main.py, websocket_mqtt.py, and config.py to the Pico
  3. The script runs automatically on boot
"""

import network
import machine
import time
import json

from umqtt.simple import MQTTClient
from websocket_mqtt import WebSocketMQTT
import config

# === CONFIG (from config.py) ===
MQTT_SERVER = "mqtt.heylua.ai"
MQTT_PORT = 443

# === HARDWARE ===
led = machine.Pin("LED", machine.Pin.OUT)
led_state = False

# === WIFI ===
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print("[wifi] Already connected:", wlan.ifconfig()[0])
        return
    print("[wifi] Connecting to", config.WIFI_SSID, end="")
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            break
        print(".", end="")
        time.sleep(1)
    if wlan.isconnected():
        print(" OK!", wlan.ifconfig()[0])
    else:
        print(" FAILED")
        machine.reset()


# === SELF-DESCRIBING COMMANDS ===
# This manifest is sent to the agent at connect time.
# The agent discovers these as tools automatically.
TOPIC_PREFIX = "lua/devices/{}/{}/".format(config.AGENT_ID, config.DEVICE_NAME)
COMMANDS = [
    {"name": "led_on", "description": "Turn the onboard LED on"},
    {"name": "led_off", "description": "Turn the onboard LED off"},
    {
        "name": "blink",
        "description": "Blink the LED a number of times",
        "inputSchema": {
            "type": "object",
            "properties": {
                "times": {"type": "number", "description": "Number of times to blink (1-20)"},
                "delay_ms": {"type": "number", "description": "Delay between blinks in ms (50-1000)"},
            },
        },
    },
    {"name": "status", "description": "Get the current LED state and device system info"},
]

# === COMMAND HANDLERS ===
seen_ids = {}
last_heartbeat = 0
client = None


def handle_command(msg):
    global led_state
    cmd_id = None
    try:
        data = json.loads(msg)
        cmd_id = data.get("commandId")
        command = data.get("command")
        payload = data.get("payload", {})

        if not cmd_id or not command:
            return

        # Dedup: if we've already handled this command, re-send the cached response
        if cmd_id in seen_ids:
            client.publish(TOPIC_PREFIX + "response", json.dumps(seen_ids[cmd_id]))
            return

        # Execute the command
        if command == "led_on":
            led.on()
            led_state = True
            result = {"led": "on"}
        elif command == "led_off":
            led.off()
            led_state = False
            result = {"led": "off"}
        elif command == "blink":
            times = min(max(int(payload.get("times", 3)), 1), 20)
            delay = min(max(int(payload.get("delay_ms", 200)), 50), 1000)
            for _ in range(times):
                led.on()
                time.sleep_ms(delay)
                led.off()
                time.sleep_ms(delay)
            led_state = False
            result = {"blinked": times, "delay_ms": delay}
        elif command == "status":
            import gc
            gc.collect()
            result = {
                "led": "on" if led_state else "off",
                "free_memory": gc.mem_free(),
                "frequency_mhz": machine.freq() // 1_000_000,
                "uptime_s": time.ticks_ms() // 1000,
            }
        else:
            response = {"commandId": cmd_id, "success": False, "error": "Unknown: " + command}
            seen_ids[cmd_id] = response
            client.publish(TOPIC_PREFIX + "response", json.dumps(response))
            return

        response = {"commandId": cmd_id, "success": True, "data": result}
        seen_ids[cmd_id] = response
        client.publish(TOPIC_PREFIX + "response", json.dumps(response))
        print("[cmd]", command, "->", result)

    except Exception as e:
        print("[err] Command handler:", e)
        if cmd_id:
            response = {"commandId": cmd_id, "success": False, "error": str(e)}
            client.publish(TOPIC_PREFIX + "response", json.dumps(response))


def on_message(topic, msg):
    topic_str = topic.decode() if isinstance(topic, bytes) else topic
    suffix = topic_str.replace(TOPIC_PREFIX, "")

    if suffix == "command":
        handle_command(msg)
    elif suffix == "connected":
        print("[mqtt] Server confirmed connection")
    elif suffix == "error":
        data = json.loads(msg)
        print("[mqtt] Error:", data.get("code"), data.get("message"))


# === MQTT CONNECTION ===
def mqtt_connect():
    global client

    client_id = "lua-{}-{}".format(config.AGENT_ID, config.DEVICE_NAME)
    username = "{}:{}".format(config.AGENT_ID, config.DEVICE_NAME)

    # Step 1: establish WebSocket connection
    print("[ws] Connecting to wss://{}:{}...".format(MQTT_SERVER, MQTT_PORT))
    ws = WebSocketMQTT(MQTT_SERVER, MQTT_PORT, "/mqtt")
    ws.connect()
    print("[ws] WebSocket connected")

    # Step 2: create MQTT client (we'll replace its socket with our WebSocket)
    client = MQTTClient(
        client_id=client_id,
        server=MQTT_SERVER,
        port=MQTT_PORT,
        user=username,
        password=config.API_KEY,
        keepalive=60,
    )

    # Last Will and Testament — marks device offline if connection drops
    will_topic = TOPIC_PREFIX + "status"
    will_msg = json.dumps({"status": "offline", "timestamp": str(time.time())})
    client.set_last_will(will_topic, will_msg, retain=True, qos=1)
    client.set_callback(on_message)

    # Step 3: replace umqtt's raw TCP socket with our WebSocket wrapper
    client.sock = ws
    _mqtt_connect_handshake(client, client_id, username, config.API_KEY, will_topic, will_msg)

    # Subscribe to server -> device topics
    for suffix in ("command", "connected", "trigger_ack", "error"):
        client.subscribe(TOPIC_PREFIX + suffix)

    # Publish online status (retained, no secrets)
    client.publish(
        TOPIC_PREFIX + "status",
        json.dumps({"status": "online", "timestamp": str(time.time())}),
        retain=True,
    )

    # Publish auth + self-describing command manifest (non-retained)
    client.publish(
        TOPIC_PREFIX + "status",
        json.dumps({
            "status": "online",
            "apiKey": config.API_KEY,
            "commands": COMMANDS,
        }),
        retain=False,
    )

    print("[mqtt] Connected as", config.DEVICE_NAME)


def _mqtt_connect_handshake(client, client_id, username, password, will_topic, will_msg):
    """Send MQTT CONNECT packet manually through the WebSocket and read CONNACK."""
    import struct

    proto_name = b"\x00\x04MQTT"
    proto_level = b"\x04"  # MQTT 3.1.1
    connect_flags = 0b11101110  # user+pass+will_retain+will_qos1+will+clean
    keep_alive = struct.pack(">H", 60)

    def encode_str(s):
        encoded = s.encode() if isinstance(s, str) else s
        return struct.pack(">H", len(encoded)) + encoded

    payload = encode_str(client_id)
    payload += encode_str(will_topic)
    payload += encode_str(will_msg)
    payload += encode_str(username)
    payload += encode_str(password)

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

    client.sock.write(bytes(pkt))

    resp = client.sock.read(4)
    if resp[0] != 0x20:
        raise OSError("Expected CONNACK, got 0x{:02x}".format(resp[0]))
    if resp[3] != 0:
        raise OSError("MQTT CONNACK error: rc={}".format(resp[3]))

    print("[mqtt] CONNACK received, session established")


# === MAIN LOOP ===
def main():
    global last_heartbeat

    connect_wifi()

    print("[pico] Connecting to Lua AI agent...")
    mqtt_connect()

    # Blink 3 times to signal ready
    for _ in range(3):
        led.on()
        time.sleep_ms(100)
        led.off()
        time.sleep_ms(100)

    print("[pico] Ready! Waiting for commands...")

    reconnect_failures = 0

    while True:
        try:
            client.check_msg()

            # Heartbeat every 30s
            now = time.ticks_ms()
            if time.ticks_diff(now, last_heartbeat) > 30_000:
                client.publish(TOPIC_PREFIX + "heartbeat", b"")
                last_heartbeat = now

            # Clean old dedup entries (keep last 20)
            if len(seen_ids) > 20:
                keys = list(seen_ids.keys())
                for k in keys[:-20]:
                    del seen_ids[k]

            reconnect_failures = 0
            time.sleep_ms(100)

        except Exception as e:
            print("[err] Connection lost:", type(e).__name__, e)
            time.sleep(5)
            try:
                connect_wifi()
                mqtt_connect()
                reconnect_failures = 0
                print("[pico] Reconnected successfully")
            except Exception as e2:
                reconnect_failures += 1
                print("[err] Reconnect failed ({}/5):".format(reconnect_failures), e2)
                if reconnect_failures >= 5:
                    print("[err] Too many failures, resetting device...")
                    machine.reset()
                time.sleep(10)


main()
