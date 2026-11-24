# TestSet: Scenario

System-level tests for scenario management via `boat scenario`.

Common precondition: gateway running.

---

### TC_Scenario_001_create_and_get

**TestSets:** [Scenario], [CLI]

**Preconditions:**
- A valid scenario definition file prepared

**TestSteps:**
1. `boat scenario create ...` with the definition
2. `boat scenario get` for the created id
3. `boat scenario list`

**Expected:**
- Create succeeds and returns/accepts the scenario id
- Get returns the same content that was submitted; list contains the id

---

### TC_Scenario_002_validate_valid

**TestSets:** [Scenario], [CLI]

**Preconditions:**
- A syntactically and semantically valid scenario definition

**TestSteps:**
1. `boat scenario validate` with the definition

**Expected:**
- Validation passes with no findings

---

### TC_Scenario_003_validate_invalid

**TestSets:** [Scenario], [CLI], [Error]

**Preconditions:**
- `test/fixtures/scenarios/scenario_invalid_semantic.json`: structurally valid, but a
  fault targets an undeclared signal and a plugin path does not exist
- `test/fixtures/scenarios/scenario_invalid_structural.json`: missing a required
  field (`duration_ticks`)

**TestSteps:**
1. `boat scenario validate --file test/fixtures/scenarios/scenario_invalid_semantic.json`
2. `boat scenario create --file test/fixtures/scenarios/scenario_invalid_structural.json`

**Expected:**
- Step 1: reports each specific issue and `valid=false` — `fault ... references
  undeclared signal '...'` and `plugin not found: '...'` — with no stack trace
- Step 2: rejected with the structural parse error (missing required field), and the
  scenario is not stored

---

### TC_Scenario_004_delete

**TestSets:** [Scenario], [CLI]

**Preconditions:**
- A scenario exists that is not referenced by a running simulation

**TestSteps:**
1. `boat scenario delete` for the id
2. `boat scenario list`
3. `boat sim create --scenario <deleted-id>`

**Expected:**
- Scenario disappears from the list; creating a simulation from it now fails with a
  clear error

---

### TC_Scenario_005_ai_generated_scenario

**TestSets:** [Scenario], [AI]

**Preconditions:**
- AI backend configured (`boat ai config set --endpoint ... --model ...`), reachable

**TestSteps:**
1. `boat ai scenario --desc "Create a CAN bus with two ECUs exchanging 0x100 and 0x200"`
2. `boat scenario validate` on the generated output

**Expected:**
- A scenario definition is generated; it passes validation (or the failure is in the
  generated content, clearly reported by validate — not a crash in the tooling)
