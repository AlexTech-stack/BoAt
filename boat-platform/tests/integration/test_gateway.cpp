#include <cassert>

#include "scheduler/sim_clock.h"

int main() {
  boat::core::SimClock clock(7);
  clock.Step();
  assert(clock.seed() == 7);
  assert(clock.tick() == 1);
  return 0;
}
