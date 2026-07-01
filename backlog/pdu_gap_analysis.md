# PDU Gap Analysis — BoAt vs. AUTOSAR

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
| **AUTOSAR SWS_PDURouter** | ❌ **MISSING** — would need separate acquisition |
| **AUTOSAR SWS_IPDUM** | ❌ **MISSING** — would need separate acquisition |
| **AUTOSAR SWS_COM** (dedicated SWS) | ❌ **MISSING** — only SRS and old ComManager available |

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

| Feature | AUTOSAR Equivalent | Files |
|---------|--------------------|-------|
| **PduRouter** (central routing hub) | PduR module | `pdu_router.h/cpp` |
| **I-PDU Groups** (enable/disable gating) | [SRS_Com_02090], [SRS_Com_00218] | `pdu_types.h:70-75`, `pdu_router.cpp:233-280` |
| **IpduM LONG header** (serialize/deserialize) | SWS_IpduM container format | `ipdumcontainer.h/cpp` |
| **Intel/Motorola bit packing** | [BSW02078] COM endianness | `com_signal.h:48-58`, `com_signal.cpp:18-88` |
| **E2E CRC8/16/32** (polynomials) | PRS_E2EProtocol Profiles 2/4/5/7 | `com_signal.h:81-83`, `com_signal.cpp:230-273` |
| **Transmission Engine** (Cyclic/OnChange/Mixed) | [SRS_Com_02083] | `transmission_engine.h/cpp` |
| **CAN ID ↔ PDU ID mapping** | `CanIfTxPdu.CanId`, `PduRoute.can_id` | `pdu_types.h:31`, `pdu_router.cpp:156` |
| **11-bit and 29-bit CAN** | `Can_IdType` in SWS_CAN_Driver | `pdu_db.schema.json:76-77` |
| **IPv4/UDP/IpduM path** | SoAd + IpduM over Ethernet | `ipdumcontainer.cpp:123-171` |
| **IPv6/UDP/IpduM path** | SoAd + IpduM over Ethernet | `ipdumcontainer.cpp:173-211` |
| **Deadline monitoring** (per-PDU timeout) | [SRS_Com_02058] | `pdu_types.h:77-80`, `pdu_router.cpp:288-298` |
| **BRS/FDF/ESI flag ABI** (plugin SDK) | CAN FD flags per ISO 11898-1 | `plugin.h:17-18` |
| **Auto-config via PduDatabase** | ComIPdu (partial) | `pdu_message_node.py:61-104`, `pdu_db.py` |
| **PDU DB schema** with routing types | PduR routing path (partial) | `pdu_db.schema.json` |

---

## 🔴 CAN — Critical Gaps

### CAN1 — No CanHardwareObject / CanControllerRef
Bus names are free-form strings (`"vcan0"`, `"Powertrain_CAN"`). AUTOSAR requires `CanIfTxPduCanControllerRef` linking to a `CanController` instance, and `CanIfTxPduCanHwObjectRef` linking to HTH (Hardware Transmit Handle). Neither exists.

- **Req**: [SRS_Can_01074], CanIfTxPduCanControllerRef / CanHwObjectRef
- **Files**: `pdu_db.schema.json:31`, all `pdu_db_*.json` bus fields
- **Severity**: HIGH — Without controller/hardware object references, CAN configuration cannot map to real hardware.

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

---

## 🟡 CAN — Important Gaps

### CAN4 — No BRS Flag Propagation
`BoatCanFrame.flags` supports `CANFD_BRS=0x01`, `CANFD_FDF=0x04`. The schema has a `BRS` field at `pdu_db.schema.json:80-81`. But `PduRouter::SendPdu()` zero-initializes the `CanFrame{}` struct (`pdu_router.cpp:157`) and never sets `flags`. Incoming frames have their flags ignored.

- **Req**: CAN FD BRS per ISO 11898-1
- **Files**: `pdu_router.cpp:157-161`
- **Severity**: MEDIUM — CAN FD works but at arbitration bitrate only.

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

- **Req**: [SRS_Com_02041] — COM must validate I-PDU length
- **Files**: `pdu_router.cpp:159`
- **Severity**: MEDIUM — Silent data corruption in misconfigured scenarios.

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

---

## 🟡 Ethernet — Important Gaps

### ETH2 — IpduM: Only LONG Header, No SHORT
`IpduMSerialize` always produces an 8-byte per-entry header (4B PDU ID + 4B DLC). The AUTOSAR SHORT header (2 bytes: 12-bit index + 4-bit length) is not supported.

For containers with ≤16 PDUs and ≤64 bytes each, LONG header wastes 6 bytes per entry.

- **Req**: SWS_IpduM ContainerHeaderType
- **Files**: `ipdumcontainer.cpp:10-26`
- **Severity**: MEDIUM — Redundant overhead for compact containers; no header format negotiation.

### ETH3 — No NPduNumber / Per-PDU Triggering in Containers
All PDUs in a container share the same cycle time (`pdu_db_test.json:296-297`). AUTOSAR `IpduMContainerPduTriggering` supports independent triggering per sub-PDU: cyclic at different rates, onChange, or scheduled via NPduNumber (which sub-PDU is transmitted on each container cycle).

- **Req**: IpduMContainerPdu.NPduNumber, IpduMContainerPduTriggering
- **Files**: `pdu_db.schema.json:94-98`, `gen_pdu_db_test.py:296-297`
- **Severity**: MEDIUM — All contained PDUs must share the same transmission schedule; cannot differentiate fast vs slow signals in one container.

### ETH4 — IPv6: No Extension Header Support
`ParseUdpIpPacket` (`ipdumcontainer.cpp:222-233`) assumes Next Header = 17 (UDP) immediately after the 40-byte fixed IPv6 header. IPv6 packets with Hop-by-Hop (0), Routing (43), Fragment (44), or Destination Options (60) extension headers will be silently rejected.

- **Req**: [SWS_EthIf_00308], RFC 8200 §4
- **Files**: `ipdumcontainer.cpp:229`
- **Severity**: MEDIUM — Fails on any real-world IPv6 network that uses extension headers.

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
- `PduRDestPdu` with categories (explicit, dynamic, direct)
- `PduRSrcPdu` with source reference
- Multi-destination routing paths

The flat map cannot express multi-hop routes, gateway characteristics, or dest category selection.

- **Req**: SWS_PduR RoutingTable, DestPdu, SrcPdu
- **Files**: `pdu_router.h:92`, `pdu_db.schema.json:40-53`
- **Severity**: HIGH — Routing is simple 1:1 PDU ID → transport; no structured routing with multi-destination or gateway support.

### CC2 — No Signal-Level Gateway Routing
`pdu_db.schema.json:108-122` defines `signal_routes` as `SrcDbId.SrcSignalId → DstDbId.DstSignalId` pairs. `PduRouter` never reads or applies them — routing is PDU-level only. AUTOSAR [SRS_GTW_06055] requires 1:n signal routing between I-PDUs.

The implementation has all the building blocks (com_signal pack/unpack, PduRouter routing, signal_routes in DB) but no runtime engine that connects them.

- **Req**: [SRS_GTW_06055], PduR_GwRoutingPath, [SRS_Com_02112]
- **Files**: `pdu_router.cpp` (unused signal routes), `pdu_db.schema.json:108-122`
- **Severity**: HIGH — Cross-bus signal remapping requires external orchestration; cannot route CAN signal → Ethernet PDU without custom code.

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

### CC4 — No SecOC
No freshness values, MAC computation, authentication header payload, or key management. `grep` confirms zero SecOC references in `src/`.

- **Req**: SWS_SecOC Profiles 1-3
- **Files**: (entirely absent)
- **Severity**: HIGH — PDUs have no cryptographic protection against injection or replay on the network.

### CC5 — No AUTOSAR-Compliant Callback API
Uses `std::function<void(const PduFrame&)>` callbacks (`pdu_router.h:52-53`). AUTOSAR SWS_PduR defines:
- `PduR_CanIfRxIndication(PduIdType, PduInfoType*)`
- `PduR_CanIfTxConfirmation(PduIdType, Std_ReturnType)`
- `PduR_CanIfTriggerTransmit(PduIdType, PduInfoType*)`
- `PduR_ErrorNotification(PduIdType, PduR_UrgentMessage)`

No TxConfirmation path exists — senders cannot determine if frames were actually transmitted.

- **Req**: SWS_PduR upcall API
- **Files**: `pdu_router.h:52-53`, `pdu_service_impl.cpp`
- **Severity**: HIGH — Non-standard API prevents integration with existing AUTOSAR COM stacks; no transmission confirmation.

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
AUTOSAR COM groups related signals into `ComIPduSignalGroup` for atomic transfer [SRS_Com_02041], [SRS_GTW_06056]. The schema has `isSignalGroup` at `pdu_db.schema.json:154` but with no grouping mechanism or atomic transfer guarantee.

- **Req**: [SRS_Com_02041]
- **Files**: `pdu_db.schema.json:125-170`, `com_signal.cpp:96-148`
- **Severity**: MEDIUM — Multiple related signals may be observed in an inconsistent state by the receiver.

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
| A5 | **No PduR BswModules concept** | No BSW module registration. Plugin ABI provides frame-level callbacks only, not PduR upcall API. | `plugin.h` |
| A6 | **JSON config, not ARXML** | Flat JSON schema cannot be imported/exported from AUTOSAR tooling (Vector DaVinci, EB tresos). | `config/pdu_db*.json` |
| A7 | **Groups: no hierarchy** | Flat group set. AUTOSAR supports nested groups (parent enables children in cascade). | `pdu_types.h:70-75` |
| A8 | **No PduR dest categories** | Routing does not distinguish explicit, dynamic, or direct routing categories. | `pdu_db.schema.json:40-53` |
| A9 | **No TxConfirmation path** | Senders never know if frames were transmitted. gRPC `SendPdu` returns immediately. | `pdu_router.cpp:120-204` |
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

1. **PduR routing table** (CC1) — flat map vs. structured routing paths with dest categories and multi-destination
2. **E2E profile state machines** (CC3) — CRC functions exist but no counter management, timeout monitoring, or Protect/Check/Forward lifecycle
3. **Signal-level gatewaying** (CC2) — DB schema defines signal routes but PduRouter never consumes them
4. **ABSENT documents** — SWS_PDURouter and SWS_IPDUM would provide definitive routing table and container configuration detail

Two missing spec documents (`AUTOSAR_SWS_PDURouter`, `AUTOSAR_SWS_IPDUM`) would be needed to close several gaps with certainty.
