# User Flows

## 1) Engineer Runs a Scenario

1. `boat scenario create --file scenario.yaml`
2. `boat sim start --scenario <id>`
3. `boat sim watch <id>`
4. `boat sim stop <id>`

## 2) CI Pipeline Validates SUT

1. GitHub Action triggers simulation job
2. `boat sim run --scenario regression.yaml --assert assertions.yaml`
3. Command exits `0` on pass, `1` on failure
4. CI marks pipeline stage success or failure

## 3) Engineer Replays a Trace

1. `boat replay start --trace <id>`
2. `boat replay seek --tick 5000`
3. `boat replay stream`

## 4) Plugin Developer Integrates a Plugin

1. Implement `boat_plugin_create()`
2. Build plugin `.so`
3. `boat plugin register --path ./myplugin.so`
4. `boat plugin list`

