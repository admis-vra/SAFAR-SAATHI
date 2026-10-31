// Blueprint 3.2 MQTT payload, built with snprintf (no heap, no JSON library) so it's testable on a PC.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

struct Sample {
  uint32_t ts;      // Unix seconds (UTC)
  double lat, lon;  // 0,0 + sats < 4 means "no fix"; the backend keeps the last position then
  float speed;      // km/h
  uint8_t sats;
  uint32_t in, out;
  int32_t net;
  bool sos;
};

// Returns bytes written (excluding NUL), or -1 if it didn't fit.
inline int formatSample(char* buf, size_t n, const char* busId, const Sample& s) {
  int w = snprintf(buf, n,
                   "{\"bus_id\":\"%s\",\"timestamp\":%lu,"
                   "\"gps\":{\"lat\":%.6f,\"lon\":%.6f,\"speed\":%.1f,\"sats\":%u},"
                   "\"pax\":{\"in_count\":%lu,\"out_count\":%lu,\"net_current\":%ld},"
                   "\"sos_active\":%s}",
                   busId, (unsigned long)s.ts, s.lat, s.lon, (double)s.speed, (unsigned)s.sats,
                   (unsigned long)s.in, (unsigned long)s.out, (long)s.net, s.sos ? "true" : "false");
  return (w < 0 || (size_t)w >= n) ? -1 : w;
}

// JSON array of up to maxItems samples starting at the oldest; stops early if the buffer would overflow.
// Returns how many samples were written (0 = nothing fit).
template <typename Buffer>
size_t formatBacklog(char* buf, size_t n, const char* busId, const Buffer& samples, size_t maxItems) {
  if (n < 3) return 0;
  size_t used = 0, items = 0;
  buf[used++] = '[';
  for (size_t i = 0; i < samples.size() && items < maxItems; i++) {
    char one[320];
    int w = formatSample(one, sizeof one, busId, samples.at(i));
    if (w < 0 || used + (items ? 1 : 0) + (size_t)w + 2 > n) break;
    if (items) buf[used++] = ',';
    for (int k = 0; k < w; k++) buf[used++] = one[k];
    items++;
  }
  buf[used++] = ']';
  buf[used] = '\0';
  return items;
}

// Days from 1970-01-01 for a proleptic Gregorian date (Howard Hinnant's algorithm), so GPS/modem time
// converts to Unix time without relying on the ESP32's timezone settings.
inline int64_t daysFromCivil(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int64_t era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int64_t)doe - 719468;
}

inline uint32_t unixTime(int y, int mo, int d, int h, int mi, int s) {
  return (uint32_t)(daysFromCivil(y, (unsigned)mo, (unsigned)d) * 86400 + h * 3600 + mi * 60 + s);
}
