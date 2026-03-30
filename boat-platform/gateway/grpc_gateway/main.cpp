#include <iostream>

#include "scheduler/sim_clock.h"

int main() {
  boat::core::SimClock clock(42);
  clock.Step();
  std::cout << "boat_gateway started at tick " << clock.tick() << "\n";
  return 0;
}
