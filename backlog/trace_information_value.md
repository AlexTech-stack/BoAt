# Trace Information Value — a classification concept

> Status: **stages 1 and 2 implemented** (`boat/trace_information.py` —
> `triage()`, `classify()`, `search_mask()`, `counter_lsb_hints()` — with
> `tests/test_trace_information.py`),
> plus the `boat trace score` CLI verb (`cli/boat_cli/trace.py`) and the stage-1 →
> RE search mask; the corpus sketch remains concept. Sits on top of `trace_analyzer.py` and
> `trace_reverse_engineer.py`; nothing changes existing behaviour.
>
> All numbers below were measured on `~/CAN_Traces/data`: the Dupont et al. IDS
> research captures (Opel Astra / Renault Clio / prototype, candump logs, 2019) and a
> recent-vehicle CAN FD startup-from-bus-sleep BLF (726 IDs, ~13.7k frames/s, 54% of
> IDs carrying >8-byte payloads). Measurement scripts were pure-python one-pass;
> 386k frames took ~2.6 s, so cost is not a design constraint.

## Scope

The goal is **structure extraction**: given a confusing trace, derive as much layout
and encoding as the bits themselves can support. Three separable inference layers, of
which only the first two are in scope:

| Layer | Question | Evidence it needs | In scope |
|---|---|---|---|
| **1 — Boundaries** | where does a field start and end | mutual information *between adjacent bits* | **yes** |
| **2 — Encoding** | width, signedness, byte order, factor/offset, counter/CRC role | smoothness / minimum description length under a candidate reading | **yes** |
| **3 — Semantics** | what the field physically *means* | external: ground truth, event markers, OBD cross-reference | **no** — later, other approaches |

Layer 3 cannot be reached from the trace at any entropy. That is a hard ceiling, not a
gap in the heuristics. Everything below scores a trace by **how much of the layer-1
and layer-2 hypothesis space it collapses** — intrinsic to the capture, no ground
truth needed.

## The question

- Is this trace worth reverse-engineering at all, or did the vehicle sit idle?
- Should I keep capturing, or is more of the same worthless?
- Of ten captures in a corpus, which three do I feed to the RE pass?
- Does this *new* trace add anything over the ones I already have?

The obvious answer is "measure Shannon entropy, more is better". That intuition is
half right, and the half that is wrong is the half that matters.

---

## Theory: value is hypotheses eliminated

Reverse engineering is hypothesis elimination. For every candidate field the space is
roughly `{padding, constant, boolean, unsigned int, signed int, counter, enum, CRC,
length field, MAC/encrypted, slice of a wider field, mux selector, …}`, and each
observation kills the hypotheses inconsistent with it.

**A trace's value is the number of hypotheses it eliminates, not the number of bits it
transmits.**

A hypothesis can only die if it makes a prediction the data can violate. A field that
never moves violates nothing — every hypothesis survives, the posterior stays at the
prior. That is the precise sense in which an all-zero cyclic payload is worthless: not
"few bits arrived", but "nothing died".

### Why more entropy is not monotonically better

Push the intuition to its limit. A 4-bit field showing uniformly random values —
maximum entropy, "the most going on" — eliminates only *constant* and *counter*.
Still alive: four independent booleans, a random enum, a CRC nibble, a truncated MAC,
an encrypted field. Layer 1 unresolved (one field or four?), layer 2 unresolved (no
encoding favoured). Maximum entropy eliminates almost nothing — same as zero entropy,
from the opposite direction. **Inference lives in the middle of the entropy range.**

Measured: the Renault Clio capture carries ID `0x500` — 2751 frames, *every payload
distinct*, per-bit mutual information 0.00. Thirty-two bits of maximal entropy, zero
extractable structure (a rolling/crypto field). Meanwhile the same trace's `0x29A`
carries 41 bits of application signals at moderate entropy — that is where all the
value is.

### The quantity that does the work

Not entropy — the gap between marginal and conditional entropy, i.e. the mutual
information between consecutive samples:

```
I(X_t ; X_{t-1}) = H(X_t) - H(X_t | X_{t-1})
```

Measured per-bit on real fields (Clio `0x29A`/`0xC6`, RE-pass classifications):

| Field kind | MI/bit | H_cond/bit | Reading |
|---|---|---|---|
| application signals | 0.44–0.45 | ~0.50 | structure **and** surprise |
| 4-bit counter | 0.41 | 0.59 | structure, surprise dies at field level (below) |
| CRC/checksum byte | 0.05–0.08 | ~0.90 | per-bit indistinguishable from noise |
| `0x500` rolling code | 0.00 | ~1.0 | pure surprise, no structure |
| constant bits | 0 | 0 | neither |

> **The structure/surprise rule.** A field is informative when it has **both**
> `I > 0` (structure exists, a type can be inferred) **and** residual `H_cond > 0`
> *after* the explanation levels below (the field is not merely derived).
> Constant has neither. Random has only surprise. A counter has only structure.
> A real application signal has both.

### Layer 2 is a minimum-description-length argument

Encoding is decided by *which reading makes the data least surprising*. A signed
quantity hovering near zero reads unsigned as `1, 0, 15, 14` (a jump of 15) and
signed as `1, 0, -1, -2` (steps of 1) — signed wins on description length. The same
test decides byte order, width, and whether two adjacent fields are one wider field.
`_signed_reading_is_smoother()`, `_wider_width_is_justified()` and
`_merge_adjacent_smooth_signals()` in `trace_reverse_engineer.py` already run this
argument; naming it makes the criterion uniform.

Precondition: the discriminating evidence is the **boundary crossing**. A signed field
that never goes negative is indistinguishable from an unsigned one at any frame count.
Layer-2 confidence depends on *which values were visited*, not how many frames
arrived — see Coverage below.

---

## The explanation pyramid (validated, with corrections)

The original draft claimed first-order per-bit conditioning "kills perfectly periodic
fields". **Measured: it does not.** Explanation happens at three levels, each
reclassifying bits the previous level cannot, and each with its own failure mode. All
three are needed.

### Level 0 — per-bit statistics (the bootstrap)

Marginal `H(b)` and first-order conditional
`H_cond(b) = P(0)·H₂(p₀→₁) + P(1)·H₂(p₁→₀)` per bit position. Robust at small N
(~30 samples/bit), one pass, shared with `_compute_bit_liveness()`.

- Kills: constants, and **only the LSB** of a counter. Measured on a 4-bit stride-1
  counter: bits contribute `0 + 1.0 + 0.81 + 0.54 = 2.35` bits of fake surprise —
  theory `H₂(1)=0, H₂(½), H₂(¼), H₂(⅛)` matches the measurement (2.36) exactly.
- Fails: **overcounts correlated bits.** Prototype `0x5D2`: three distinct payloads
  alternating, yet `ΣH_marg = 32.8` bits because 36 bits flip *together*. Summing
  per-bit marginals assumes inter-bit independence — it is an upper bound, honest
  only after boundary clustering.

### Level 1 — per-field conditioning (after boundary clustering)

`H(field_t | field_{t-1})` on clustered multi-bit fields.

- Kills: counters **exactly**. Measured on Clio `0x29A`'s counter: `H_cond = 0.000`
  (deterministic successor), where level 0 left 2.36 bits standing.
- Fails: **collapses on wide random fields.** The plug-in conditional estimator on
  `0x500`'s 32-bit rolling code measured `H_cond = 0.000` — every context seen once,
  so the *most random field in the trace scores as the most predictable*. The
  estimate is vacuous unless contexts repeat: require `N / K̂ ≥ ~8` observations per
  distinct context, else fall back to level 0 for that field.

### Level 2 — functional tests

`find_crcs()` / `_find_simple_checksum()` (CRC, checksum), `find_counters()`
(stride/wrap proof), and — new on CAN FD — **length fields**: E2E Profiles 4/6/7
transmit an explicit 16-bit length (PRS_E2EProtocol, Tables 6.20/6.34/6.41), a
deterministic function of the DLC, hence `H_derived`.

- Kills: derived fields that survive level 1. Measured: the Clio `0x29A` CRC still
  carries 4.37 bits/frame of conditional surprise at level 1 (it correlates with the
  partly-predictable payload) and per-bit looks like pure noise (MI/bit 0.05,
  H_cond/bit 0.9). **No statistical level identifies a CRC — only the functional
  proof does.** The entropy machinery and the RE detectors are not alternatives;
  they are mandatory complements.

---

## Consequence: decompose the bit budget

Every observed bit position lands in exactly one bucket, assigned by the *highest*
pyramid level that explains it:

| Bucket | Explained by | Definition | Value |
|---|---|---|---|
| `H_const` | level 0 | never changed | low, but *known* — it is layout |
| `H_derived` | level 2 | function of same-frame bits (CRC, checksum, length, mirror) | **0** |
| `H_seq` | level 1/2 | function of frame history (counter, alternating) | **0** |
| `H_signal` | none — but structured | `I > 0` and residual surprise > 0 | **all of it** |
| `H_residual` | none | i.i.d.-uniform-looking | opaque, or not yet understood |

```
I_useful   = H_signal                       # the extractable structure
Redundancy = (H_derived + H_seq) / H_total  # protocol overhead
Opacity    = H_residual / H_total           # encrypted, or we failed
```

**Value tracks `I_useful`, not `H_total`.** Measured scale of the correction: on the
Clio's checksum-protected messages, the CRC byte alone is **24–29% of all per-bit
surprise** and the counter another 8–9% — roughly *one third* of naive "innovation"
is protocol overhead, on a 2017 vehicle with 8-byte frames. On the recent car, where
E2E and SecOC cover far more of the payload, the fraction is higher. An unsubtracted
entropy score systematically rewards exactly the wrong messages.

### Opacity is per-region, not per-message

Measured on the recent car: `0x06F95418` carries an E2E header (CRC byte 0 + 4-bit
counter in byte 1, the canonical Profile 2/22 layout), application bytes, `0xFF`
signal-not-available fill, *and* a trailing 4-byte high-entropy block; `0x51` is two
high-entropy blocks separated by zeros. SecOC appends truncated freshness + truncated
MAC to an otherwise ordinary PDU (CP_SWS_SecureOnboardCommunication §7.3) — so a
message is typically *partially* opaque. Score opacity per byte-region and report
"structured head + opaque tail" as a recognized shape, not as a low-quality message.

---

## Estimator hygiene (all three rules measured)

**1. Estimate at bit and field granularity, never at payload granularity.**
With `N` samples, `Ĥ ≤ log2(N)`. Measured: on the Clio, *nearly every busy ID* has
whole-payload entropy pinned at 0.8–1.0× the `log2 N` ceiling; on the recent car's
48–64-byte FD frames the ceiling saturates instantly (dozens of IDs at exactly
sat = 1.00, every payload distinct). Whole-payload entropy comparisons measure frame
counts, not information.

**2. Publish the ceiling next to every figure.**
Report **saturation** = `H / min(width, log2 N)`. Saturation ≈ 1.0 means the estimate
is pinned — *capture more*, not *maximally random*. With Miller–Madow as the cheap
bias correction: `H_MM = H + (K̂-1)/(2N ln 2)`.

**3. Gate every conditional estimate on context coverage.**
The `0x500` collapse above. `H(X|Y)` is meaningful only when contexts repeat
(`N/K̂ ≥ ~8`); below that, fall back one pyramid level. Below ~30 samples total, emit
no verdict at all — emit `under_sampled`, which names a specific message that needs a
longer capture.

---

## Coverage — what layer 2 actually depends on

**Range coverage.** A 12-bit signal that only ever showed 2040–2056 covers 0.4% of
its range: factor/offset guesswork, `Min`/`Max` garbage, signedness undecidable (never
crossed zero). Feed this into `DiscoveredSignal.confidence` — currently the largest
unmodelled source of false confidence in the RE output.

**Init/SNA awareness.** Measured on the wake-up BLF: early frames carry `0xFF`
signal-not-available fill and init constants. Excitation measured over a window that
includes startup counts dead-but-initialized fields as constant. Coverage metrics
must either exclude the warm-up phase or report per-phase (below).

**Saturation curve.** Distinct payloads per ID vs. time is a coupon-collector curve.
Last-decile yield >10% new → keep recording; <1% for >90% of IDs → more *time* buys
nothing, change the drive scenario (gear, HVAC, doors, faults). Cheap, and the most
useful single output for whoever is holding the logger.

---

## Traces have phases — score per phase

Measured ID-discovery curves are qualitatively different per capture type:

- **OBD-port captures (Opel, Clio):** all 85 / all 55 IDs present within the first 1%
  of the capture. Flat discovery — the logger attached to a running, filtered bus.
- **Startup-from-bus-sleep BLF:** 344 IDs in the first second, a plateau, 428 by
  10 s, 726 by 30 s. Staged network-management wake-up, with init/SNA payload content
  early on.

A single B/E/I score over a phase-changing trace mixes regimes: the wake-up phase has
maximal breadth-rate and near-zero excitation; steady state is the opposite. Segment
the trace (change-points in aggregate frame rate + ID arrival rate are enough) and
score per segment. A wake-up capture is *excellent* for enumeration (B) and message
liveness timing, and *poor* for layer-2 encoding inference — one number cannot say
both.

---

## Adversarial content inflates every naive metric

Measured on the prototype captures: `fuzzing_payload.log` scores **nearly identically**
to the normal drive on aggregate entropy (ΣH_marg 113.8 vs 112.4, same innovation
rate) — and the injected ID's live-bit count explodes from 5/64 to 62/64. Naive
excitation and entropy metrics reward injected garbage. For structure extraction,
injected frames are worse than worthless: they poison smoothness-based encoding
inference with values the real ECU never emits.

Defense: cross-reference with `find_timing_anomalies()`. A liveness/entropy jump
co-located with a timing anomaly (burst, sporadic onset) is an injection signature —
exclude the anomalous window from level-1/2 inference and report it. This falls out
of machinery the analyzer already has.

---

## The scorer audits the RE pass (bidirectional, new)

Measured: the RE pass claims 90% of live bits on the Clio — but on `0x500` it claimed
**pure noise as thirty-two 1-bit "signals"** (MI/bit = 0.00, all 2751 payloads
distinct, saturation 1.00). The claim rate alone would score the RE output as nearly
complete; the information decomposition proves part of it is confabulated.

So the dependency runs both ways:

- RE result → scorer: bucket assignment (`H_derived`, `H_seq`, mux partitioning).
- Scorer → RE result: **evidence-weighting of claims.** A `DiscoveredSignal` whose
  bits show `I ≈ 0` at saturation ≈ 1.0 is flagged `suspect_noise` and its
  confidence damped. Explainability `X` counts *validated* claimed bits, not claimed
  bits.

This is the cheapest high-value deliverable in the whole concept: it needs only the
level-0 statistics plus the existing RE output, and it directly improves
`to_pdu_db()` output quality by pruning confabulated signals.

---

## Where it runs in the pipeline: two stages

The evaluation is split around the RE pass, because its two halves have different
dependencies — and measured costs (Clio: level-0 pass ~2.6 s, RE pass ~46 s) make the
first half a cheap gate for the expensive second.

```
analyze() ──► Stage 1: triage ─────► reverse_engineer() ──► Stage 2: classify + audit ──► to_pdu_db()
              (gate, exclude,         (on the cleaned,        (buckets, X, grade,
               segment, flag)          gated input)            pruned confidence)
```

**Stage 1 — before the RE pass** (level-0 statistics, shareable with
`_compute_bit_liveness()`): decides whether the trace is worth the RE pass at all,
emits `under_sampled` flags, partitions by DLC, and segments phases. (Anomalous-
window exclusion was considered here — injected frames corrupt counter-stride
measurement and CRC match fractions — but is deliberately **out of scope**: the
input is assumed to be a genuine capture of a healthy bus, not an IDS dataset.)
Stage 1 must never publish a final score: CRC bits are
statistically noise (MI/bit ≈ 0.05) and counters still carry ~2.4 fake bits per-bit,
so a pre-RE "innovation" overstates value by the measured ~35% redundancy share.

**Stage 2 — after the RE pass**: assigns `H_derived`/`H_seq` from `find_crcs()` /
`find_counters()`, runs the claim audit (`suspect_noise`), and computes `X`, the
grade, and the action list. It re-labels the retained stage-1 statistics — it never
re-reads the trace.

---

## Feeding the statistics back: the search mask

The scorer does not only grade the RE pass — it can constrain it. Measured on
real captures, the reliable direction is **negative**: the statistics are far
better at saying *where no application signal can live* than at proposing where
one does.

`TraceInformationScorer.search_mask()` builds that constraint from stage-1
statistics alone, so it is available before the expensive pass runs. A byte is
noise-like when every live bit in it clears the same bars the stage-2 audit uses
(`h_cond/bit ≥ 0.5`, `MI/bit ≤ 0.05`); runs of ≥2 consecutive such bytes are
masked. The run requirement matters: per-bit masking would shred genuine wide
signals, whose low bits legitimately look noisy (a 16-bit smooth signal's LSB is
close to a fair coin), while the measured opaque regions are multi-byte tails.

**Scope is routing, not suppression.** The mask gates *only* the statistical
application-signal clustering. `find_counters`, `find_crcs` and
`find_multiplexors` keep full access to every bit, deliberately — they *prove*
what they find, so a statistical prior has no business overruling them, and a
CRC sitting inside an otherwise opaque tail must stay findable. That is the
explanation pyramid used as a dispatcher: each region goes to the detector that
can actually establish something about it.

Measured on the Renault Clio capture:

| | unmasked | masked |
|---|---|---|
| signals found | 166 | 108 |
| audit suspects | 70 | 12 |
| genuine signals lost | — | **0** |
| runtime | 54 s | 53 s |

The 58 removed signals are exactly the three fully-opaque messages (0x303,
0x500, 0x564); nothing else changed, and no signal was added. Explainability,
opacity, signal share and grade are **identical** in both runs — the mask moves
work earlier without altering the information accounting, which is a useful
consistency check that the two mechanisms agree about the same bits.

Two honest caveats:

1. **No speedup.** Runtime is dominated by `find_counters`/`find_crcs`, which
   are deliberately unmasked. The win is precision, not cost. A cost win would
   need the counter-template scan (below), not the mask.
2. **The mask and the audit are not independent.** They target the same failure
   mode with the same constants, by design. So a masked run legitimately finds
   little to flag, and a clean audit is then evidence the mask worked — not
   evidence the RE pass is sound. `boat trace score --no-mask` exists for that
   reason, and both output formats record whether the mask ran.

The 12 surviving suspects (0x511, 0x563) are regions the conservative byte-run
rule declines to mask; the audit catches them afterwards. That division of
labour is intended: mask the confident cases, audit the rest.

### The counter template scan

A counter's per-bit conditional entropy is analytically exact: bit *k* counted from
the LSB flips every 2^k frames, so `H_cond = H₂(2^-k)` — 0, 1.0, 0.811, 0.544, …
Measured on two real counters (Clio `0xC6` @51+4 and `0x29A` @52+4): **0.00 / 1.00 /
0.81 / 0.54**, matching the prediction to two decimals.

Two corrections came out of building the scan, both of which changed the design:

1. **The full template is not usable as a filter, only the LSB term is.** The higher
   bits deviate under decimation. Measured on the Clio's *stride-3* counter `0x511`
   @52+4: bit 3 reads 0.95 against a predicted 0.54, while the LSB still reads
   exactly 0.00. The LSB survives because with an observed stride *s* it alternates
   deterministically whenever *s* is odd — and even strides are rejected outright by
   `_counter_stride`. So the LSB condition is necessary for every counter this code
   can accept, decimated or not.

2. **`H_cond ≈ 0` does not identify the LSB.** `H₂(2^-k) → 0` for *large* k as well
   as for k=0, so a counter's slow high bits clear an entropy bar just as its LSB
   does — measured on an 8-bit counter, bits 5–7 all read below 0.25. The
   discriminating quantity is the **change rate**: the LSB toggles on every frame
   (`p_change ≈ 1`) while a high bit almost never does (`< 0.02`). Switching the
   condition from entropy to change rate cut the hinted positions on the Clio from
   87 to 10 (3.8% → 0.4% of all bit positions) and the speedup from 37% to 49%.

`counter_lsb_hints()` returns `{can_id: sorted positions}`; `_scan_for_counters`
drops any candidate span containing no hinted position *before* extracting frames,
where `_quick_counter_check` costs a 64-frame extraction. Because the condition is
necessary, pruning cannot change which counters are found — verified, not assumed:

| | unhinted | hinted |
|---|---|---|
| `find_counters` (Clio) | 5.1 s | **2.6 s** (49% faster) |
| `find_counters` (Prototype) | 1.3 s | 0.7 s (48% faster) |
| counters found | identical | identical |
| every signal in the full pipeline | — | **byte-identical** |

The full-pipeline gain is small (4–8%) because `find_crcs` dominates; the counter
scan is simply no longer part of the cost. Unlike the search mask this is on
unconditionally — a necessary condition cannot trade accuracy for speed, so there is
nothing to opt out of.

### Not implemented: boundary proposal

Transition-coupling MI between adjacent bits — `I(Δbᵢ ; Δbᵢ₊₁)`, "do these bits
change together?" — shows a real sawtooth (carries couple bits within a field,
so MI ramps up and collapses at the edge), reproducible on both real and
synthetic captures. As a *detector* it is unsolved: on synthetic frames with
known layout, a collapse-gradient readout reached recall 4/6 with ~50%
precision, and blocks of independent booleans are invisible to it by
construction (no carry structure to collapse from). Two further notes: the MI at
gap *p* couples bits *p* and *p+1*, so a naive readout is off by one; and
evaluating it against the RE pass's own output is circular, since that output is
what the audit shows to be unreliable. Worth revisiting against a real DBC, not
before.

---

## Corpus-relative value

Value is relative to what is already known. A trace unremarkable in isolation is
valuable if it shows an ID, a value range, a DLC, or a timing profile the corpus has
never seen — the measure is cross-entropy surprise of the new trace under the corpus
model. Practically: store a compact **sketch** per ID in the PDU database — per-bit
toggle counts, per-signal observed min/max, distinct-payload count, timing profile —
and score novelty in one pass without keeping old traces around.

---

## The classification

Deliberately **not** a single number. Five components, 0–1 each:

| | Component | Measures | Predicts |
|---|---|---|---|
| **B** | Breadth | unique IDs, channels, phase-resolved discovery | how much of the vehicle is in view |
| **E** | Excitation | live-bit fraction + range coverage, warm-up-excluded, anomaly-excluded | layer 2 — are the boundary crossings there |
| **I** | Innovation | net `H_signal` bits/s after pyramid subtraction | layer 1+2 — is there structure to find |
| **X** | Explainability | *validated* fraction of `H_total` attributed by RE + scorer audit | how much was resolved vs. opaque |
| **S** | Sufficiency | sample floors, saturation status, context-coverage gates | whether the estimates can be trusted at all |

**Grade for structure extraction** weights `S` and `E` highest (they gate whether
inference is possible), then `I`, then `B`. `X` is reported separately — it measures
the RE pass as much as the trace.

Output: the profile **plus a ranked action list** — "0x2A1 under-sampled, needs ~4×
more frames"; "0x310 saturated at 12% range coverage, exercise the actuator";
"0x4B0–0x4B8 opaque tails, likely SecOC — structure extraction capped at N bytes";
"burst at t=411–413 s excluded as suspected injection".

### Other consumers invert this

A replay/regression baseline and an anomaly-detection baseline both want *low*
innovation — a boring, saturated, repetitive trace is the better artefact there. Only
structure extraction wants `I_useful` maximised. The profile is a vector; the grade
is named after its purpose.

---

## Implementation sketch

New module `boat-platform/sdk/python/boat/trace_information.py`:

- `@dataclass BitInformation` — per bit: `p1`, `h_marginal`, `h_conditional`,
  `mutual_information`, `bucket`, `pyramid_level`
- `@dataclass RegionInformation` — per byte-region: bucket sums, opacity, shape tag
  (`structured`, `opaque_tail`, `sna_fill`, `padding`)
- `@dataclass MessageInformation` — per CAN ID: bucket sums, saturation, sample and
  context-coverage sufficiency, distinct-payload curve, range coverage, DLC set,
  `under_sampled` / `suspect_noise` flags
- `@dataclass TraceInformationProfile` — phase segments, per-phase B/E/I/X/S, the
  structure-extraction grade, ranked action list
- `class TraceInformationScorer(analysis, re_result=None)` — without the RE result it
  degrades gracefully: no `H_derived` split (those bits stay in `H_residual`,
  pessimistic), levels 0–1 only. Same optional-numpy pattern as
  `trace_reverse_engineer.py`.

Practical notes from the measurement runs:

- Level-0 pass is `O(frames × payload_bits)`, shareable with
  `_compute_bit_liveness()`; pure python handled 386k×64-bit frames in ~2.6 s and
  2.69M frames in ~20 s. BLF decode dominates on large FD captures.
- Variable-length IDs are real but rare (12 of 726 on the recent car): partition
  per-DLC before any per-bit statistics, analogous to `_cluster_per_variant()` —
  bit position *i* means different things in a 20- and a 48-byte frame of the same
  ID, and CAN FD DLC padding (`0xAA`/`0x00` tails, observed) must land in `H_const`
  per partition, not smear across partitions.
- Multiplexed messages: compute per selector-variant (reuse `_cluster_per_variant()`),
  else the selector's switching dominates every downstream field.

Surfaces: `boat trace score <file> [--json]`; a panel in `tools/trace_analyzer.py`
(8088); the corpus sketch as an optional block in the PDU database JSON.

Determinism: pure function of the trace bytes, no sampling, no RNG.

---

## Open questions

1. **Unclaimed bits** default to `H_residual` (pessimistic — a weak RE result should
   not flatter the trace), with `X` reported separately. Settled by the `0x500`
   finding; no longer open.
2. **Phase segmentation granularity** — change-point detection on frame rate + ID
   arrival is probably enough; is per-phase scoring worth the report complexity for
   short steady-state captures? (Skip segmentation when the discovery curve is flat
   in the first 1%, as on the OBD captures.)
3. **Estimator strength.** Miller–Madow plus the context-coverage gate handled every
   case observed so far; revisit Chao–Shen/NSB only if thin mux partitions misbehave.
4. **Opacity vs. failure.** Uniform distribution + near-zero autocorrelation + no CRC
   match + (new) *tail position after a structured head* is a fair MAC/crypto
   signature — the region shape carries real evidence. Getting this wrong in the
   optimistic direction is the worse error.
5. **Inter-bit MI as first-class output.** Level 0's correlated-bit overcounting makes
   the case stronger: the scorer needs at least the pairwise-adjacent MI to do honest
   field-level accounting, which is the same quantity layer 1 clustering uses. Expose
   the adjacent-bit MI vector; the full matrix stays internal to
   `_cluster_correlated_bits()`.
