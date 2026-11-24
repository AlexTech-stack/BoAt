# TestSet: Simulation

System-level tests for the simulation lifecycle and the determinism guarantee.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0`; at least one
valid scenario available (`boat scenario list`).

---

### TC_Simulation_001_create_and_start

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- Scenario `my_scenario` exists

**TestSteps:**
1. `boat sim create --scenario my_scenario` (note the returned simulation id)
2. `boat sim start <id>`
3. `boat sim state <id>`

**Expected:**
- Create returns a simulation id; state transitions IDLE → RUNNING
- Scenario-declared traffic appears on the bus (`candump vcan0`)

---

### TC_Simulation_002_pause_resume

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- A running simulation producing cyclic traffic

**TestSteps:**
1. `boat sim pause <id>` — watch `candump vcan0`
2. `boat sim start <id>` (resume)

**Expected:**
- While paused: no simulation traffic on the bus, state = PAUSED
- After resume: traffic continues, state = RUNNING

---

### TC_Simulation_003_step_exact_ticks

**TestSets:** [Simulation], [CLI], [Determinism]

**Preconditions:**
- A paused simulation whose scenario sends one frame every N ticks

**TestSteps:**
1. `boat sim step <id> --ticks 500`
2. Count the frames that appeared during the step (candump timestamped log)

**Expected:**
- Exactly the number of frames corresponding to 500 ticks is emitted, then the
  simulation is paused again — no free-running drift

---

### TC_Simulation_004_stop_and_cleanup

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- A running simulation with simulation-scoped plugins loaded

**TestSteps:**
1. `boat sim stop <id>`
2. `boat sim list`
3. `boat plugin list`

**Expected:**
- Simulation transitions to STOPPED and disappears from (or is marked stopped in) the list
- Simulation-scoped plugins are torn down; always-on node plugins remain loaded

---

### TC_Simulation_005_list_and_watch

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- Two simulations created, one running

**TestSteps:**
1. `boat sim list`
2. `boat sim watch <running-id>` for a few seconds

**Expected:**
- List shows both simulations with correct states
- Watch streams live state/tick updates until interrupted with Ctrl+C

---

### TC_Simulation_006_determinism_same_seed_identical

**TestSets:** [Simulation], [Determinism]

**Preconditions:**
- Gateway running; CLI + SDK installed
- `boat-platform/config/scenarios/example.json` available (declares `vehicle.speed`)
- `test/fixtures/scripts/determinism_probe.py`: uploads a copy of the scenario with the
  requested seed, injects a seeded `noise` fault on `vehicle.speed`, then steps the paused
  simulation one tick at a time and injects a fixed signal value at each tick, recording
  every `(tick, name, value)` the gateway emits

**TestSteps:**
1. `python3 test/fixtures/scripts/determinism_probe.py --seed 1234 --ticks 32 --out run1.jsonl`
2. Repeat with the same seed: `--out run2.jsonl`
3. Compare the two recordings

**Expected:**
- Both runs produce an identical `(tick, name, value)` sequence — the gateway's per-tick
  PRNG reseed makes the seeded noise reproducible (wall-clock timing is irrelevant because
  the probe advances ticks explicitly with `StepSimulation`)
- This is the signal-event guarantee; it does not require a frame recorder

---

### TC_Simulation_007_different_seed_differs

**TestSets:** [Simulation], [Determinism]

**Preconditions:**
- Same gateway, scenario and probe script as TC_Simulation_006

**TestSteps:**
1. `python3 test/fixtures/scripts/determinism_probe.py --seed 1234 --ticks 32 --out run1.jsonl`
2. `python3 test/fixtures/scripts/determinism_probe.py --seed 4321 --ticks 32 --out run2.jsonl`
3. Compare the recordings

**Expected:**
- The runs differ (at least one `(tick, value)` pair changes) — sanity check that the
  seed actually influences the noise stream, guarding against a determinism test that
  passes trivially because nothing is random

---

### TC_Simulation_008_invalid_scenario_error

**TestSets:** [Simulation], [Error]

**Preconditions:**
- Gateway running; scenario id `does_not_exist` absent

**TestSteps:**
1. `boat sim create --scenario does_not_exist`

**Expected:**
- A clear error naming the unknown scenario; no simulation is created
  (`boat sim list` unchanged)

---

### TC_Simulation_009_fault_inject_and_list

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- Gateway running; CLI connected

**TestSteps:**
1. `boat fault inject vehicle.speed --type stuck --tick 10 --end-tick 20 --magnitude 42`
2. `boat fault inject vehicle.speed --type delay --tick 5 --delay-ticks 3`
3. One fault of each remaining type: `noise`, `dropout`, `invert`
4. `boat fault list`

**Expected:**
- Steps 1-3: each prints a row with a `fault-<n>` id, the target, type and tick window; a
  single-tick fault (no `--end-tick`) shows `to` equal to `from`
- Step 4: lists every injected fault with type, window, magnitude and delay
- Aliases are accepted and map to the same wire type: `corruption` = `noise`,
  `drop`/`packet-drop` = `dropout`

---

### TC_Simulation_010_fault_inject_validation

**TestSets:** [Simulation], [CLI], [Error]

**Preconditions:**
- Gateway running; CLI connected

**TestSteps:**
1. `boat fault inject vehicle.speed --type bogus --tick 1`
2. `boat fault inject '' --type stuck --tick 1`
3. `boat fault inject vehicle.speed --type stuck --tick 20 --end-tick 10`
4. `boat fault inject vehicle.speed --type delay --tick 1 --delay-ticks 0`
5. `boat fault inject vehicle.speed --type noise --tick 1 --magnitude nan`

**Expected:**
- Every step exits 1 and injects nothing (`boat fault list` unchanged):
  1. `unknown fault type 'bogus'; valid: corruption, delay, drop, dropout, invert, noise, packet-drop, stuck`
  2. `RPC error [INVALID_ARGUMENT]: target must not be empty`
  3. `end_tick must be greater than or equal to tick`
  4. `RPC error [INVALID_ARGUMENT]: delay_ticks must be greater than zero for a delay fault`
  5. `RPC error [INVALID_ARGUMENT]: magnitude must be finite`

---

### TC_Simulation_011_one_live_session

**TestSets:** [Simulation], [Error]

**Preconditions:**
- Two scenarios uploaded: `boat scenario create --file boat-platform/config/scenarios/example.json`,
  and a copy with `"id": "example_b"`

**TestSteps:**
1. `boat sim create --scenario example` (note id A), `boat sim create --scenario example_b` (id B)
2. `boat sim start A`
3. `boat sim start B; echo rc=$?`
4. `boat sim list`
5. `boat sim stop A`, then `boat sim start B`

**Expected:**
- Step 3: `RPC error [FAILED_PRECONDITION]: simulation 'A' is already active; stop or reset it
  before starting another`, `rc=1`
- Step 4: both rows report the one live session's state (they share one `SimulationContext`)
- Step 5: B starts

---

### TC_Simulation_012_reset_rewinds_tick

**TestSets:** [Simulation]

**Preconditions:**
- A simulation created from `example`

**TestSteps:**
1. `boat sim start <id>`, wait ~1 s
2. `boat --json sim watch <id>` for a moment (Ctrl-C) -- note the tick
3. `boat sim reset <id>`
4. `boat --json sim watch <id>` again; then `boat sim start <id>` and watch

**Expected:**
- Step 2: tick in the hundreds or more, `"state": 2` (RUNNING)
- Step 4: `"tick": 0`, `"state": 1` (IDLE) right after reset; after start the tick counts up from 0 again
  with no `tick must be monotonically increasing` INTERNAL error
