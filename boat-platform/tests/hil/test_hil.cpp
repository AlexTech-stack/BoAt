#include <cassert>
#include <cstdlib>

int main() {
  const char* enabled = std::getenv("BOAT_HIL_ENABLED");
  if (enabled == nullptr) {
    return 0;
  }
  assert(*enabled != '\0');
  return 0;
}
