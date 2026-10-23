# Performance baseline

This is the committed baseline for the two non-functional requirements in
[`project-plan.md`](../project-plan.md): frame/event throughput and soft-real-time
tick jitter.

The harness lives at `src/benchmarks/benchmark_main.cpp` and is built as
`boat_benchmark` (it is deliberately not a ctest case; runner speed varies too
much for a throughput threshold to be a useful pass/fail gate).

## How to run

```bash
cmake --build build/debug --target boat_benchmark

# Synthetic driver: isolates software dispatch, never touches the kernel.
./build/debug/src/benchmarks/boat_benchmark --frames 1000000 --ticks 0

# Real vcan path (requires an existing, up vcan0):
sudo modprobe vcan
sudo ip link add vcan0 type vcan 2>/dev/null || true
sudo ip link set vcan0 up
./build/debug/src/benchmarks/boat_benchmark --frames 1000000 --ticks 0 \
  --iface vcan0

# Tick jitter, isolated from throughput warmup:
./build/debug/src/benchmarks/boat_benchmark --frames 0 --ticks 1000 \
  --interval-us 1000
```

The JSON result has two sections:

- `frame_sink`: frames passed through `FrameSink -> CanBusRegistry -> driver`.
  With no `--iface` the driver is a synthetic no-op, which isolates the core
  dispatch path. With `--iface vcan0` it is the real SocketCAN/vcan path,
  including the TX syscall and loopback RX dispatch.
- `tick`: wall-clock interval samples from a real `TickAuthority` configured
  for 1 ms ticks. `*_jitter_us` is `abs(interval - nominal)`.

## Baseline: 2026-10-23

Host: Intel Core i7-3840QM, 8 logical CPUs, x86_64. Build: `debug` preset.

Commands:

```bash
# Throughput only (the tick section reports samples=0).
./build/debug/src/benchmarks/boat_benchmark --frames 1000000 --ticks 0
./build/debug/src/benchmarks/boat_benchmark --frames 1000000 --ticks 0 \
  --iface vcan0

# Tick jitter only (no throughput warmup or vcan RX backlog).
for i in 1 2 3 4 5; do
  ./build/debug/src/benchmarks/boat_benchmark --frames 0 --ticks 1000 \
    --interval-us 1000
done
for i in 1 2 3; do
  ./build/debug/src/benchmarks/boat_benchmark --frames 0 --ticks 1000 \
    --interval-us 1000 --load-threads 4
done
```

Results:

| Measurement | Result |
|---|---|
| Synthetic core throughput | **2,133,395 frames/s** (1,000,000 frames in 0.468736 s) |
| vcan0 wire throughput | **291k–308k frames/s** across runs (1,000,000 frames in 3.25–3.43 s) |
| Tick interval, idle (5 runs) | p99 jitter **45–370 µs**; max **154–2,628 µs**; **99.8–100%** within 1 ms |
| Tick interval, 4 busy threads (3 runs) | p99 jitter **2.09–2.97 ms**; max **3.04–6.18 ms**; **97.5–97.9%** within 1 ms |

## Interpretation

- The planned **1,000,000 events/s** threshold is **not met on the real vcan0
  path** on this host: the measured wire throughput is ~0.29–0.31M frames/s.
  The core software dispatch path reaches ~2.13M frames/s, so the 1M figure can
  only be retained as a synthetic/internal-path result, not as a vcan throughput
  claim. The plan's NFR has been amended accordingly; a future optimization can
  use this harness to show whether the vcan path improves.
- The idle tick distribution meets a 1 ms bound at **p99**, but not reliably at
  the **maximum**: two of five idle runs had a single outlier above 1 ms (max
  2.628 ms), while 99.8–100% of intervals stayed within the bound. Under
  deliberate 4-thread CPU contention the p99 itself is 2.09–2.97 ms, so the
  requirement is an **idle/soft-real-time target**, not a guarantee under
  arbitrary CPU oversubscription. A single run is not enough to establish a max;
  the repeatable commands above expose the run-to-run variance.
