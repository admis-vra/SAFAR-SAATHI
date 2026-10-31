// Non-secret settings. Per-device secrets (device id, MQTT password, APN login) go in secrets.h.
#pragma once

// ---- pins (see BENCH_TEST.md for wiring) ----
#define PIN_GPS_RX 16       // ESP32 RX2  <- NEO-6M TX
#define PIN_GPS_TX 17       // ESP32 TX2  -> NEO-6M RX
#define PIN_MODEM_RX 26     // ESP32 RX1  <- SIM800L TXD
#define PIN_MODEM_TX 27     // ESP32 TX1  -> SIM800L RXD
#define PIN_MODEM_RST 5     // SIM800L RST (active low)
#define PIN_IR_A 32         // outer beam (door side). Receiver output LOW when the beam is broken.
#define PIN_IR_B 33         // inner beam (aisle side)
#define PIN_SOS 25          // SOS push button to GND (internal pull-up)
#define PIN_STATUS_LED 2    // on-board LED: solid = MQTT connected, blinking = offline

// ---- timing ----
#define PUBLISH_EVERY_MS 2000      // blueprint: every 2 s
#define IR_WINDOW_MS 1500          // max gap between A and B for one person
#define IR_DEBOUNCE_MS 60
#define SOS_DEBOUNCE_MS 50
#define SOS_LATCH_MS 10000         // keep sos_active true for 10 s so a short press is never missed
#define GPS_FIX_MAX_AGE_MS 5000    // older fix = treat as no fix
#define BACKLOG_CHUNK 15           // ~190 B each: keeps a message well under PubSubClient's 4 KB buffer
#define WATCHDOG_S 90              // modem calls can block ~30 s; reboot if the publish task hangs longer

// ---- MQTT broker (reachable from the internet over GPRS, not a laptop on campus Wi-Fi) ----
#define MQTT_HOST "broker.example.com"
#define MQTT_PORT 1883  // SIM800L's built-in TLS is old (TLS 1.0); see tools/mosquitto/mosquitto.conf notes
#define TOPIC_PREFIX "safarsaathi/bus/"
