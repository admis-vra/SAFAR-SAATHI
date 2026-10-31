// Fixed-size buffer for telemetry while the GSM link is down (blueprint 6: network partition).
// When full, the oldest sample is dropped so the newest data always survives.
#pragma once
#include <stddef.h>
#include <stdint.h>

template <typename T, size_t N>
class RingBuffer {
 public:
  void push(const T& v) {
    if (count_ == N) {  // full: overwrite oldest
      head_ = (head_ + 1) % N;
      count_--;
      dropped_++;
    }
    data_[(head_ + count_) % N] = v;
    count_++;
  }
  const T& at(size_t i) const { return data_[(head_ + i) % N]; }  // 0 = oldest
  void popFront(size_t n) {
    if (n > count_) n = count_;
    head_ = (head_ + n) % N;
    count_ -= n;
  }
  size_t size() const { return count_; }
  bool empty() const { return count_ == 0; }
  uint32_t dropped() const { return dropped_; }
  static constexpr size_t capacity() { return N; }

 private:
  T data_[N];
  size_t head_ = 0, count_ = 0;
  uint32_t dropped_ = 0;
};
