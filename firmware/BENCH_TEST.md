# Bus device: wiring and bench test (for Rahul)

> Status: **the core logic is unit-tested on a PC** (`test/host`: door counting, offline buffer, payload,
> clock). **The ESP32 code (`src/main.cpp`) has not been compiled or run on hardware yet.** Expect to fix a
> few things on first build. Work through this list in order and note real results.

## Parts

ESP32 DevKit (esp32dev) · NEO-6M GPS + antenna · SIM800L + antenna + **2G SIM (Airtel or Vi; Jio has no 2G)** ·
2 × IR break-beam pairs (emitter + receiver, e.g. 5 mm 940 nm or E18-type) · push button (SOS) · 4.0 V supply for
the SIM800L (see power) · 1000 µF capacitor.

## Wiring

| From | To (ESP32) | Notes |
|---|---|---|
| NEO-6M TX | GPIO16 (RX2) | GPS at 9600 baud |
| NEO-6M RX | GPIO17 (TX2) | |
| NEO-6M VCC / GND | 3.3 V or 5 V (check board) / GND | |
| SIM800L TXD | GPIO26 (RX1) | |
| SIM800L RXD | GPIO27 (TX1) | SIM800L logic is ~2.8 V; ESP32 3.3 V TX is usually OK, add a divider if unsure |
| SIM800L RST | GPIO5 | |
| SIM800L VCC / GND | **3.7–4.2 V, 2 A peak** / common GND | **Not** the ESP32's 3.3 V or 5 V pin |
| IR receiver A (door side) | GPIO32 | output must go **LOW when the beam is broken** (internal pull-up on) |
| IR receiver B (aisle side) | GPIO33 | ~20–30 cm from A, towards the aisle |
| SOS button | GPIO25 → GND | internal pull-up; pressed = LOW |
| Status LED | GPIO2 (on-board) | solid = online, blinking = offline (buffering) |

**Power is the #1 SIM800L failure.** It draws 2 A bursts while transmitting. Use a LiPo cell or a buck
converter set to 4.0 V, with the 1000 µF capacitor right at its VCC pin. If it keeps rebooting or loses the network
when sending, it's the power supply. In the bus, feed everything from the 12/24 V supply through proper buck
converters with a fuse.

## Software setup

```bash
# PC unit tests for the portable logic (no hardware needed)
cd firmware/test/host
g++ -std=c++17 -Wall -Wextra -Werror -I ../../lib/core/src test_core.cpp -o test_core && ./test_core
```

1. Install PlatformIO (VS Code extension "PlatformIO IDE", or `pip install platformio`).
2. Copy `include/secrets.example.h` to `include/secrets.h` and set `DEVICE_ID` (e.g. `B-14`, must match
   `buses.mqtt_device_id`), the MQTT password, and the APN for your SIM.
3. In `include/config.h` set `MQTT_HOST` to a broker **reachable from the internet** (e.g. Mosquitto on a cloud VM
   using `tools/mosquitto/`). A broker on a laptop on campus Wi-Fi can't be reached over GSM.
4. `pio run -t upload`, then `pio device monitor`.

## Bench checklist (tick and write down real numbers)

| # | Test | Expected | Result |
|---|---|---|---|
| 1 | Boot | `SafarSaathi device B-14 started` on serial | |
| 2 | GPS indoors → near a window | `fix=1 sats>=4` within a few minutes; time printed as a 2026 Unix timestamp | |
| 3 | No GPS fix but GSM up | Samples still sent with `sats=0` (backend keeps last position); time from network | |
| 4 | Walk through A then B, 10 times | `in` +10, `out` unchanged | |
| 5 | Walk through B then A, 10 times | `out` +10 | |
| 6 | Wave a hand in A only | no count | |
| 7 | Two people shoulder-to-shoulder | **Expect miscounts.** Note how many. This is why the camera fusion (M4) exists | |
| 8 | Hold SOS 0.1 s | `sos=1` for ~10 s | |
| 9 | GSM up | LED solid, `online=1`; bus moves on the website's `/office` map | |
| 10 | Pull the GSM antenna / SIM for 1 min | LED blinks, `backlog` grows by ~30 | |
| 11 | Restore | backlog drains to 0; `/office` shows the trip with no false "last bus" alert | |
| 12 | Leave offline > 15 min | `dropped` starts counting (oldest data lost, newest kept) | |
| 13 | Unplug for 2 min while the backend runs | `/office` shows "Device offline" after ~30 s, cleared when back | |

**Accuracy (for the pitch):** on a real bus, count boarding/alighting by hand for at least 3 stops and compare with
`in`/`out`. Report the real percentage. Until then, IR accuracy is **not measured**.

## Known limits

- **2G only:** SIM800L needs a 2G network, and operators are retiring 2G. For a real deployment, consider a
  4G (LTE Cat-1 / Cat-M) module such as SIM7600 / A7670. TinyGSM supports these, so mostly a config change.
- **No TLS:** SIM800L's built-in TLS is outdated, so MQTT runs unencrypted here. A pilot needs a private APN, a VPN,
  or a 4G module with modern TLS.
- **Single doorway, two beams:** people walking side by side or lingering in the beam are miscounted. Camera fusion
  re-anchors the count when the bus is at rest.
- **Buffer lives in RAM:** 15 min, and it's lost on a power cut. Storing it in flash (SPIFFS/LittleFS) is the next step if needed.
