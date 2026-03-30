#pragma once

#include <cstdint>
#include <random>

namespace boat::core {

class DeterminismEngine {
 public:
  explicit DeterminismEngine(std::uint64_t seed);

  void BeforeTick(std::uint64_t tick);
  [[nodiscard]] std::uint64_t NextRandom();
  [[nodiscard]] std::uint64_t Seed() const { return seed_; }

 private:
  std::uint64_t seed_;
  std::uint64_t last_tick_;
  std::mt19937_64 rng_;
};

}  // namespace boat::core
