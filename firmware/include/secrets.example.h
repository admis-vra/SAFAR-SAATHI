// Copy to secrets.h (git-ignored) and fill in per bus. Never commit secrets.h.
#pragma once

#define DEVICE_ID "B-14"              // must match buses.mqtt_device_id and the MQTT username
#define MQTT_USER DEVICE_ID           // Mosquitto ACL: a device can only publish its own topic
#define MQTT_PASSWORD "change-me"     // mosquitto_passwd -b passwords B-14 <password>

// SIM800L is 2G only: use an Airtel or Vi SIM (Jio has no 2G). APN examples: "airtelgprs.com", "www".
#define GPRS_APN "airtelgprs.com"
#define GPRS_USER ""
#define GPRS_PASS ""
#define SIM_PIN ""
