#include <cassert>

#include "scheduler/sim_clock.h"

int main() {
  boat::core::SimClock clock;
  clock.Step(2);
  assert(clock.tick() == 2);
  return 0;
}
