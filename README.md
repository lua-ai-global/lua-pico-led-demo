# Pico LED Controller — AI Agent Controls Real Hardware

Control a Raspberry Pi Pico 2 W's onboard LED by talking to an AI agent. Send a message like "blink the LED 5 times" via the terminal or WhatsApp, and the LED blinks on your desk.

The device is **self-describing** — it tells the agent what commands it supports at connect time. No server configuration, no redeployment. Plug it in, connect to WiFi, and your agent discovers it automatically.

Built with [Lua AI](https://heylua.ai) Devices, the [`lua_device` MicroPython library](https://docs.heylua.ai/devices/micropython-client), and MicroPython.

## What You'll Build

```
You (terminal or WhatsApp)
  → Lua AI Agent (cloud)
    → MQTT over WebSocket
      → Raspberry Pi Pico 2 W
        → LED turns on
```

The Pico connects to your agent via MQTT over WebSocket (through `wss://mqtt.heylua.ai/mqtt`). It sends a manifest of its commands — `led_on`, `led_off`, `blink`, `status` — and your agent picks them up as tools it can use.

## Prerequisites

| What | Why |
|------|-----|
| [Raspberry Pi Pico 2 W](https://www.raspberrypi.com/products/raspberry-pi-pico-2/) | Must be the **W** variant (with WiFi). ~$7 |
| Micro-USB cable | Data-capable (not charge-only) |
| 2.4 GHz WiFi network | Pico W does not support 5 GHz |
| [Node.js 18+](https://nodejs.org/) | For the Lua CLI |
| [Python 3](https://python.org/) | For `mpremote` (Pico file transfer tool) |

## Part 1: Create Your Lua AI Agent

### 1.1 Install the Lua CLI

```bash
npm install -g lua-cli
```

### 1.2 Authenticate

```bash
lua auth configure
```

This will open your browser to sign in (or create an account) at [heylua.ai](https://heylua.ai). Your API key is saved to `~/.lua/config.json`.

### 1.3 Create an agent project

```bash
mkdir pico-agent && cd pico-agent
lua init --agent-name "Pico LED Controller"
```

Select your organization when prompted. This creates the project files including `src/index.ts` and `lua.skill.yaml`.

### 1.4 Set the agent persona

Open `src/index.ts` and replace the persona with:

```typescript
import { LuaAgent } from "lua-cli";

const agent = new LuaAgent({
    name: 'Pico LED Controller',
    persona: `You are an IoT controller agent. You can control physical devices connected to you. Use the device tools available to fulfill user requests. If a command fails or a device is offline, let the user know. Keep responses short and friendly.`,
    model: 'google/gemini-2.5-flash',
    skills: [],
});
```

You don't need to list the LED commands in the persona — the Pico sends them at connect time and the agent discovers them automatically.

### 1.5 Deploy

```bash
lua push all --force --auto-deploy
```

### 1.6 Note your credentials

You'll need two values for the Pico:

- **Agent ID**: Open `lua.skill.yaml` — it's the `agentId` field (e.g. `baseAgent_agent_1234_abcde`)
- **API Key**: Open `~/.lua/config.json` — it's the `apiKey` field (e.g. `api_abc123...`)

## Part 2: Flash MicroPython on the Pico

1. Hold the **BOOTSEL** button on the Pico 2 W
2. While holding, plug in the USB cable
3. Release BOOTSEL — the Pico appears as a USB drive (e.g. `RPI-RP2` or `RP2350`)
4. Download the latest MicroPython firmware for Pico 2 W from [micropython.org/download/RPI_PICO2_W](https://micropython.org/download/RPI_PICO2_W/)
5. Drag the `.uf2` file onto the Pico USB drive
6. The Pico reboots automatically with MicroPython

Verify it worked:

```bash
python3 -m mpremote connect auto exec "import sys; print(sys.implementation)"
```

You should see `micropython` and `Raspberry Pi Pico 2 W` in the output.

## Part 3: Install the MQTT Library

The Pico needs the `umqtt.simple` library for MQTT communication:

```bash
python3 -m mpremote connect auto mip install umqtt.simple
```

> **Note:** If you don't have `mpremote`, install it with `pip3 install mpremote`.

## Part 4: Configure the Pico

Copy the config template and fill in your values:

```bash
cd pico/
cp config.example.py config.py
```

Edit `config.py`:

```python
WIFI_SSID = "MyWiFi"              # Your 2.4 GHz WiFi network name
WIFI_PASSWORD = "MyPassword"       # Your WiFi password
AGENT_ID = "baseAgent_agent_..."   # From lua.skill.yaml (step 1.6)
API_KEY = "api_..."                # From ~/.lua/config.json (step 1.6)
DEVICE_NAME = "pico-led"           # Keep this as-is
```

> **Important:** Never commit `config.py` to git — it contains your WiFi password and API key. The `.gitignore` already excludes it.

## Part 5: Upload and Run

Upload the files to the Pico:

```bash
python3 -m mpremote connect auto cp main.py :main.py
python3 -m mpremote connect auto cp lua_device.py :lua_device.py
python3 -m mpremote connect auto cp websocket_mqtt.py :websocket_mqtt.py
python3 -m mpremote connect auto cp config.py :config.py
```

Reset the Pico to run `main.py`:

```bash
python3 -m mpremote connect auto reset
```

The Pico will:
1. Connect to WiFi
2. Establish an MQTT WebSocket connection to `mqtt.heylua.ai`
3. Send its command manifest to your agent
4. Blink 3 times to signal it's ready
5. Wait for commands

## Part 6: Test It

Go back to your agent project directory and chat:

```bash
cd ../pico-agent
lua chat --env production -m "turn on the LED"
lua chat --env production -m "blink the LED 5 times"
lua chat --env production -m "what is the device status?"
lua chat --env production -m "turn off the LED"
```

The LED on the Pico should respond to each command in real time.

## Bonus: Control via WhatsApp

You can connect your agent to WhatsApp and control the LED from your phone:

1. Save **+1 302 377 8932** as a contact (this is the Lua AI WhatsApp number)
2. Send this exact message:

   ```
   link-me-to:YOUR_AGENT_ID
   ```

   Replace `YOUR_AGENT_ID` with your agent ID from `lua.skill.yaml`.

3. Once linked, send messages like:
   - "Turn on the LED"
   - "Blink 3 times"
   - "What's the device status?"

## How It Works

```
┌──────────┐     ┌────────────────┐     ┌──────────┐     ┌──────────────┐
│ You      │────>│ Lua AI Agent   │────>│ MQTT     │────>│ Pico 2 W     │
│ (chat)   │<────│ (cloud)        │<────│ Broker   │<────│ (your desk)  │
└──────────┘     └────────────────┘     └──────────┘     └──────────────┘
                                         wss://mqtt.heylua.ai/mqtt
```

1. **You** send a natural language message ("blink the LED 3 times")
2. **The agent** interprets it and calls the `blink` tool with `{times: 3}`
3. **The platform** routes the command via MQTT over WebSocket to the Pico
4. **The Pico** executes the command and returns `{blinked: 3, delay_ms: 200}`
5. **The agent** responds: "The LED blinked 3 times"

The Pico is **self-describing**: at connect time, it publishes a JSON manifest listing its commands (`led_on`, `led_off`, `blink`, `status`) with descriptions and input schemas. The agent discovers these as tools automatically — you never configure commands on the server.

## Troubleshooting

**WiFi connection fails:**
- Pico W only supports 2.4 GHz (not 5 GHz)
- Double-check SSID and password in `config.py` (case-sensitive)
- Move the Pico closer to the router

**MQTT connection fails (CONNACK error):**
- Verify your `AGENT_ID` and `API_KEY` in `config.py`
- Make sure the agent has been pushed (`lua push all --force --auto-deploy`)
- Ensure port 443 is not blocked by your network

**WebSocket handshake fails (503):**
- The MQTT broker may be temporarily unavailable — wait and retry
- The Pico will auto-reconnect on failure

**Device connects but agent says "device is offline":**
- The device may have connected before the agent was deployed
- Reset the Pico to trigger a fresh connection

**mpremote: "failed to access" error:**
- Close any other serial monitors (Thonny, screen, etc.)
- On macOS: `lsof /dev/tty.usbmodem*` to find and kill other processes

## Learn More

- [Lua Devices Documentation](https://docs.heylua.ai/devices/overview)
- [Self-Describing Commands](https://docs.heylua.ai/devices/self-describing-commands)
- [MQTT Protocol Reference](https://docs.heylua.ai/devices/mqtt-client)
- [Build Your Own Client](https://docs.heylua.ai/devices/build-your-own)
- [Node.js Device Client](https://www.npmjs.com/package/@lua-ai-global/device-client) (`npm install @lua-ai-global/device-client`)
- [Python Device Client](https://pypi.org/project/lua-device-client/) (`pip install lua-device-client`)

## License

MIT
