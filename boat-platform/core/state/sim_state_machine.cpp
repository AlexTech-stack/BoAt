#include "state/sim_state_machine.h"

#include <unordered_map>
#include <unordered_set>

namespace boat::core {

namespace {
const std::unordered_map<SimState, std::unordered_set<SimState>> kTransitions = {
    {SimState::IDLE, {SimState::RUNNING}},
    {SimState::RUNNING, {SimState::PAUSED, SimState::STOPPED, SimState::ERROR}},
    {SimState::PAUSED, {SimState::RUNNING, SimState::STOPPED, SimState::ERROR}},
    {SimState::STOPPED, {SimState::IDLE}},
    {SimState::ERROR, {SimState::IDLE}},
};
}  // namespace

bool SimStateMachine::Transition(SimState target) {
  std::vector<Observer> observers;
  SimState from = SimState::ERROR;

  {
    std::lock_guard<std::mutex> lock(mutex_);
    from = current_;
    auto it = kTransitions.find(current_);
    if (it == kTransitions.end() || it->second.find(target) == it->second.end()) {
      return false;
    }
    current_ = target;
    observers = observers_;
  }

  for (const auto& observer : observers) {
    observer(from, target);
  }
  return true;
}

SimState SimStateMachine::Current() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return current_;
}

void SimStateMachine::OnTransition(Observer observer) {
  std::lock_guard<std::mutex> lock(mutex_);
  observers_.push_back(std::move(observer));
}

}  // namespace boat::core
