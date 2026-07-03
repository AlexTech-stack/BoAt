# System Test Results — 2026-07-03

**Date:** 2026-07-03T13:47:37.415563
**Host:** agn-testcomputer, x86_64
**Gateway:** /home/testuser/ProjectBoat/boat-platform/build/debug/src/gateway/grpc_gateway/boat_gateway

| TC# | Category | Scope | Verdict | Comment |
|-----|----------|-------|---------|--------|
| 1 | Gateway Startup | Gateway startup — no plugins | OK |  |
| 2 | Gateway Startup | Gateway startup — PduRouter | NOK | PduRouter plugin loaded but gRPC returns NOT_FOUND (gateway code bug) |
| 3 | Gateway Startup | Gateway startup — all plugins | NOK | PduRouter NOT_FOUND. Plugin loading issue |
| 4 | Gateway Startup | Gateway startup — no CAN | OK | Gateway runs without CAN interfaces |
| 5 | Gateway Startup | Gateway startup — Ethernet | OK | Both vcan0 and veth0 listed |
| 10 | CAN Communication | Send CAN deprecated | NOT_EXECUTED | boat can send removed from CLI; use boat frame send |
| 11 | CAN Communication | Send CAN FrameService | OK |  |
| 12 | CAN Communication | Subscribe CAN | NOT_EXECUTED | Requires two terminals |
| 13 | CAN Communication | List CAN interfaces | OK | Both vcan interfaces listed with metadata |
| 14 | CAN Communication | Detect CAN hardware | OK | vcan interfaces appear (gateway-query mode) |
| 15 | CAN Communication | Send CAN FD | OK | CAN FD frame accepted by gateway |
| 20 | Ethernet | Send ETH deprecated | NOT_EXECUTED | boat eth command removed from CLI |
| 21 | Ethernet | Send ETH FrameService | OK |  |
| 22 | Ethernet | Subscribe ETH | NOT_EXECUTED | Requires two terminals |
| 30 | Plugin | Register plugin | NOK | Plugin not confirmed. list: ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━ |
| 31 | Plugin | Unload plugin | OK | Plugin unloaded |
| 32 | Plugin | can_responder trigger | OK | 0x123 triggered 0x234 response on vcan1 |
| 33 | Plugin | can_responder wrong iface | OK | No response on wrong interface (correct) |
| 34 | Plugin | vehicle_dynamics CAN | OK | CAN frames 0x100/0x101 observed |
| 35 | Plugin | vehicle_dynamics ETH | OK | Ethernet frames observed |
| 36 | Plugin | vehicle_dynamics signals | NOT_EXECUTED | boat signal subscribe command not in current CLI |
| 37 | Plugin | network_sim stderr | OK | network_sim logging observed |
| 38 | Plugin | sensor_model stderr | OK | sensor_model logging observed |
| 39 | Plugin | ABI rejection | NOT_EXECUTED | Requires v7 plugin .so for testing |
| 40 | PDU Routing | PDU without plugin | OK | Correctly rejected |
| 41 | PDU Routing | Configure PDU route | NOK | PduRouter plugin loaded but gRPC returns NOT_FOUND (gateway code bug) |
| 42 | PDU Routing | Send PDU | NOK | PDU send error: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 43 | PDU Routing | Subscribe PDU | NOT_EXECUTED | Requires two terminals |
| 44 | PDU Routing | Remove PDU route | NOK | Remove failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 45 | PDU Routing | Cyclic PDU | NOK | Cyclic route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 46 | PDU Routing | PDU Ethernet | NOK | ETH route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 47 | PDU Routing | PDU IP/UDP | NOK | IP/UDP route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 50 | I-PDU Groups | Create group | NOK | Group create failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 51 | I-PDU Groups | Disable group | NOK | Disable failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 52 | I-PDU Groups | Enable group | NOK | Enable failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 53 | I-PDU Groups | Multi-PDU group | NOK | Both PDUs not seen after enable:  |
| 60 | CanTp | CanTp single-frame | NOK | CanTp frame not on 0x7E0. CLI: ╭───────────────────── Traceback (most recent cal |
| 61 | CanTp | CanTp multi-frame | NOK | No frame on 0x7E0:  |
| 70 | Transmission Schedule | Cyclic schedule | NOK | Route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 71 | Transmission Schedule | OnChange schedule | NOK | OnChange route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 72 | Transmission Schedule | Mixed schedule | NOK | Mixed route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded
 |
| 80 | Replay | Replay start | NOT_EXECUTED | Requires pre-recorded trace file |
| 81 | Replay | Replay seek | NOT_EXECUTED | Depends on TC80 |
| 82 | Replay | Replay pause | NOT_EXECUTED | Depends on TC80 |
| 90 | FrameService | FrameService CAN | OK |  |
| 91 | FrameService | FrameService filter | NOT_EXECUTED | Requires two terminals |
| 92 | FrameService | FrameService TCP | NOK | TCP frame not sent: Frame not accepted (unrecognized bus type or no handler)
 |
| 93 | FrameService | FrameService PDU | NOK | PDU frame send failed: Frame not accepted (unrecognized bus type or no handler)
 |
| 100 | Error Handling | Non-existent iface | OK | Graceful error for invalid iface |
| 101 | Error Handling | PDU no plugin | OK | Correctly rejected |
| 102 | Error Handling | Invalid JSON | INCONCLUSIVE | Unexpected result (rc=0): ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━ |
| 103 | Error Handling | Plugin .so not found | OK | Error reported for missing .so |
| 110 | Signal Bus | Signal bus | NOT_EXECUTED | boat signal/bus subscribe not in current CLI |
| 120 | Multi-Bus | Concurrent CAN+ETH | NOT_EXECUTED | Requires three terminals |
| 121 | Multi-Bus | Multiple subscribers | NOT_EXECUTED | Requires three terminals |
| 130 | Performance/Stress | High-frequency send | OK | 100 frames sent with 0 failures |
| 131 | Performance/Stress | Extended uptime | OK | Gateway alive after 60s (113 frames) |

## Summary

- **Total:** 57  **OK:** 22  **NOK:** 20  **INCONCLUSIVE:** 1  **NOT_EXECUTED:** 14

## Non-OK Details

### TC2: Gateway startup — PduRouter — NOK

PduRouter plugin loaded but gRPC returns NOT_FOUND (gateway code bug)

### TC3: Gateway startup — all plugins — NOK

PduRouter NOT_FOUND. Plugin loading issue

### TC10: Send CAN deprecated — NOT_EXECUTED

boat can send removed from CLI; use boat frame send

### TC12: Subscribe CAN — NOT_EXECUTED

Requires two terminals

### TC20: Send ETH deprecated — NOT_EXECUTED

boat eth command removed from CLI

### TC22: Subscribe ETH — NOT_EXECUTED

Requires two terminals

### TC30: Register plugin — NOK

Plugin not confirmed. list: ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ plugin_id        , sub: Subscribing to frames (bus_types=CAN)...


### TC36: vehicle_dynamics signals — NOT_EXECUTED

boat signal subscribe command not in current CLI

### TC39: ABI rejection — NOT_EXECUTED

Requires v7 plugin .so for testing

### TC41: Configure PDU route — NOK

PduRouter plugin loaded but gRPC returns NOT_FOUND (gateway code bug)

### TC42: Send PDU — NOK

PDU send error: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC43: Subscribe PDU — NOT_EXECUTED

Requires two terminals

### TC44: Remove PDU route — NOK

Remove failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC45: Cyclic PDU — NOK

Cyclic route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC46: PDU Ethernet — NOK

ETH route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC47: PDU IP/UDP — NOK

IP/UDP route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC50: Create group — NOK

Group create failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC51: Disable group — NOK

Disable failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC52: Enable group — NOK

Enable failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC53: Multi-PDU group — NOK

Both PDUs not seen after enable: 

### TC60: CanTp single-frame — NOK

CanTp frame not on 0x7E0. CLI: ╭───────────────────── Traceback (most recent call last) ──────────────────────╮
│ /home/testuser/Pr. candump: 

### TC61: CanTp multi-frame — NOK

No frame on 0x7E0: 

### TC70: Cyclic schedule — NOK

Route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC71: OnChange schedule — NOK

OnChange route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC72: Mixed schedule — NOK

Mixed route failed: RPC error [NOT_FOUND]: PduRouter plugin not loaded


### TC80: Replay start — NOT_EXECUTED

Requires pre-recorded trace file

### TC81: Replay seek — NOT_EXECUTED

Depends on TC80

### TC82: Replay pause — NOT_EXECUTED

Depends on TC80

### TC91: FrameService filter — NOT_EXECUTED

Requires two terminals

### TC92: FrameService TCP — NOK

TCP frame not sent: Frame not accepted (unrecognized bus type or no handler)


### TC93: FrameService PDU — NOK

PDU frame send failed: Frame not accepted (unrecognized bus type or no handler)


### TC102: Invalid JSON — INCONCLUSIVE

Unexpected result (rc=0): ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ plugin_id                             ┃ name                       

### TC110: Signal bus — NOT_EXECUTED

boat signal/bus subscribe not in current CLI

### TC120: Concurrent CAN+ETH — NOT_EXECUTED

Requires three terminals

### TC121: Multiple subscribers — NOT_EXECUTED

Requires three terminals

