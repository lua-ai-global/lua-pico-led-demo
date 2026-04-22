# config.py — Pico LED Controller credentials
#
# Copy this file to config.py and fill in your values:
#   cp config.example.py config.py
#
# IMPORTANT: Never commit config.py to version control.

# Your WiFi network (must be 2.4 GHz — Pico W does not support 5 GHz)
WIFI_SSID = "YOUR_WIFI_NETWORK"
WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"

# Your Lua AI agent credentials
# Agent ID: found in lua.skill.yaml after running `lua init`
# API Key:  found in ~/.lua/config.json after running `lua auth configure`
AGENT_ID = "baseAgent_agent_XXXX_XXXX"
API_KEY = "api_XXXX"

# Device name (must match what your agent expects)
DEVICE_NAME = "pico-led"
