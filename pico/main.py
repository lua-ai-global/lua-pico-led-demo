"""
Lua AI Pico 2 W LED Controller

Controls the onboard LED via commands from a Lua AI agent.
Uses the lua_device MicroPython library for MQTT over WebSocket.

The device is self-describing: it sends its command manifest to the
agent at connect time. The agent discovers available commands as tools
automatically — no server-side configuration needed.

Setup:
  1. Copy config.example.py to config.py and fill in your credentials
  2. Upload all .py files to the Pico (main.py, lua_device.py, websocket_mqtt.py, config.py)
  3. The script runs automatically on boot
"""

import network
import machine
import time
import config

from lua_device import LuaDevice

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

# === DEVICE SETUP ===
connect_wifi()

device = LuaDevice(
    agent_id=config.AGENT_ID,
    api_key=config.API_KEY,
    device_name=config.DEVICE_NAME,
)

# === COMMANDS ===
# Each command is self-describing: the name, description, and input schema
# are sent to the agent at connect time. The agent discovers them as tools.

@device.command("led_on", description="Turn the onboard LED on")
def led_on(payload):
    global led_state
    led.on()
    led_state = True
    return {"led": "on"}

@device.command("led_off", description="Turn the onboard LED off")
def led_off(payload):
    global led_state
    led.off()
    led_state = False
    return {"led": "off"}

@device.command("blink",
    description="Blink the LED a number of times",
    inputSchema={
        "type": "object",
        "properties": {
            "times": {"type": "number", "description": "Number of times to blink (1-20)"},
            "delay_ms": {"type": "number", "description": "Delay between blinks in ms (50-1000)"},
        },
    })
def blink(payload):
    global led_state
    times = min(max(int(payload.get("times", 3)), 1), 20)
    delay = min(max(int(payload.get("delay_ms", 200)), 50), 1000)
    for _ in range(times):
        led.on()
        time.sleep_ms(delay)
        led.off()
        time.sleep_ms(delay)
    led_state = False
    return {"blinked": times, "delay_ms": delay}

@device.command("status", description="Get the current LED state and device system info")
def status(payload):
    import gc
    gc.collect()
    return {
        "led": "on" if led_state else "off",
        "free_memory": gc.mem_free(),
        "frequency_mhz": machine.freq() // 1_000_000,
        "uptime_s": time.ticks_ms() // 1000,
    }

# === CONNECT AND RUN ===
print("[pico] Connecting to Lua AI agent...")
device.connect()

# Blink 3 times to signal ready
for _ in range(3):
    led.on()
    time.sleep_ms(100)
    led.off()
    time.sleep_ms(100)

device.run()
