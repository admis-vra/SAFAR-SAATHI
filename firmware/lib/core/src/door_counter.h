// Directional passenger counting from two IR break-beams (blueprint 3.1, Task_IR_Counter).
// Beam A is on the outside of the door, beam B on the inside: A then B = boarding, B then A = alighting.
// Pure C++ (no Arduino) so it can be unit-tested on a PC.
#pragma once
#include <stdint.h>

class DoorCounter {
 public:
  explicit DoorCounter(uint32_t window_ms = 1500, uint32_t debounce_ms = 60)
      : window_(window_ms), debounce_(debounce_ms) {}

  // A beam was broken at time t (ms). Returns +1 (in), -1 (out) or 0 (nothing counted yet).
  int onBreak(char sensor, uint32_t t) {
    const bool isA = sensor == 'A';
    Edge& self = isA ? a_ : b_;
    Edge& other = isA ? b_ : a_;
    if (self.seen && t - self.lastEdge < debounce_) return 0;  // contact bounce / one body, two edges
    self.seen = true;
    self.lastEdge = t;

    if (other.pending && t - other.pendingAt <= window_) {
      other.pending = false;
      if (isA) { out_++; return -1; }  // B then A
      in_++;
      return +1;                        // A then B
    }
    self.pending = true;                // wait for the other beam; expires after window_
    self.pendingAt = t;
    return 0;
  }

  uint32_t in() const { return in_; }
  uint32_t out() const { return out_; }
  int32_t net() const { return (int32_t)in_ - (int32_t)out_; }
  void reset() { *this = DoorCounter(window_, debounce_); }

 private:
  struct Edge { bool seen = false; uint32_t lastEdge = 0; bool pending = false; uint32_t pendingAt = 0; };
  uint32_t window_, debounce_;
  Edge a_, b_;
  uint32_t in_ = 0, out_ = 0;
};
