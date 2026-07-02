# PDU Gap Analysis — BoAt vs. AUTOSAR

## Simulation Philosophy & Architecture

BoAt is a **simulation environment for automotive ECUs** . The simulation does **not** reimplement AUTOSAR — it generates and consumes traffic to exercise AUTOSAR ECUs under test.

**Core vs. Plugin**: The gateway core stays intentionally minimal (CAN/Ethernet transport, basic PDU routing, gRPC API). AUTOSAR-aligned features belong as **plugins** (e.g., CanTp is already a plugin). Features like a full PDURouter, signal gateway, E2E engine, and SecOC traffic generator shall be implemented as plugins, not in the core.

**Intentional deviations**: Where BoAt diverges from AUTOSAR, it does so deliberately for simulation purposes and is documented transparently. Examples: gRPC API instead of PduR_Callbacks (the simulation's control plane), flat routing (core is a bus simulator, not a gateway), no COM signal-level stack (the ECU handles that internally).

**Two-tier classification**: Each gap below is classified from the simulation's perspective:

| Tier | Meaning |
|------|---------|
| **A — Core fix** | Real bug affecting simulation correctness (protocol, encoding, parsing) |
| **A — Plugin** | Important simulation capability that belongs as a plugin |
| **B — Documented** | Intentional deviation or it's not relevant to simulation purpose |

The AUTOSAR severity (Critical/High/Medium/Minor) reflects spec compliance. The simulation tier reflects what actually matters for the simulation to generate correct traffic and test ECUs effectively.

---

## Spec Documents Consulted

| Document | Status |
|----------|--------|
| AUTOSAR CP_RS_Gateway (R24-11) | ✅ Found |
| AUTOSAR CP_SRS_COM (R23-11) | ✅ Found |
| AUTOSAR SWS_CAN Interface (R20-11) | ✅ Found |
| AUTOSAR SWS_CAN Driver (R3.2) | ✅ Found |
| AUTOSAR SWS_CAN_TP (V2.6.0) | ✅ Found |
| AUTOSAR CP_SWS_EthernetInterface (R25-11) | ✅ Found |
| AUTOSAR CP_SWS_SecOC (R25-11) | ✅ Found |
| AUTOSAR PRS_E2EProtocol (R19-11) | ✅ Found |
| AUTOSAR CP_EXP_BSWDistributionGuide (R25-11) | ✅ Found |
| AUTOSAR CP_SWS_COMManager (R25-11) | ✅ Found |
| AUTOSAR CP_SWS_PDURouter (R25-11) | ✅ Found |
| AUTOSAR SWS_IPDUMultiplexer (R22-11) | ✅ Found |
| AUTOSAR CP_SWS_COM (R23-11) | ✅ Found |

---

## Summary Count

| Technology | 🔴 Critical | 🟡 Important | 🔵 Minor/Arch | Total |
|------------|:-----------:|:------------:|:-------------:|:-----:|
| **CAN** | 3 | 4 | 2 | **9** |
| **Ethernet** | 1 | 4 | 3 | **8** |
| **Cross-cutting** | 5 | 8 | 5 | **18** |
| **Out of scope** | — | — | 3 | **3** |
| **Total** | **9** | **16** | **13** | **38** |

---

## ✅ What IS Implemented (AUTOSAR-Aligned)

> **Note**: Features marked **core** are in the gateway binary. Features marked **plugin** are separate `.so` files loaded at runtime. This is intentional — the core stays minimal; AUTOSAR-aligned functionality ships as plugins (see § Plugin Architecture Opportunities).

| Feature | AUTOSAR Equivalent | Files |
|---------|--------------------|-------|
| **PduRouter** (central routing hub) **(core)** | PduR module | `pdu_router.h/cpp` |
| **I-PDU Groups** (enable/disable gating) **(core)** | [SRS_Com_02090], [SRS_Com_00218] | `pdu_types.h:70-75`, `pdu_router.cpp:233-280` |
| **IpduM LONG header** (serialize/deserialize) **(core)** | SWS_IpduM container format | `ipdumcontainer.h/cpp` |
| **Intel/Motorola bit packing** **(core)** | [BSW02078] COM endianness | `com_signal.h:48-58`, `com_signal.cpp:18-88` |
| **E2E CRC8/16/32** (polynomials) **(core)** | PRS_E2EProtocol Profiles 2/4/5/7 | `com_signal.h:81-83`, `com_signal.cpp:230-273` |
| **Transmission Engine** (Cyclic/OnChange/Mixed) **(core)** | [SRS_Com_02083] | `transmission_engine.h/cpp` |
| **CAN ID ↔ PDU ID mapping** **(core)** | `CanIfTxPdu.CanId`, `PduRoute.can_id` | `pdu_types.h:31`, `pdu_router.cpp:156` |
| **11-bit and 29-bit CAN** | `Can_IdType` in SWS_CAN_Driver | `pdu_db.schema.json:76-77` |
| **IPv4/UDP/IpduM path** **(core)** | SoAd + IpduM over Ethernet | `ipdumcontainer.cpp:123-171` |
| **IPv6/UDP/IpduM path** **(core)** | SoAd + IpduM over Ethernet | `ipdumcontainer.cpp:173-211` |
| **Deadline monitoring** (per-PDU timeout) **(core)** | [SRS_Com_02058] | `pdu_types.h:77-80`, `pdu_router.cpp:288-298` |
| **BRS/FDF/ESI flag ABI** (plugin SDK) | CAN FD flags per ISO 11898-1 | `plugin.h:17-18` |
| **Auto-config via PduDatabase** | ComIPdu (partial) | `pdu_message_node.py:61-104`, `pdu_db.py` |
| **PDU DB schema** with routing types | PduR routing path (partial) | `pdu_db.schema.json` |
| **CanTp** — ISO 15765-2 segmentation **(plugin)** | CanTp module | `src/plugins/can_tp/can_tp_plugin.cpp` |
| **TCP Plugin** — TCP send/receive/relay **(plugin)** | — (non-AUTOSAR) | `src/plugins/tcp/tcp_plugin.cpp` |
| **SOME/IP Plugin** — service discovery + req/resp **(plugin)** | SOME/IP | `src/plugins/someip/someip_plugin.cpp` |

---

## 🔴 CAN — Critical Gaps

### CAN1 — No CanHardwareObject / CanControllerRef
Bus names are free-form strings (`"vcan0"`, `"Powertrain_CAN"`). AUTOSAR requires `CanIfTxPduCanControllerRef` linking to a `CanController` instance, and `CanIfTxPduCanHwObjectRef` linking to HTH (Hardware Transmit Handle). Neither exists.

- **Req**: [SRS_Can_01074], CanIfTxPduCanControllerRef / CanHwObjectRef
- **Files**: `pdu_db.schema.json:31`, all `pdu_db_*.json` bus fields
- **Severity**: HIGH — Without controller/hardware object references, CAN configuration cannot map to real hardware.
- **Simulation posture**: **Tier B** — documented deviation. The simulation uses symbolic interface names (`"vcan0"`, `"Powertrain_CAN"`); hardware controller abstraction lives on the ECU side. Not needed for simulation traffic generation.

### CAN2 — No CanIfTxPduTriggering
AUTOSAR defines `CanIfTxPduTriggering` as a separate configuration entity linking `CanIfTxPdu` → `CanController` → `CanHardwareObject` → `CanIfTxPduUserTxConfirmation`. The schema embeds `SendType`, `CycleTime` etc. directly in the message object with no separate triggering layer.

- **Req**: CanIfTxPduTriggering configuration
- **Files**: `pdu_db.schema.json:63-66`
- **Severity**: HIGH — No way to express hardware-specific triggering (e.g., different CAN controllers use different Tx confirmation modes).

### CAN3 — CAN FD DLC Encoding Is Raw Bytes
`pdu_router.cpp:159` copies `min(payload.size(), 64)` as `BoatCanFrame.dlc`. ISO 11898-1 §10.4.2.2 defines DLC encoding:
- DLC 0-8 → bytes 0-8 (direct)
- DLC 9 → 12 bytes, DLC 10 → 16, DLC 11 → 20, DLC 12 → 24, DLC 13 → 32, DLC 14 → 48, DLC 15 → 64

A 12-byte payload should set DLC=9, not DLC=12. Any CAN controller expecting ISO encoding will truncate or misinterpret the frame.

- **Req**: [SRS_Can_01073], ISO 11898-1 §10.4.2.2
- **Files**: `pdu_router.cpp:159`, also affects `can_tp_plugin.cpp:78, 428`
- **Severity**: HIGH — Frames have wrong DLC for any payload that is not an exact discrete DLC length.
- **Simulation posture**: **Tier A, core fix** — real protocol bug. CAN FD frames produced by the simulation will be clocked at the wrong DLC rate, producing incorrect traffic for ECUs under test.

---

## 🟡 CAN — Important Gaps

### CAN4 — No BRS Flag Propagation
`BoatCanFrame.flags` supports `CANFD_BRS=0x01`, `CANFD_FDF=0x04`. The schema has a `BRS` field at `pdu_db.schema.json:80-81`. But `PduRouter::SendPdu()` zero-initializes the `CanFrame{}` struct (`pdu_router.cpp:157`) and never sets `flags`. Incoming frames have their flags ignored.

- **Req**: CAN FD BRS per ISO 11898-1
- **Files**: `pdu_router.cpp:157-161`
- **Severity**: MEDIUM — CAN FD works but at arbitration bitrate only.
- **Simulation posture**: **Tier A, core fix** — protocol correctness bug. Without BRS flag, CAN FD frames transmit at arbitration bitrate only, producing incorrect traffic.

### CAN5 — No CAN Filter / Acceptance Mask
No configuration for `CanHardwareObjectFilter` with acceptance code/mask. All incoming CAN frames are forwarded to PduRouter — filtering is done purely by `can_id` match in software.

- **Req**: CanHardwareObjectFilter, `Can_SetControllerMode`
- **Files**: Missing from `pdu_db.schema.json`
- **Severity**: MEDIUM — Software filtering adds CPU overhead but functionally correct.

### CAN6 — No CanIfRxPdu Configuration
No Rx PDU assignment to HRH (Hardware Receive Handle) with `CanIfRxPduUserRxIndication` upcall routing. Receive path at `pdu_router.cpp:315` is a simple CAN ID match.

- **Req**: CanIfRxPduCanHwObjectRef
- **Files**: `pdu_db.schema.json:35-38`
- **Severity**: MEDIUM — No hardware-level Rx filtering.

### CAN7 — No PDU Length Validation on Send
`pdu_router.cpp:159` silently truncates `min(payload.size(), 64)` with no configured PDU length. A PDU configured as 4 bytes in the database can be sent with 64 bytes of payload without error.

- **Req**: [SWS_Com_00574] — COM checks length on Rx unpack; [SWS_PduR_00746] — PduR copies up to min(received, configured_dest_length)
- **Files**: `pdu_router.cpp:159`
- **Severity**: MEDIUM — Silent data corruption in misconfigured scenarios. **Correction**: The original analysis cited [SRS_Com_02041] — this requirement is actually about atomic data consistency for signal groups, not I-PDU length validation (see §Corrections).

---

## 🔴 Ethernet — Critical Gaps

### ETH1 — No EthControllerRef / Socket Abstraction
IP addresses, ports, and VLAN IDs are embedded inline in `PduRoute` (`pdu_types.h:34-38`) and `PduContainerDef` (`pdu_types.h:49-53`). AUTOSAR requires:
- `EthController` reference for each Ethernet frame path
- `SoAdBswConfig` mapping PDU routes to socket connections
- `TcpIpSocket` configuration (binding, UDP/TCP type, local/remote address)

- **Req**: SoAdBswConfig, EthController, TcpIpSocket
- **Files**: `pdu_db.schema.json:83-93`, `pdu_service_impl.cpp`
- **Severity**: HIGH — Without controller abstraction, Ethernet routing bypasses the entire SoAd/TcpIp stack, conflating application-layer addresses with hardware interfaces.
- **Simulation posture**: **Tier B** — documented deviation. The simulation directly constructs UDP/IP/IpduM frames for traffic generation; the SoAd/TcpIp stack runs on the ECU under test. Inline address fields are adequate for the simulation use case.

---

## 🟡 Ethernet — Important Gaps

### ETH2 — IpduM: Only LONG Header, No SHORT
`IpduMSerialize` always produces an 8-byte per-entry header (4B PDU ID + 4B DLC). The AUTOSAR SHORT header is not supported.

**Correction**: The original analysis claimed SHORT is "2 bytes: 12-bit index + 4-bit length." The IpduM spec (R22-11) defines it as **4 bytes (32 bits total): 24-bit Header ID + 8-bit DLC**. Also `IPDUM_HEADERTYPE_NONE` exists for static containers. See §Corrections.

- **Req**: SWS_IpduM ContainerHeaderType
- **Files**: `ipdumcontainer.cpp:10-26`
- **Severity**: **⬆️ HIGH** (upgraded from MEDIUM) — Header format is now well-understood from the spec. BoAt's 8-byte overhead per entry for small containers is worse than originally stated (6 bytes vs 4 bytes overhead vs SHORT).

### ETH3 — No NPduNumber / Per-PDU Triggering in Containers
All PDUs in a container share the same cycle time (`pdu_db_test.json:296-297`).

**Correction**: The original analysis cited `NPduNumber` — this parameter was **not found** in the IpduM spec (R22-11). The actual per-contained-PDU triggering model uses `IpduMContainedTxPduTrigger` (IPDUM_TRIGGER_ALWAYS / IPDUM_TRIGGER_NEVER), `IpduMContainerTxSendTimeout`, `IpduMContainerTxSizeThreshold`, and `IpduMContainerTxFirstContainedPduTrigger`. Also, child ETH_PDU entries in the schema already have per-PDU `SendType`/`CycleTime` fields — the runtime ignores them. See §Corrections.

- **Req**: IpduMContainedTxPduTrigger, IpduMContainerTxSendTimeout, IpduMContainerTxSizeThreshold
- **Files**: `config/pdu_db.schema.json:94-98`, `gen_pdu_db_test.py:296-297`
- **Severity**: **⬆️ HIGH** (upgraded from MEDIUM) — The per-contained-PDU triggering model is richer than described, with multiple independent trigger sources (timeout, size threshold, per-PDU trigger flag, first-PDU trigger).

### ETH4 — IPv6: No Extension Header Support
`ParseUdpIpPacket` (`ipdumcontainer.cpp:222-233`) assumes Next Header = 17 (UDP) immediately after the 40-byte fixed IPv6 header. IPv6 packets with Hop-by-Hop (0), Routing (43), Fragment (44), or Destination Options (60) extension headers will be silently rejected.

- **Req**: [SWS_EthIf_00308], RFC 8200 §4
- **Files**: `ipdumcontainer.cpp:229`
- **Severity**: MEDIUM — Fails on any real-world IPv6 network that uses extension headers.
- **Simulation posture**: **Tier A, core fix** — protocol correctness bug. IPv6 packets with extension headers (Hop-by-Hop, Routing, Fragment, Destination Options) are silently rejected, causing false failures in simulation.

### ETH5 — No Multicast / Anycast PDU Addressing
`PduRoute::dst_ip` is a single address with no multicast group support. AUTOSAR SoAd supports one-to-many PDU distribution via multicast socket routes with IGMP/MLD group management.

- **Req**: SoAdSocketRoute multicast group addresses
- **Files**: `pdu_types.h:36`
- **Severity**: MEDIUM — Cannot distribute a single Ethernet PDU to multiple receivers without separate routes.

---

## 🔴 Cross-Cutting — Critical Gaps

### CC1 — No PduR Routing Table Structure
Routes stored as `unordered_map<uint32_t, PduRoute>` keyed by `pdu_id` (`pdu_router.h:92`). AUTOSAR SWS_PduR defines:
- `PduRRoutingTable` with `PduRRoutingTableSet`
- `PduRDestPdu` with data provision type (PDUR_DIRECT or PDUR_TRIGGERTRANSMIT)
- `PduRSrcPdu` with source reference
- Multi-destination routing paths via 0..* `PduRDestPdu` per path

**Correction**: The original analysis claimed "explicit/dynamic/direct" categories. The PduR spec (R25-11) defines only two data provision types: `PDUR_DIRECT` (data passed in Transmit call) and `PDUR_TRIGGERTRANSMIT` (data fetched later via TriggerTransmit callback). See §Corrections.

The flat map cannot express multi-hop routes, gateway characteristics, or dest data provision.

- **Req**: SWS_PduR RoutingTable, DestPdu, SrcPdu
- **Files**: `pdu_router.h:92`, `pdu_db.schema.json:40-53`
- **Severity**: HIGH — Routing is simple 1:1 PDU ID → transport; no structured routing with multi-destination or gateway support. (Note: the schema already has `RoutingType`, `TargetDbIds`, `SourceDbId` fields — see §Corrections for the runtime vs schema distinction.)
- **Simulation posture**: **Tier A plugin**. The core PduRouter stays minimal (1:1 transport). A PDURouter plugin would implement structured routing with PduRRoutingPath, PduRDestPdu (PDUR_DIRECT / PDUR_TRIGGERTRANSMIT), multi-destination, and buffering for realistic gateway behavior testing.

### CC2 — No Signal-Level Gateway Routing
`pdu_db.schema.json:108-122` defines `signal_routes` as `SrcDbId.SrcSignalId → DstDbId.DstSignalId` pairs. `PduRouter` never reads or applies them — routing is PDU-level only. AUTOSAR [SRS_GTW_06055] requires 1:n signal routing between I-PDUs.

The implementation has all the building blocks (com_signal pack/unpack, PduRouter routing, signal_routes in DB) but no runtime engine that connects them.

- **Req**: [SRS_GTW_06055], PduR_GwRoutingPath, [SRS_Com_02112]
- **Files**: `pdu_router.cpp` (unused signal routes), `pdu_db.schema.json:108-122`
- **Severity**: HIGH — Cross-bus signal remapping requires external orchestration; cannot route CAN signal → Ethernet PDU without custom code.
- **Simulation posture**: **Tier A plugin** — Signal Gateway plugin. The building blocks exist (com_signal pack/unpack, signal_routes in schema, PduRouter transport) but no runtime connects them. A plugin reading `signal_routes` from the DB and orchestrating unpack→remap→pack→send would enable cross-bus signal testing.

### CC3 — E2E: CRC Functions Only, No Profile State Machines
`com_signal.cpp:230-273` computes CRC8/16/32 with correct AUTOSAR polynomials. The `isE2E` field in `pdu_db.schema.json:55-61` stores a profile number. But:
- No `DataId` / `DataIdList` per PDU
- No `CounterOffset` (where in the payload the 4-bit counter lives)
- No `MaxDeltaCounter` / `MaxNoNewOrRepeatedData` (state machine thresholds)
- No `E2E_Protect()` / `E2E_Check()` / `E2E_Forward()` implementations
- CRC functions are never called from `PduRouter` or `PduNode`

- **Req**: PRS_E2EProtocol Profiles 1/2/4/5/7/22
- **Files**: `com_signal.cpp:226-273`, `pdu_db.schema.json:55-61`
- **Severity**: HIGH — `isE2E` field is a placeholder. Without profile state machines, no real E2E protection (counter sequence check, timeout, repeated detection) is achievable.
- **Simulation posture**: **Tier A plugin**. The ECU under test validates E2E on received PDUs; the simulation must produce E2E-correct traffic. CRC functions already exist (`com_signal.cpp`), but an E2E plugin adding Protect/Check/Forward state machines with counter management would let the simulation generate and validate E2E-protected PDUs.

### CC4 — No SecOC
No freshness values, MAC computation, authentication header payload, or key management. `grep` confirms zero SecOC references in `src/`.

- **Req**: SWS_SecOC Profiles 1-3
- **Files**: (entirely absent)
- **Severity**: HIGH — PDUs have no cryptographic protection against injection or replay on the network.
- **Simulation posture**: **Tier B** for core, **Tier A plugin** for traffic generation. The simulation does not need full HSM/key-management. But a SecOC plugin should be able to compute MACs and manage freshness values to produce frames that pass validation on the receiving ECU. Without this, the simulation cannot generate traffic for SecOC-protected PDUs.

### CC5 — No AUTOSAR-Compliant Callback API
Uses `std::function<void(const PduFrame&)>` callbacks (`pdu_router.h:52-53`). AUTOSAR SWS_PduR defines:
- `PduR_CanIfRxIndication(PduIdType, PduInfoType*)`
- `PduR_CanIfTxConfirmation(PduIdType, Std_ReturnType)`
- `PduR_CanIfTriggerTransmit(PduIdType, PduInfoType*)`
- `PduR_CanIfCopyRxData`, `PduR_CanIfCopyTxData` (TP API for segmented PDUs)
- `PduR_CanIfStartOfReception`, `PduR_CanIfTxConfirmation`

No TxConfirmation path exists — senders cannot determine if frames were actually transmitted. **Correction**: The gap analysis previously claimed `PduR_ErrorNotification` exists. It does NOT appear in AUTOSAR_CP_SWS_PDURouter R25-11. The actual API surface is ~10 callback families (up/down/TP), not the 4 claimed.

- **Req**: SWS_PduR upcall API (see §Corrections)
- **Files**: `pdu_router.h:52-53`, `pdu_service_impl.cpp`
- **Severity**: **⬆️ CRITICAL** (upgraded from HIGH) — The API gap is larger than originally assessed. Non-standard `std::function<void(const PduFrame&)>` callbacks match none of the 10+ AUTOSAR callback variants. No TxConfirmation path exists.
- **Simulation posture**: **Tier B** — documented deviation. gRPC is the correct control-plane API for a simulation environment. The AUTOSAR callback API lives on the ECU under test. BoAt's `std::function` + gRPC streaming is functionally adequate for simulation traffic distribution.

---

## 🟡 Cross-Cutting — Important Gaps

### CC6 — No Update-Bit Mechanism
AUTOSAR COM defines per-signal update bits [SRS_Com_02030]:
- **Tx side**: Automatically set when application writes signal value; automatically cleared after transmission completes
- **Rx side**: Signal only processed if its update bit is set; deadline monitor only reset if update bit set

Not implemented in `com_signal.cpp`. The schema has no `updateBit` or `HasUpdateBit` field.

- **Req**: [SRS_Com_02030]
- **Files**: `com_signal.cpp`, `pdu_db.schema.json:125-170`
- **Severity**: MEDIUM — Receivers cannot distinguish stale vs. fresh signal values without application-level logic.
- **Simulation posture**: **Tier B** for core (ECU's COM stack handles update-bits on received signals). **Tier A plugin** if the simulation generates signals TO an ECU — the ECU's COM stack checks update-bits on receive; the simulation must set them correctly or the ECU discards the data. An update-bit plugin in the Signal Gateway would handle this.

### CC7 — No Rolling Counter
All E2E profiles include a sequential counter (4-bit or 8-bit). No automated counter increment on send or sequence monitoring on receive.

- **Req**: PRS_E2EProtocol all profiles
- **Files**: `com_signal.cpp:226-273`
- **Severity**: MEDIUM — Counter is essential for message-loss detection; currently only CRC is computed.

### CC8 — No Signal Timeout / Deadline Monitoring
`PduDeadlineConfig` at `pdu_types.h:77-80` operates at the PDU level. AUTOSAR COM supports per-signal or per-signal-group deadline monitoring [SRS_Com_02058], [SRS_Com_02089] with:
- Configurable timeout detection
- Substitution with init value on timeout
- Upper-layer notification on timeout

- **Req**: [SRS_Com_02058], [SRS_Com_02089]
- **Files**: `pdu_router.cpp:288-298`
- **Severity**: MEDIUM — Signal-level timeout detection cannot be configured; only PDU-level timeout exists.

### CC9 — No Signal Invalidation
AUTOSAR COM defines `ComSignalDataInvalidValue` [SRS_Com_02077] for sender-side invalidation, and configurable notification/substitution [SRS_Com_02079], [SRS_Com_02087] on the receiver side.

- **Req**: [SRS_Com_02077], [SRS_Com_02079], [SRS_Com_02087]
- **Files**: `com_signal.cpp`, `pdu_db.schema.json:125-170`
- **Severity**: MEDIUM — No standard mechanism to signal "data not available" to receivers.

### CC10 — No Signal Groups (Atomic Complex Data Types)
AUTOSAR COM groups related signals into `ComIPduSignalGroup` for atomic transfer [SRS_Com_02041], [SRS_GTW_06056].

**Correction**: The original analysis claimed the schema has `isSignalGroup` at `pdu_db.schema.json:154`. **This field does not exist** anywhere in the codebase. The `Signal` definition has no grouping mechanism whatsoever. See §Corrections.

- **Req**: [SRS_Com_02041], ComIPduSignalGroup with dedicated APIs (Com_SendSignalGroup, Com_ReceiveSignalGroup)
- **Files**: `config/pdu_db.schema.json:126-170`, `com_signal.cpp:96-148`
- **Severity**: **⬆️ HIGH** (upgraded from MEDIUM) — Full signal group infrastructure with dedicated APIs, atomicity guarantees, array access, and group-level invalidation is specified in COM R23-11.

### CC11 — No Dynamic-Length Signals
AUTOSAR COM allows at most one dynamic-length signal per I-PDU, placed last in the PDU [SRS_Com_02092], [SRS_Com_02093]. Length derived from I-PDU total length minus static signal sizes. Not modeled.

- **Req**: [SRS_Com_02092], [SRS_Com_02093]
- **Files**: `pdu_db.schema.json:125-170`
- **Severity**: MEDIUM — Cannot model variable-length diagnostic or logging PDUs.

### CC12 — No PduR Buffering Strategy
AUTOSAR PduR defines per-PDU configurable buffering [SRS_GTW_06032]: buffer size (1..n FIFO), overwrite on full, TriggerTransmit with most recent or default value, or no buffer at all.

The implementation at `pdu_router.cpp` keeps no per-PDU buffer; `SendPdu` forwards immediately to CAN/Ethernet.

- **Req**: [SRS_GTW_06032]
- **Files**: `pdu_router.cpp`
- **Severity**: MEDIUM — No queuing/jitter buffering for PDUs; send is synchronous.

### CC13 — No PduR Multicast
AUTOSAR supports transparent multicast where the source module is unaware of multiple destinations [SRS_GTW_06125]. The implementation routes 1:1 — a single PduRoute has one destination.

The schema has `TargetDbIds` arrays (`pdu_db.schema.json:40-46`) suggesting multi-destination intent, but `PduRouter::SendPdu` dispatches to a single route.

- **Req**: [SRS_GTW_06125]
- **Files**: `pdu_router.h:92`, `pdu_router.cpp:120-204`
- **Severity**: MEDIUM — Multi-destination routing requires manual setup of multiple routes.

---

## 🔵 Minor / Architecture Issues

| # | Issue | Details | Files |
|---|-------|---------|-------|
| A1 | **No N-PDU / L-PDU layer separation** | Single `PduFrame` type conflates I-PDU, N-PDU, and L-PDU concepts. AUTOSAR separates these with distinct IDs. | `pdu_types.h:61-67` |
| A2 | **No PduTriggering module** | Transmission scheduling embedded in `TransmissionEngine` class, not a separate BSW module with independent configuration. | `transmission_engine.h:25` |
| A3 | **No ISO-TP integration in PduRouter** | `can_tp` is a standalone plugin with its own C ABI and separate instance. PduRouter has no awareness of transport protocol sessions or `PduR_CanTp*` callbacks. | `pdu_router.cpp`, `src/plugins/can_tp/` |
| A4 | **Deadline: single timeout_factor** | No min/max arrival time, jitter tolerance, consecutive timeout count (debouncing), or recovery callback. | `pdu_types.h:77-80`, `pdu_router.h:131` |
| A5 | **No PduR BswModules concept** | No BSW module registration. Plugin ABI provides frame-level callbacks only, not PduR upcall API. Severity **⬆️ MEDIUM** (upgraded from MINOR) — PduRBswModules is a comprehensive per-module capability registry controlling which callbacks PduR generates. | `plugin.h` |
| A6 | **JSON config, not ARXML** | Flat JSON schema cannot be imported/exported from AUTOSAR tooling (Vector DaVinci, EB tresos). | `config/pdu_db*.json` |
| A7 | **Groups: no hierarchy** | Flat group set. AUTOSAR supports nested groups (parent enables children in cascade). | `pdu_types.h:70-75` |
| A8 | **No PduRDestPduDataProvision** | Routing does not distinguish PDUR_DIRECT vs PDUR_TRIGGERTRANSMIT. **Correction**: Original claimed "explicit/dynamic/direct" — these do NOT exist in the spec. Only PDUR_DIRECT and PDUR_TRIGGERTRANSMIT exist. | `pdu_db.schema.json:40-53` |
| A9 | **No TxConfirmation path** | Senders never know if frames were transmitted. gRPC `SendPdu` returns immediately. Severity **⬆️ HIGH** (upgraded from MINOR) — TxConfirmation is a core PduR callback with exact spec signature `PduR_<User:Lo>TxConfirmation(PduIdType, Std_ReturnType)`. | `pdu_router.cpp:120-204` |
| A10 | **gRPC SubscribePdus 50ms poll** | Each subscriber creates a thread polling a `wait_for(50ms)` loop. Adds latency (worst-case 50ms). | `pdu_service_impl.cpp:216` |

---

## 🔵 Out of Scope

| # | Item | Reason |
|---|------|--------|
| O1 | **ASIL / safety decomposition** | Simulation platform; no functional safety requirements. |
| O2 | **SecOC key management / HSM** | Beyond PDU transport layer; depends on hardware security module. |
| O3 | **Multi-core / multi-partition PDU routing** | Single-process gateway architecture. |

---

## Key Takeaway

The implementation covers the **basic transport layer** well (PduRouter, IpduM LONG header, COM signal pack/unpack, Cyclic/OnChange/Mixed transmission, I-PDU groups, deadline monitoring). The largest gaps are:

1. **PduR routing table** (CC1) — flat map vs. structured routing paths with data provision (PDUR_DIRECT / PDUR_TRIGGERTRANSMIT) and multi-destination
2. **E2E profile state machines** (CC3) — CRC functions exist but no counter management, timeout monitoring, or Protect/Check/Forward lifecycle (requires PRS_E2EProtocol state machines)
3. **Signal-level gatewaying** (CC2) — DB schema defines signal routes but PduRouter never consumes them
4. **Runtime-schema gap** — The schema at `config/pdu_db.schema.json` already models RoutingType, TargetDbIds, signal_routes, and per-PDU schedule fields — the runtime (C++ and Python) ignores them

All three previously missing specs are now integrated — see §Corrections for the complete reassessment.

---

## Corrections from Spec Reassessment (R25-11 / R23-11 / R22-11)

All three previously missing documents (`AUTOSAR_CP_SWS_PDURouter`, `AUTOSAR_SWS_IPDUMultiplexer`, `AUTOSAR_CP_SWS_COM`) have been acquired and cross-referenced against every gap. The reassessment (using spec-reference, pdu-database, and explore agents in parallel) found the following:

### 1. Factual Errors in Original Analysis

| # | Error in Original | Corrected Finding | Source |
|---|-------------------|-------------------|--------|
| E1 | "explicit/dynamic/direct" routing categories (CC1, A8) | Do NOT exist. PduR defines **PDUR_DIRECT** and **PDUR_TRIGGERTRANSMIT** only | PDURouter §10.2.8 |
| E2 | `PduR_ErrorNotification` callback (CC5) | Does NOT exist in the PduR spec | PDURouter §8.3.3 |
| E3 | SHORT header "2 bytes: 12-bit index + 4-bit length" (ETH2) | Actual spec defines **4 bytes: 24-bit Header ID + 8-bit DLC**. Also `IPDUM_HEADERTYPE_NONE` exists. | IpduM §7.3.1 |
| E4 | `[SRS_Com_02041]` cited as I-PDU length validation (CAN7) | This requirement is about **atomic complex data types** for signal groups, not length validation | COM R23-11 |
| E5 | `isSignalGroup` at `pdu_db.schema.json:154` (CC10) | **Field does not exist** anywhere in the codebase | grep across all files |
| E6 | `NPduNumber` per-PDU container triggering (ETH3) | Parameter not found in IpduM spec. Actual model uses `IpduMContainedTxPduTrigger`, `IpduMContainerTxSendTimeout`, `IpduMContainerTxSizeThreshold` | IpduM §7.3.3.2, §7.3.4 |
| E7 | Schema at `src/hil/pdu/pdu_db.schema.json` | Actual location is **`config/pdu_db.schema.json`** (172 lines) | Filesystem |
| E8 | `cli/boat_cli/cmd.py` referenced for PDU commands | **File does not exist.** PDU CLI is in `cli/boat_cli/pdu.py` | Filesystem |
| E9 | "I-PDU counter" as missing feature (CC7) | COM R23-11 explicitly states **"Removed I-PDU counter and I-PDU replication"** — not a gap | COM §1 line 27 |
| E10 | 4 callbacks claimed for PduR upcall API | Actual API surface: **~10 callback families** — LoRxIndication, LoTxConfirmation, LoTriggerTransmit (CanIf→PduR); UpTransmit (PduR→Com); LoTpCopyRxData, LoTpRxIndication, LoTpStartOfReception, LoTpCopyTxData, LoTpTxConfirmation (TP path); plus BswModules-driven naming | PDURouter §§8.3.2–8.3.4 |

### 2. Severity Changes

| Gap | Old Severity | New Severity | Reason |
|-----|:-----------:|:-----------:|--------|
| **CC5** | HIGH | **⬆️ CRITICAL** | 10+ callback variants (not 4), none matched by `std::function<void(PduFrame&)>` |
| **CC6** | MEDIUM | **⬆️ HIGH** | Full Tx/Rx lifecycle with configurable clear-once-sent/clear-on-confirmed; Rx discards if not set |
| **CC8** | MEDIUM | **⬆️ HIGH** | Signal-level timeout with substitution, notification, and first-timeout fully spec'd |
| **CC9** | MEDIUM | **⬆️ HIGH** | Complete invalidation lifecycle: Com_InvalidateSignal → NOTIFY or REPLACE on Rx |
| **CC10** | MEDIUM | **⬆️ HIGH** | Full signal group API (Com_SendSignalGroup, atomicity, array access) confirmed |
| **CC12** | MEDIUM | **⬆️ HIGH** | Rich buffering model (FIFO, last-is-best, dedicated/global, TriggerTransmit) not just "no queue" |
| **CC13** | MEDIUM | **⬆️ HIGH** | Multicast is a core PduR capability, not minor; 0..* PduRDestPdu per path |
| **ETH2** | MEDIUM | **⬆️ HIGH** | SHORT header size corrected to 4B (not 2B); gap is larger than originally stated |
| **ETH3** | MEDIUM | **⬆️ HIGH** | Per-contained-PDU triggering model is richer (trigger flags, timeouts, size thresholds) |
| **A5** | MINOR | **⬆️ MEDIUM** | PduRBswModules is a comprehensive per-module capability registry with API flag matrix |
| **A8** | MINOR | **⬇️ REMOVED** | "explicit/dynamic/direct" categories don't exist; replace with "No PDUR_DIRECT / PDUR_TRIGGERTRANSMIT" |
| **A9** | MINOR | **⬆️ HIGH** | TxConfirmation is a core callback; merged into CC5 assessment |
| **CAN2** | HIGH | **⬇️ MEDIUM** | Schema already captures all essential timing params; missing controller binding overlaps with CAN1 |
| **CC7** | MEDIUM | **SAME (HIGH)** | I-PDU counter removed from COM spec; E2E state machines remain in PRS_E2EProtocol |
| **CC1** | HIGH | HIGH | Still HIGH but corrected: dest categories are PDUR_DIRECT/TRIGGERTRANSMIT, not explicit/dynamic/direct |
| **CAN7** | MEDIUM | MEDIUM | Corrected requirement reference ([SWS_Com_00574] not [SRS_Com_02041]) |

### 3. Schema vs. Runtime Gap Pattern

Several gaps are **not schema gaps** but **runtime gaps** — the schema already has the necessary fields:

| Gap | Schema Has | Runtime Does |
|-----|-----------|-------------|
| **CC1** | `RoutingType` (0/1/2), `TargetDbIds` (array), `SourceDbId`, `Direction` | Flat `unordered_map<uint32_t, PduRoute>` — ignores all routing table fields |
| **CC2** | `signal_routes` array (SrcDbId → DstDbId per signal) | `PduRouter` never reads or applies signal routes |
| **CC13** | `TargetDbIds` array (explicit multi-destination support) | `SendPdu` dispatches to a single route |
| **ETH3** | Child ETH_PDU entries have per-PDU `SendType`/`CycleTime`/`CycleTimeFast` | Container uses a single shared schedule |

### 4. Key Spec Details by Gap

**CC1** (PduR RoutingTable): The spec defines `PduRRoutingPath` (0..*) each containing `PduRSrcPdu` (1) + `PduRDestPdu` (0..*). Multi-destination = multiple `PduRDestPdu` under one path. `PduRDestPduDataProvision` = PDUR_DIRECT or PDUR_TRIGGERTRANSMIT. `PduRQueueDepth` (0 = no buffer, 1 = last-is-best, >1 = FIFO). `PduRQueueingStrategy` = COMMON_QUEUE or DEDICATED_QUEUE.

**CC5** (Callback API): Exact signatures confirmed:
- `PduR_<User:Lo>RxIndication(PduIdType, const PduInfoType*)` — Service 0x42
- `PduR_<User:Lo>TxConfirmation(PduIdType, Std_ReturnType)` — Service 0x40
- `PduR_<User:Lo>TriggerTransmit(PduIdType, PduInfoType*)` — Service 0x41
- `PduR_<User:Up>Transmit(PduIdType, const PduInfoType*)` — Service 0x49
- TP: `PduR_<User:LoTp>CopyRxData`, `RxIndication`, `StartOfReception`, `CopyTxData`, `TxConfirmation`
- `<User:Lo>` etc. are replaced at config time based on the BswModules entry (e.g., `PduR_CanIfRxIndication`)

**CC6** (Update-bit): COM R23-11 §§7.9–7.9.2 defines:
- Tx: `Com_SendSignal` auto-sets update-bit; clear behavior configurable (on Transmit/Confirmation/TriggerTransmit)
- Rx: signal only processed if update-bit is set ([SWS_Com_00324]); discarded if not set ([SWS_Com_00802])
- All update-bits cleared during initialization ([SWS_Com_00117])

**CC10** (Signal Groups): COM R23-11 §7.4, §10.2.14 defines `ComIPduSignalGroup` with:
- `Com_SendSignalGroup`, `Com_ReceiveSignalGroup` APIs
- `Com_SendSignalGroupArray` / `Com_ReceiveSignalGroupArray` (uint8[] access)
- `Com_InvalidateSignalGroup`
- Atomicity guarantee: all-or-nothing transfer

**CC12** (Buffering): PduR defines:
- `PduRQueueDepth = 0` → no buffering
- `PduRQueueDepth = 1` → last-is-best (overwrite)
- `PduRQueueDepth > 1` → FIFO queue ([SWS_PduR_00785])
- FIFO flush on overflow ([SWS_PduR_00255])
- Multicast buffers are independent per destination ([SWS_PduR_00307])
- Fan-in must use PDUR_COMMON_QUEUE ([SWS_PduR_CONSTR_00872])

**ETH2** (Header formats): IpduM defines three header types:
- `IPDUM_HEADERTYPE_LONG`: 64 bits (32-bit Header ID + 32-bit DLC)
- `IPDUM_HEADERTYPE_SHORT`: 32 bits (24-bit Header ID + 8-bit DLC)
- `IPDUM_HEADERTYPE_NONE`: Static layout, no headers
- Byte order configured via `IpduMHeaderByteOrder`

**ETH3** (Per-PDU triggering): IpduM defines:
- `IpduMContainedTxPduTrigger`: IPDUM_TRIGGER_ALWAYS / IPDUM_TRIGGER_NEVER
- `IpduMContainerTxFirstContainedPduTrigger`
- `IpduMContainerTxSendTimeout` / `IpduMContainedTxPduSendTimeout`
- `IpduMContainerTxSizeThreshold`
- `IpduMContainedTxPduPriorityHandling` — re-sort by priority
- Collection: `IPDUM_COLLECT_LAST_IS_BEST` or `IPDUM_COLLECT_QUEUED`

### 5. Remaining Spec Gaps

The following gaps are **confirmed correct** and remain as originally stated:

| Gap | Severity | Spec Basis |
|-----|----------|-----------|
| **CC3** (No E2E state machines) | HIGH | PRS_E2EProtocol (separate from COM) |
| **CC4** (No SecOC) | HIGH | SWS_SecOC — entirely absent from codebase |
| **CC7** (No rolling counter) | HIGH | E2E profiles require counter; COM removed I-PDU counter |
| **CAN1** (No CanHardwareObject) | HIGH | SWS_CanIf requires controller/hardware refs |
| **CAN3** (No DLC encoding) | HIGH | ISO 11898-1 §10.4.2.2 |
| **CAN4** (No BRS flag) | MEDIUM | CAN FD per ISO 11898-1 |
| **CAN5** (No CAN filter) | MEDIUM | CanHardwareObjectFilter |
| **ETH1** (No EthControllerRef) | HIGH | SoAdBswConfig, EthController, TcpIpSocket |
| **ETH4** (No IPv6 extension headers) | MEDIUM | RFC 8200 §4 |
| **ETH5** (No multicast addressing) | MEDIUM | SoAdSocketRoute multicast |
| **A1**–A7, A10 | MINOR/MEDIUM | Architecture issues, no spec change |

---

## Gap Classification Summary (Simulation Context)

All 38 gaps reclassified from the simulation's perspective. AUTOSAR severity is retained for spec compliance tracking; simulation tier indicates what actually matters for BoAt as a simulation platform.

| Gap | AUTOSAR | Sim Tier | Category | Rationale |
|-----|:-------:|:--------:|----------|-----------|
| **CAN1** | HIGH | **B** | Doc. deviation | Symbolic interface names adequate; HW controller abstraction on ECU |
| **CAN2** | MEDIUM | **A** | Plugin | Realistic CAN transmission scheduling (Advanced Tx Plugin) |
| **CAN3** | HIGH | **A** | Core fix | ISO 11898-1 DLC encoding — protocol correctness bug |
| **CAN4** | MEDIUM | **A** | Core fix | BRS flag not set — CAN FD transmits at wrong bitrate |
| **CAN5** | MEDIUM | **B** | Doc. deviation | Software CAN ID filtering adequate for simulation |
| **CAN6** | MEDIUM | **B** | Doc. deviation | Simple CAN ID → PDU ID match works for simulation |
| **CAN7** | MEDIUM | **B** | Doc. deviation | Simulation intentionally permissive on payload size |
| **ETH1** | HIGH | **B** | Doc. deviation | Inline IP/port fields adequate; SoAd/TcpIp on ECU |
| **ETH2** | HIGH | **A** | Core fix | SHORT header for protocol-correct IpduM containers |
| **ETH3** | HIGH | **A** | Plugin | Per-PDU container triggering (Signal Gateway plugin) |
| **ETH4** | MEDIUM | **A** | Core fix | IPv6 extension header parsing — protocol correctness |
| **ETH5** | MEDIUM | **A** | Plugin | Multicast PDU addressing (PDU Router plugin) |
| **CC1** | HIGH | **A** | Plugin | Structured PDU routing table with multi-destination (PDU Router plugin) |
| **CC2** | HIGH | **A** | Plugin | Cross-bus signal remapping (Signal Gateway plugin) |
| **CC3** | HIGH | **A** | Plugin | E2E Protect/Check/Forward with counter management (E2E/SecOC plugin) |
| **CC4** | HIGH | **B/A** | Core B, Plugin A | Core: no crypto needed. Plugin: generate SecOC-valid traffic |
| **CC5** | CRTCL | **B** | Doc. deviation | gRPC is the correct simulation control-plane API |
| **CC6** | HIGH | **B/A** | Core B, Plugin A | Core: ECU COM handles update-bits. Plugin: set them when generating traffic TO ECUs |
| **CC7** | HIGH | **A** | Plugin | Rolling counter (part of E2E/SecOC plugin) |
| **CC8** | HIGH | **A** | Plugin | Signal-level timeout with substitution (Signal Monitoring plugin) |
| **CC9** | HIGH | **A** | Plugin | Signal invalidation lifecycle (Signal Monitoring plugin) |
| **CC10** | HIGH | **A** | Plugin | Signal groups — atomic transfer API (Signal Gateway plugin) |
| **CC11** | MEDIUM | **A** | Plugin | Dynamic-length signals (Signal Gateway plugin) |
| **CC12** | HIGH | **A** | Plugin | PDU buffering — FIFO, last-is-best, TriggerTransmit (PDU Router plugin) |
| **CC13** | HIGH | **A** | Plugin | Multicast / 1:n routing paths (PDU Router plugin) |
| **A1** | MINOR | **B** | Doc. deviation | N-PDU/L-PDU separation unnecessary for simulation |
| **A2** | MINOR | **B** | Doc. deviation | TransmissionEngine class adequate for scheduling |
| **A3** | MINOR | **B** | Doc. deviation | Standalone CanTp plugin is the correct architecture |
| **A4** | MINOR | **B** | Doc. deviation | Single timeout_factor adequate for simulation deadline monitoring |
| **A5** | MEDIUM | **A** | Plugin | BswModules capability registry (part of PDU Router plugin) |
| **A6** | MINOR | **B** | Doc. deviation | JSON is the simulation config format; ARXML import tooling separate |
| **A7** | MINOR | **B** | Doc. deviation | Flat I-PDU groups adequate for simulation |
| **A8** | REMOVED | **A** | Plugin | PDUR_DIRECT/TRIGGERTRANSMIT data provision (part of PDU Router plugin) |
| **A9** | HIGH | **B** | Doc. deviation | gRPC returns immediately; Tx confirmation on ECU |
| **A10** | MINOR | **B** | Doc. deviation | 50ms poll latency acceptable for simulation; optimize if needed |
| **O1** | — | — | Out of scope | No functional safety requirements |
| **O2** | — | — | Out of scope | HSM/key management not needed (traffic generation only) |
| **O3** | — | — | Out of scope | Single-process gateway |

### Summary

| Tier | Count | Action |
|------|:-----:|--------|
| **A — Core fix** | 5 | Fix in core: CAN3 (DLC), CAN4 (BRS), ETH2 (SHORT header), ETH4 (IPv6), CAN2 (scheduling) |
| **A — Plugin** | 16 | Implement as plugins per §Plugin Opportunities below |
| **B — Documented** | 17 | No action; deviation rationale documented above |
| **Out of scope** | 3 | No action |

---

## Plugin Architecture Opportunities

The gaps below are candidates for new plugins. Each plugin operates via the existing plugin ABI (`plugin.h`) and has access to the PduRouter, CAN/Ethernet registries, and tick timer.

### 1. PDU Router Plugin (`pdu_router_plugin`)

Implements full AUTOSAR-aligned PDU routing as a plugin rather than replacing the core PduRouter (which stays minimal — 1:1 transport). The plugin intercepts frames from the core PduRouter and applies structured routing logic.

**Covers:** CC1, CC12, CC13, A5, A8, ETH5

**Features:**
- `PduRRoutingTable` with `PduRRoutingPath` → `PduRSrcPdu` + `PduRDestPdu` (0..* per path)
- `PduRDestPduDataProvision`: PDUR_DIRECT vs PDUR_TRIGGERTRANSMIT
- Multi-destination routing (fan-out from one source to multiple destinations)
- Per-destination buffering: `PduRQueueDepth` (0=none, 1=last-is-best, >1=FIFO)
- `PduRQueueingStrategy`: COMMON_QUEUE vs DEDICATED_QUEUE
- `PduRBswModules` capability registry for config-driven callback generation
- Transparent multicast
- Multicast PDU addressing (ETH5)

**Spec basis:** AUTOSAR_CP_SWS_PDURouter R25-11 (8692 lines, now in spec/text/)

### 2. Signal Gateway Plugin (`signal_gateway_plugin`)

Connects the existing building blocks (com_signal pack/unpack, signal_routes in schema, PduRouter transport) into a runtime signal routing engine.

**Covers:** CC2, CC10, CC11, ETH3, CC6 (update-bit generation on Tx)

**Features:**
- Reads `signal_routes` from PduDatabase and sets up cross-bus signal mappings
- `ComGwMapping` hierarchy: `SourceDescription` → `DestinationDescription` → individual signal maps
- 1:n signal routing between I-PDUs (CAN → Ethernet, Ethernet → CAN, CAN → CAN)
- Signal groups: atomic pack/unpack of `ComIPduSignalGroup` with dedicated APIs
- Dynamic-length signals (UINT8_DYN, last in PDU, length from PDU size)
- Per-PDU container triggering (ETH3): `IpduMContainedTxPduTrigger`, timeouts, size thresholds
- Update-bit auto-set on Tx (CC6) — critical when sending signals TO an ECU's COM stack

**Spec basis:** AUTOSAR_CP_SWS_COM R23-11 (11171 lines, now in spec/text/), AUTOSAR_SWS_IPDUMultiplexer R22-11 (5689 lines)

### 3. E2E / SecOC Traffic Generation Plugin (`e2e_secoc_plugin`)

Generates protocol-correct E2E-protected and SecOC-authenticated PDU traffic. Does NOT implement full HSM or key management — uses configured keys to compute MACs and freshness values.

**Covers:** CC3, CC4, CC7

**Features:**
- E2E profile state machines: Protect (Tx counter increment + CRC), Check (Rx counter validation + CRC), Forward (pass-through with data ID remap)
- Profiles 1/2/4/5/7/22 per PRS_E2EProtocol
- Per-PDU E2E config: DataId, CounterOffset, MaxDeltaCounter, MaxNoNewOrRepeatedData
- SecOC traffic generation: compute Freshness Values, MACs per configured key, insert Auth Header into payload
- Does NOT implement: HSM integration, key derivation, secure key storage — these live on the ECU

**Spec basis:** AUTOSAR_PRS_E2EProtocol, AUTOSAR_CP_SWS_SecOC R25-11 (already in spec/text/)

### 4. Signal Monitoring Plugin (`signal_monitor_plugin`)

Monitors received PDUs at signal level, providing timeout detection, invalidation, and notification callbacks.

**Covers:** CC8, CC9

**Features:**
- Per-signal timeout with configurable `ComTimeout`, `ComFirstTimeout`
- Substitution with init value on timeout
- Upper-layer notification via `<ComUser_CbkRxTOut>` callback
- Signal invalidation: `ComSignalDataInvalidValue` on send, NOTIFY/REPLACE on receive

**Spec basis:** AUTOSAR_CP_SWS_COM R23-11 §§7.3.4, 7.3.6

### 5. Advanced Transmission Plugin (`advanced_tx_plugin`)

Extends the core TransmissionEngine with AUTOSAR-aligned scheduling.

**Covers:** CAN2

**Features:**
- `CanIfTxPduTriggering`: separate entity linking PDU → CanController → HTH → TxConfirmation
- Per-controller Tx confirmation modes
- Optional integration with PDU Router plugin for structured scheduling

**Spec basis:** AUTOSAR_SWS_CAN_Interface

### Plugin Priority Summary

| Priority | Plugin | Gaps Covered | Rationale |
|:--------:|--------|-------------|-----------|
| **1** | Signal Gateway | CC2, CC10, CC11, ETH3, CC6 | Most immediate simulation value — cross-bus signal routing with correct protocol behavior |
| **2** | E2E / SecOC | CC3, CC4, CC7 | Required to generate traffic for ECUs with E2E/SecOC on critical signal paths |
| **3** | PDU Router | CC1, CC12, CC13, A5, A8, ETH5 | Structural routing needed for multi-bus gateway simulation |
| **4** | Signal Monitoring | CC8, CC9 | Useful for ECU integration testing and timeout detection |
| **5** | Advanced Tx | CAN2 | Nice-to-have for realistic CAN scheduling behavior |
