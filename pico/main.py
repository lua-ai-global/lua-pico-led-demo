"""
Lua AI Pico 2 W LED Controller

Controls the onboard LED via commands from a Lua AI agent.
Setup: copy config.example.py to config.py, fill in your credentials,
then upload main.py, lua_device.py, and config.py to the Pico.
"""

import machine
import time
import config

from lua_device import LuaDevice

led = machine.Pin("LED", machine.Pin.OUT)
led_state = False

device = LuaDevice(
    agent_id=config.AGENT_ID,
    api_key=config.API_KEY,
    device_name=config.DEVICE_NAME,
    wifi_ssid=config.WIFI_SSID,
    wifi_password=config.WIFI_PASSWORD,
)


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


device.run()
