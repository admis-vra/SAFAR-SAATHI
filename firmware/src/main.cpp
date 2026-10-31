// SafarSaathi bus device (blueprint 3): ESP32 + NEO-6M GPS + 2 IR break-beams + SIM800L, FreeRTOS tasks:
//   Task_GPS          reads NMEA, keeps position/speed/satellites and the clock
//   Task_IR_Counter   directional door counting (A->B in, B->A out) + SOS button
//   Task_MQTT_Publish every 2 s publishes the blueprint payload; while offline it buffers and, on
//                     reconnect, sends the backlog in chunks to .../backlog
// Logic that can be tested without hardware lives in lib/core (see test/host).
#define TINY_GSM_MODEM_SIM800
#define TINY_GSM_RX_BUFFER 1024
#include <Arduino.h>
#include <PubSubClient.h>
#include <TinyGPSPlus.h>
#include <TinyGsmClient.h>
#include <esp_arduino_version.h>
#include <esp_task_wdt.h>

#include "config.h"
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Copy include/secrets.example.h to include/secrets.h and fill it in for this bus"
#endif
#include "door_counter.h"
#include "payload.h"
#include "ring_buffer.h"

static const char* TOPIC_TELEMETRY = TOPIC_PREFIX DEVICE_ID "/telemetry";
static const char* TOPIC_BACKLOG = TOPIC_PREFIX DEVICE_ID "/backlog";

HardwareSerial gpsSerial(2);
HardwareSerial modemSerial(1);
TinyGPSPlus gps;
TinyGsm modem(modemSerial);
TinyGsmClient net(modem);
PubSubClient mqtt(net);

struct IrEvent { char sensor; uint32_t t; };

struct Shared {
  double lat = 0, lon = 0;
  float speed = 0;
  uint8_t sats = 0;
  bool fix = false;
  bool timeKnown = false;
  int64_t epochOffset = 0;  // unix seconds = millis()/1000 + epochOffset
  uint32_t sosUntil = 0;    // millis() until which sos_active is reported
};

static Shared state;
static SemaphoreHandle_t lock;
static QueueHandle_t irEvents;
static DoorCounter door(IR_WINDOW_MS, IR_DEBOUNCE_MS);
static RingBuffer<Sample, 450> backlog;  // 15 min at 2 s (~18 KB RAM); oldest dropped beyond that

struct Locked {
  Locked() { xSemaphoreTake(lock, portMAX_DELAY); }
  ~Locked() { xSemaphoreGive(lock); }
};

static void IRAM_ATTR queueBreak(char sensor) {
  IrEvent e{sensor, millis()};
  BaseType_t woke = pdFALSE;
  xQueueSendFromISR(irEvents, &e, &woke);
  if (woke) portYIELD_FROM_ISR();
}
static void IRAM_ATTR onBeamA() { queueBreak('A'); }
static void IRAM_ATTR onBeamB() { queueBreak('B'); }

static void setClock(uint32_t unix) {
  Locked l;
  state.epochOffset = (int64_t)unix - (int64_t)(millis() / 1000);
  state.timeKnown = true;
}

// ---------------- Task_GPS ----------------
static void taskGps(void*) {
  gpsSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  uint32_t lastClockSync = 0;
  for (;;) {
    while (gpsSerial.available()) gps.encode(gpsSerial.read());
    const bool fix = gps.location.isValid() && gps.location.age() < GPS_FIX_MAX_AGE_MS;
    {
      Locked l;
      state.fix = fix;
      if (fix) {
        state.lat = gps.location.lat();
        state.lon = gps.location.lng();
      }
      state.speed = gps.speed.isValid() ? (float)gps.speed.kmph() : 0.0f;
      state.sats = gps.satellites.isValid() ? (uint8_t)gps.satellites.value() : 0;
    }
    // GPS time is UTC and very accurate: prefer it over the modem's network time, re-sync every minute.
    if (gps.date.isValid() && gps.time.isValid() && gps.time.age() < 2000 && gps.date.year() >= 2024 &&
        (lastClockSync == 0 || millis() - lastClockSync > 60000)) {
      setClock(unixTime(gps.date.year(), gps.date.month(), gps.date.day(), gps.time.hour(), gps.time.minute(),
                        gps.time.second()));
      lastClockSync = millis();
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

// ---------------- Task_IR_Counter (+ SOS button) ----------------
static void taskIr(void*) {
  pinMode(PIN_IR_A, INPUT_PULLUP);
  pinMode(PIN_IR_B, INPUT_PULLUP);
  pinMode(PIN_SOS, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_IR_A), onBeamA, FALLING);
  attachInterrupt(digitalPinToInterrupt(PIN_IR_B), onBeamB, FALLING);
  IrEvent e;
  uint32_t sosLowSince = 0;
  for (;;) {
    while (xQueueReceive(irEvents, &e, pdMS_TO_TICKS(20)) == pdTRUE) {
      Locked l;
      door.onBreak(e.sensor, e.t);
    }
    const uint32_t now = millis();
    if (digitalRead(PIN_SOS) == LOW) {
      if (sosLowSince == 0) sosLowSince = now | 1;
      else if (now - sosLowSince >= SOS_DEBOUNCE_MS) {
        Locked l;
        state.sosUntil = now + SOS_LATCH_MS;
      }
    } else {
      sosLowSince = 0;
    }
  }
}

// ---------------- Task_MQTT_Publish ----------------
static bool buildSample(Sample& s) {
  Locked l;
  if (!state.timeKnown) return false;  // the backend needs a real timestamp; wait for GPS or network time
  s.ts = (uint32_t)((int64_t)(millis() / 1000) + state.epochOffset);
  s.lat = state.fix ? state.lat : 0.0;
  s.lon = state.fix ? state.lon : 0.0;
  s.sats = state.fix ? state.sats : 0;  // 0 sats tells the backend "no fix, keep the last position"
  s.speed = state.speed;
  s.in = door.in();
  s.out = door.out();
  s.net = door.net();
  s.sos = (int32_t)(state.sosUntil - millis()) > 0;
  return true;
}

static void syncClockFromModem() {
  {
    Locked l;
    if (state.timeKnown) return;
  }
  int y, mo, d, h, mi, s;
  float tz;
  if (modem.getNetworkTime(&y, &mo, &d, &h, &mi, &s, &tz) && y >= 2024) {
    setClock(unixTime(y, mo, d, h, mi, s) - (int32_t)(tz * 3600));  // network time is local; tz in hours
  }
}

static bool ensureOnline() {
  static uint32_t nextTry = 0;
  if (mqtt.connected()) return true;
  if ((int32_t)(millis() - nextTry) < 0) return false;
  nextTry = millis() + 15000;  // don't hammer a weak 2G link
  if (!modem.isNetworkConnected() && !modem.waitForNetwork(20000L)) return false;
  if (!modem.isGprsConnected() && !modem.gprsConnect(GPRS_APN, GPRS_USER, GPRS_PASS)) return false;
  syncClockFromModem();
  return mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASSWORD);
}

static void flushBacklog() {
  static char buf[4096];
  while (!backlog.empty() && mqtt.connected()) {
    const size_t n = formatBacklog(buf, sizeof buf, DEVICE_ID, backlog, BACKLOG_CHUNK);
    if (n == 0 || !mqtt.publish(TOPIC_BACKLOG, buf)) return;
    backlog.popFront(n);
    mqtt.loop();
    esp_task_wdt_reset();
  }
}

static void taskPublish(void*) {
  esp_task_wdt_add(nullptr);
  modemSerial.begin(9600, SERIAL_8N1, PIN_MODEM_RX, PIN_MODEM_TX);
  pinMode(PIN_MODEM_RST, OUTPUT);
  digitalWrite(PIN_MODEM_RST, LOW);
  vTaskDelay(pdMS_TO_TICKS(200));
  digitalWrite(PIN_MODEM_RST, HIGH);
  vTaskDelay(pdMS_TO_TICKS(3000));
  modem.restart();
  if (strlen(SIM_PIN)) modem.simUnlock(SIM_PIN);
  modem.sendAT("+CLTS=1");  // let the network set the modem clock (fallback when GPS has no time yet)
  modem.waitResponse();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(4096);
  mqtt.setKeepAlive(30);

  char buf[384];
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(PUBLISH_EVERY_MS));
    esp_task_wdt_reset();
    Sample s;
    const bool have = buildSample(s);
    const bool online = ensureOnline();
    digitalWrite(PIN_STATUS_LED, online ? HIGH : (millis() / 500) % 2);
    if (online) {
      flushBacklog();
      if (have && (formatSample(buf, sizeof buf, DEVICE_ID, s) < 0 || !mqtt.publish(TOPIC_TELEMETRY, buf))) {
        backlog.push(s);
      }
      mqtt.loop();
    } else if (have) {
      backlog.push(s);
    }
    if (have) {
      Serial.printf("[%lu] fix=%d sats=%u in=%lu out=%lu net=%ld sos=%d online=%d backlog=%u dropped=%lu\n",
                    (unsigned long)s.ts, s.sats >= 4, s.sats, (unsigned long)s.in, (unsigned long)s.out,
                    (long)s.net, s.sos, online, (unsigned)backlog.size(), (unsigned long)backlog.dropped());
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_STATUS_LED, OUTPUT);
  lock = xSemaphoreCreateMutex();
  irEvents = xQueueCreate(64, sizeof(IrEvent));
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  const esp_task_wdt_config_t wdt = {.timeout_ms = WATCHDOG_S * 1000, .idle_core_mask = 0, .trigger_panic = true};
  esp_task_wdt_reconfigure(&wdt);
#else
  esp_task_wdt_init(WATCHDOG_S, true);
#endif
  xTaskCreatePinnedToCore(taskGps, "Task_GPS", 4096, nullptr, 2, nullptr, 1);
  xTaskCreatePinnedToCore(taskIr, "Task_IR_Counter", 4096, nullptr, 3, nullptr, 1);
  xTaskCreatePinnedToCore(taskPublish, "Task_MQTT_Publish", 12288, nullptr, 1, nullptr, 0);
  Serial.printf("SafarSaathi device %s started\n", DEVICE_ID);
}

void loop() { vTaskDelete(nullptr); }  // all work happens in the FreeRTOS tasks
