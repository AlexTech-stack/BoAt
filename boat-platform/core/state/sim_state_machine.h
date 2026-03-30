#pragma once

#include <functional>
#include <mutex>
#include <vector>

namespace boat::core {

enum class SimState { IDLE, RUNNING, PAUSED, STOPPED, ERROR };

class SimStateMachine {
 public:
  using Observer = std::function<void(SimState from, SimState to)>;

  bool Transition(SimState target);
  [[nodiscard]] SimState Current() const;
  void OnTransition(Observer observer);

 private:
  mutable std::mutex mutex_;
  SimState current_{SimState::IDLE};
  std::vector<Observer> observers_;
};

}  // namespace boat::core
