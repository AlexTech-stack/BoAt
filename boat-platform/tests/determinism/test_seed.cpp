#include <cassert>

#include "scheduler/sim_clock.h"

int main() {
  boat::core::SimClock a(1234);
  boat::core::SimClock b(1234);
  a.Step(10);
  b.Step(10);
  assert(a.seed() == b.seed());
  assert(a.tick() == b.tick());
  return 0;
}
