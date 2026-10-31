// Host-side tests for the firmware's portable logic (no ESP32 needed):
//   g++ -std=c++17 -Wall -Wextra -Werror -I ../../lib/core/src test_core.cpp -o test_core && ./test_core
// Prints sample payloads on the last lines so the backend's validator can check them (see BENCH_TEST.md).
#include <stdio.h>
#include <string.h>

#include "door_counter.h"
#include "payload.h"
#include "ring_buffer.h"

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) { printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } \
  } while (0)

static void test_directions() {
  DoorCounter c(1500, 60);
  CHECK(c.onBreak('A', 1000) == 0);
  CHECK(c.onBreak('B', 1300) == +1);  // A then B: boarding
  CHECK(c.onBreak('B', 5000) == 0);
  CHECK(c.onBreak('A', 5400) == -1);  // B then A: alighting
  CHECK(c.in() == 1 && c.out() == 1 && c.net() == 0);
}

static void test_debounce_and_window() {
  DoorCounter c(1500, 60);
  c.onBreak('A', 1000);
  CHECK(c.onBreak('A', 1030) == 0);   // bounce inside 60 ms ignored
  CHECK(c.onBreak('B', 1200) == +1);
  c.onBreak('A', 10000);              // someone steps in and backs out: only A
  CHECK(c.onBreak('B', 12000) == 0);  // B 2 s later is outside the 1.5 s window: not a boarding
  CHECK(c.in() == 1);
}

static void test_queue_of_people() {
  DoorCounter c(1500, 60);
  for (uint32_t i = 0; i < 30; i++) {  // 30 people boarding 1.2 s apart
    c.onBreak('A', 100000 + i * 1200);
    c.onBreak('B', 100000 + i * 1200 + 400);
  }
  CHECK(c.in() == 30 && c.out() == 0);
}

static void test_ring_buffer() {
  RingBuffer<int, 4> r;
  for (int i = 1; i <= 6; i++) r.push(i);  // 1,2 dropped
  CHECK(r.size() == 4 && r.at(0) == 3 && r.at(3) == 6 && r.dropped() == 2);
  r.popFront(3);
  CHECK(r.size() == 1 && r.at(0) == 6);
  r.popFront(10);
  CHECK(r.empty());
}

static void test_unix_time() {
  CHECK(unixTime(1970, 1, 1, 0, 0, 0) == 0);
  CHECK(unixTime(2000, 3, 1, 0, 0, 0) == 951868800);       // leap-year boundary
  CHECK(unixTime(2026, 10, 31, 8, 0, 0) == 1793433600);
}

static void test_payload_and_backlog(char* one, size_t oneN, char* many, size_t manyN) {
  Sample s{1793433600, 30.316512, 78.032212, 42.5f, 8, 45, 12, 33, false};
  CHECK(formatSample(one, oneN, "B-402", s) > 0);
  CHECK(strstr(one, "\"net_current\":33") && strstr(one, "\"sos_active\":false"));
  char tiny[40];
  CHECK(formatSample(tiny, sizeof tiny, "B-402", s) == -1);  // refuses to truncate

  RingBuffer<Sample, 8> buf;
  for (uint32_t i = 0; i < 5; i++) {
    Sample x = s;
    x.ts += i * 2;
    x.sos = i == 3;
    buf.push(x);
  }
  CHECK(formatBacklog(many, manyN, "B-402", buf, 20) == 5);
  size_t fit = formatBacklog(many, 400, "B-402", buf, 20);   // only whole samples that fit
  CHECK(fit >= 1 && fit < 5 && strlen(many) < 400 && many[strlen(many) - 1] == ']');
  CHECK(formatBacklog(many, manyN, "B-402", buf, 2) == 2);   // chunk size respected
  formatBacklog(many, manyN, "B-402", buf, 20);
}

int main() {
  static char one[512], many[4096];
  test_directions();
  test_debounce_and_window();
  test_queue_of_people();
  test_ring_buffer();
  test_unix_time();
  test_payload_and_backlog(one, sizeof one, many, sizeof many);
  printf("%s\n", failures ? "SOME TESTS FAILED" : "all host tests passed");
  printf("%s\n%s\n", one, many);
  return failures ? 1 : 0;
}
