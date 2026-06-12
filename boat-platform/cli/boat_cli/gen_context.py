"""Assembles the system prompt for plugin generation.

Injects:
  1. The base-class source code (CanNode, BusNode, EthernetNode) — the LLM sees
     the actual API, not documentation that may drift.
  2. One complete reference example per base class.
  3. Live gateway state (CAN buses, Ethernet interfaces, known signal names) —
     this is queried at generation time so the LLM can use the real names.
     If the gateway is unreachable the section is omitted gracefully.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


# ── SDK API reference (compact — signatures only, no full source) ────────────

# Injecting full source files costs hundreds of tokens for docstrings and
# implementation details the LLM does not need.  These compact references give
# it the import path, constructor args, available methods, and frame fields.

_SDK_API_REFERENCE = """\
## SDK API Reference

Import paths and available methods — use ONLY what is listed here.

### CanNode  (from boat.can_node import CanNode)
CanNode(address="localhost:50051", iface_filter="", sim_id="")
  iface_filter: CAN interface name to subscribe to, or "" for ALL interfaces.
  .on_frame(frame, iface: str)  ← override this; called for every received frame
  .send(can_id: int, data: bytes, iface: str = "") -> bool
  .run()             # blocks until stop() or server disconnect
  .run_background()  # runs in a daemon thread, returns Thread
  .stop()

  frame fields: frame.can_id (int), frame.dlc (int), frame.data (bytes),
                frame.iface (str), frame.flags (int)

### BusNode  (from boat.bus_node import BusNode)
BusNode(address="localhost:50051", node_id="")
  .on_signal(signal)  ← override this; called for every received signal
  .publish(name: str, value: float|int|str|bool|bytes, publisher="") -> bool
  .run(names: list[str] | None = None)  # None = all signals
  .run_background(names=None)
  .stop()

  signal fields: signal.name (str), signal.number_value (float),
                 signal.string_value (str), signal.bool_value (bool),
                 signal.bytes_value (bytes), signal.timestamp_ns (int)

### EthernetNode  (from boat.ethernet_node import EthernetNode)
EthernetNode(address="localhost:50051", iface_filter="", ethertype_filter=0)
  .on_frame(frame, iface: str)  ← override this
  .send(ethertype: int, payload: bytes, iface="", src_mac=b"", dst_mac=b"") -> bool
  .run() / .run_background() / .stop()

  frame fields: frame.iface (str), frame.src_mac (bytes), frame.dst_mac (bytes),
                frame.ethertype (int), frame.payload (bytes),
                frame.timestamp_ns (int)
"""


def _example_source(example_filename: str) -> str:
    spec = importlib.util.find_spec("boat.can_node")
    if spec is None or spec.origin is None:
        return ""
    examples_dir = Path(spec.origin).parent / "examples"
    path = examples_dir / example_filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# ── Live gateway context ─────────────────────────────────────────────────────

def _query_gateway(host: str) -> dict[str, Any]:
    """Return a dict with live gateway state. Empty on any failure."""
    result: dict[str, Any] = {}
    try:
        import grpc
        from boat.client import BoAtClient
        from boat.v1 import can_pb2, bus_pb2, ethernet_pb2

        client = BoAtClient(address=host)
        try:
            resp = client.can.ListBuses(can_pb2.ListBusesRequest())
            result["can_ifaces"] = [b.iface for b in resp.buses]
        except Exception:
            pass
        try:
            resp = client.ethernet.ListInterfaces(
                ethernet_pb2.ListEthernetInterfacesRequest()
            )
            result["eth_ifaces"] = list(resp.ifaces)
        except Exception:
            pass
        try:
            resp = client.bus.ListSignals(bus_pb2.BusListSignalsRequest())
            result["bus_signals"] = list(resp.names)
        except Exception:
            pass
        client.close()
    except Exception:
        pass
    return result


# ── Prompt assembly ──────────────────────────────────────────────────────────

_SYSTEM_INTRO = """\
You are an expert BoAt platform plugin developer.

BoAt is an automotive simulation platform. A BoAt plugin is a standalone Python
script that connects to the gateway via gRPC. Plugins subclass one of the provided
base classes, override a single callback, and call run() to start.

════════════════════════════════════════════════════════
ALLOWED IMPORTS — use NOTHING else:
  from boat.can_node      import CanNode
  from boat.bus_node      import BusNode
  from boat.ethernet_node import EthernetNode
  import threading        (stdlib — only for cyclic/timer logic)
  import time             (stdlib — only if absolutely needed)

FORBIDDEN — never use these, even if you know them from other contexts:
  import can              ← python-can library, NOT available
  import cantools         ← NOT available
  import asyncio          ← NOT available
  any other third-party library
════════════════════════════════════════════════════════

Rules you MUST follow:
1. Generate a single self-contained Python script.
2. Only call methods that are defined in the SDK API Reference below.
3. To listen on multiple interfaces at once, use iface_filter="" and check
   the iface argument inside on_frame().
4. For cyclic/periodic sending use threading.Timer — follow the cyclic
   example exactly.
5. Always include `if __name__ == "__main__":` at the bottom.
6. Add a one-line docstring at the top describing what the plugin does.
7. Output ONLY the Python code — no explanation, no markdown fences.

Payload encoding rules (bytes must be in range 0-255 each):
- Single byte value N:        bytes([N])           — only valid if N <= 255
- 16-bit big-endian value N:  N.to_bytes(2, "big") — e.g. (1234).to_bytes(2, "big")
- 16-bit little-endian:       N.to_bytes(2, "little")
- Hex literal bytes:          bytes([0x12, 0x34])
- NEVER write bytes([1234]) — 1234 > 255 and will raise ValueError at runtime.
"""


_SECTION_EXAMPLES = """\
## Reference Examples

These are the ONLY correct patterns. Match the example that is closest to the \
requested task and follow it exactly for imports and structure.

### 1. Basic CAN (receive + conditional send + bus publish)
```python
{can_example}
```

### 2. Multi-byte payload decode and encode
```python
{decode_encode_example}
```

### 3. Multiple CAN IDs in one node (dispatch table + state)
```python
{multi_id_example}
```

### 4. Bus signal → CAN frame (reverse direction)
```python
{bus_to_can_example}
```

### 5. Bus signals (subscribe to named signals + publish derived value)
```python
{bus_example}
```

### 6. Cyclic sending + multi-interface + start/stop \
(any periodic/timed behaviour or listening on more than one interface)
```python
{cyclic_example}
```

### 7. Ethernet (receive + decode + send)
```python
{eth_example}
```
"""

_SECTION_RUNTIME = """\
## Live Gateway State

{content}
"""


def build_system_prompt(gateway_host: str = "localhost:50051") -> str:
    parts: list[str] = [_SYSTEM_INTRO, _SDK_API_REFERENCE]

    # Examples
    can_ex          = _example_source("can_example.py")
    decode_ex       = _example_source("decode_encode_example.py")
    multi_id_ex     = _example_source("multi_id_example.py")
    bus_to_can_ex   = _example_source("bus_to_can_example.py")
    bus_ex          = _example_source("bus_example.py")
    cyclic_ex       = _example_source("cyclic_example.py")
    eth_ex          = _example_source("ethernet_example.py")
    if any([can_ex, bus_ex, eth_ex, cyclic_ex, decode_ex, multi_id_ex, bus_to_can_ex]):
        parts.append(_SECTION_EXAMPLES.format(
            can_example=can_ex              or "(not found)",
            decode_encode_example=decode_ex or "(not found)",
            multi_id_example=multi_id_ex    or "(not found)",
            bus_to_can_example=bus_to_can_ex or "(not found)",
            bus_example=bus_ex              or "(not found)",
            cyclic_example=cyclic_ex        or "(not found)",
            eth_example=eth_ex              or "(not found)",
        ))

    # Live gateway state
    gw = _query_gateway(gateway_host)
    if gw:
        lines: list[str] = []
        if "can_ifaces" in gw:
            lines.append(f"Available CAN interfaces : {', '.join(gw['can_ifaces']) or 'none'}")
        if "eth_ifaces" in gw:
            lines.append(f"Available Eth interfaces : {', '.join(gw['eth_ifaces']) or 'none'}")
        if "bus_signals" in gw:
            signals = gw["bus_signals"]
            lines.append(f"Known bus signal names   : {', '.join(signals[:40]) or 'none'}")
            if len(signals) > 40:
                lines.append(f"  ... and {len(signals) - 40} more")
        parts.append(_SECTION_RUNTIME.format(content="\n".join(lines)))
    else:
        parts.append(_SECTION_RUNTIME.format(
            content="Gateway offline — use placeholder names; the user will adjust them."
        ))

    return "\n\n".join(parts)
