#!/usr/bin/env python3
"""
BoAt Flow Executor
Runs a single visual flow JSON as a gateway node subprocess.
Usage: python3 demo/flow_executor.py <flow.json>
"""
from __future__ import annotations

import copy
import json
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "/home/testuser/.local/lib/python3.12/site-packages")
sys.path.insert(0, "/home/testuser/ProjectBoat/boat-platform/sdk/python")

import grpc
from boat.client import BoAtClient
from boat.v1 import bus_pb2, can_pb2, ethernet_pb2

_GW = "localhost:50051"


# ── helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def log(msg: str) -> None:
    print(f"{_ts()}  {msg}", flush=True)

def parse_value(v: Any) -> Any:
    """Try hex int, decimal int, float, bool, else string."""
    if isinstance(v, (int, float, bool)):
        return v
    s = str(v).strip()
    if s.lower() == "true":   return True
    if s.lower() == "false":  return False
    if s.lower().startswith("0x"):
        try:   return int(s, 16)
        except ValueError: pass
    try:   return int(s)
    except ValueError: pass
    try:   return float(s)
    except ValueError: pass
    return s

def get_field(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj

def set_field(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        if part not in obj or not isinstance(obj[part], dict):
            obj[part] = {}
        obj = obj[part]
    obj[parts[-1]] = value

def compare(actual: Any, op: str, expected: Any) -> bool:
    try:
        if op == "==":       return actual == expected or str(actual) == str(expected)
        if op == "!=":       return actual != expected and str(actual) != str(expected)
        if op == ">":        return float(actual) > float(expected)
        if op == "<":        return float(actual) < float(expected)
        if op == "contains": return str(expected).lower() in str(actual).lower()
    except Exception:
        return False
    return False


# ── node base ─────────────────────────────────────────────────────────────────

class Node:
    def __init__(self, node_id: str, config: dict) -> None:
        self.id = node_id
        self.config = config
        # successors[output_index] = [NodeA, NodeB, ...]
        # Single-output nodes only use index 0.
        self.successors: list[list["Node"]] = []

    def process(self, msg: dict) -> "dict | tuple | None":
        """
        Return values:
          msg            → forward to output 0
          (index, msg)   → forward to specific output port
          None           → drop message
        """
        return msg

    def start(self, client: BoAtClient, graph: "Graph") -> None:
        pass


# ── source nodes ──────────────────────────────────────────────────────────────

class CanInNode(Node):
    def start(self, client: BoAtClient, graph: "Graph") -> None:
        iface      = self.config.get("iface", "")
        id_flt     = self.config.get("can_id_filter", "").strip()
        expected_id = parse_value(id_flt) if id_flt else None

        def _run() -> None:
            while True:
                try:
                    stream = client.can.SubscribeCanFrames(
                        can_pb2.SubscribeCanFramesRequest(iface=iface)
                    )
                    for frame in stream:
                        if expected_id is not None and frame.can_id != expected_id:
                            continue
                        msg = {
                            "_type": "can_frame",
                            "payload": {
                                "can_id": frame.can_id,
                                "dlc":    frame.dlc,
                                "data":   bytes(frame.data[:frame.dlc]),
                                "iface":  frame.iface,
                            },
                            "topic":  f"can/{frame.iface}/0x{frame.can_id:X}",
                            "ts_ns":  frame.timestamp_ns,
                        }
                        log(f"[CAN In] 0x{frame.can_id:X} [{frame.dlc}] on {frame.iface}")
                        graph.dispatch(self.id, msg)
                except grpc.RpcError:
                    time.sleep(2)
        threading.Thread(target=_run, daemon=True).start()


class EthInNode(Node):
    def start(self, client: BoAtClient, graph: "Graph") -> None:
        iface      = self.config.get("iface", "")
        et_flt     = self.config.get("ethertype_filter", "").strip()
        expected_et = parse_value(et_flt) if et_flt else 0

        def _run() -> None:
            while True:
                try:
                    stream = client.ethernet.SubscribeFrames(
                        ethernet_pb2.SubscribeEthernetFramesRequest(
                            iface=iface, ethertype=expected_et
                        )
                    )
                    for frame in stream:
                        msg = {
                            "_type": "eth_frame",
                            "payload": {
                                "iface":     frame.iface,
                                "ethertype": frame.ethertype,
                                "src_mac":   frame.src_mac.hex(":") if frame.src_mac else "",
                                "dst_mac":   frame.dst_mac.hex(":") if frame.dst_mac else "",
                                "data":      bytes(frame.payload),
                            },
                            "topic":  f"eth/{frame.iface}/0x{frame.ethertype:04X}",
                            "ts_ns":  frame.timestamp_ns,
                        }
                        log(f"[Eth In] 0x{frame.ethertype:04X} len={len(frame.payload)} on {frame.iface}")
                        graph.dispatch(self.id, msg)
                except grpc.RpcError:
                    time.sleep(2)
        threading.Thread(target=_run, daemon=True).start()


class BusInNode(Node):
    def start(self, client: BoAtClient, graph: "Graph") -> None:
        sig_filter = self.config.get("signal_filter", "").strip()
        names      = [sig_filter] if sig_filter else []

        def _run() -> None:
            while True:
                try:
                    stream = client.bus.Subscribe(
                        bus_pb2.BusSubscribeRequest(names=names)
                    )
                    for sig in stream:
                        which = sig.WhichOneof("value")
                        val   = getattr(sig, which) if which else None
                        if which == "bytes_value":
                            val = bytes(val)
                        msg = {
                            "_type": "bus_signal",
                            "payload": {
                                "name":      sig.name,
                                "value":     val,
                                "type":      which or "unknown",
                                "publisher": sig.publisher,
                            },
                            "topic":  f"bus/{sig.name}",
                            "ts_ns":  sig.timestamp_ns,
                        }
                        log(f"[Bus In] {sig.name} = {val}")
                        graph.dispatch(self.id, msg)
                except grpc.RpcError:
                    time.sleep(2)
        threading.Thread(target=_run, daemon=True).start()


class TimerNode(Node):
    def start(self, client: BoAtClient, graph: "Graph") -> None:
        interval_ms = float(self.config.get("interval_ms", 1000))
        topic       = self.config.get("topic", "timer") or "timer"

        def _run() -> None:
            count = 0
            while True:
                time.sleep(interval_ms / 1000.0)
                graph.dispatch(self.id, {
                    "_type":   "any",
                    "payload": {"count": count},
                    "topic":   topic,
                    "ts_ns":   time.time_ns(),
                })
                count += 1
        threading.Thread(target=_run, daemon=True).start()


class InjectNode(Node):
    """Fire a single configurable message shortly after flow start.

    Config:
      topic      — message topic
      payload    — JSON object string OR a scalar value (e.g. "42" or "hello")
      delay_ms   — ms to wait before firing (default 500)
    """

    def start(self, client: BoAtClient, graph: "Graph") -> None:
        delay_ms    = float(self.config.get("delay_ms", 500) or 500)
        topic       = self.config.get("topic", "inject") or "inject"
        payload_raw = (self.config.get("payload", "") or "").strip()

        if payload_raw.startswith("{"):
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = {"value": payload_raw}
        elif payload_raw:
            payload = {"value": parse_value(payload_raw)}
        else:
            payload = {}

        def _run() -> None:
            time.sleep(delay_ms / 1000.0)
            log(f"[Inject] firing → {topic}")
            graph.dispatch(self.id, {
                "_type":   "any",
                "payload": payload,
                "topic":   topic,
                "ts_ns":   time.time_ns(),
            })
        threading.Thread(target=_run, daemon=True).start()


# ── processing nodes ──────────────────────────────────────────────────────────

class FilterNode(Node):
    def process(self, msg: dict) -> dict | None:
        field    = self.config.get("field", "topic") or "topic"
        op       = self.config.get("op", "==") or "=="
        expected = parse_value(self.config.get("value", ""))
        actual   = get_field(msg, field)
        if compare(actual, op, expected):
            log(f"[Filter] PASS  {field} {op} {expected}")
            return msg
        return None


class TransformNode(Node):
    def process(self, msg: dict) -> dict | None:
        field = self.config.get("field", "topic") or "topic"
        value = parse_value(self.config.get("value", ""))
        out   = copy.deepcopy(msg)
        set_field(out, field, value)
        log(f"[Transform] {field} = {value}")
        return out


class CounterNode(Node):
    def __init__(self, node_id: str, config: dict) -> None:
        super().__init__(node_id, config)
        self._count = 0
        self._lock  = threading.Lock()

    def process(self, msg: dict) -> dict | None:
        out = copy.deepcopy(msg)
        with self._lock:
            out["count"] = self._count
            self._count += 1
        return out


class DelayNode(Node):
    def process(self, msg: dict) -> dict | None:
        delay_ms = float(self.config.get("delay_ms", 100) or 100)
        time.sleep(delay_ms / 1000.0)
        return msg


class MathNode(Node):
    """Apply an arithmetic operation to a numeric field.

    Config:
      field  — dotted path to the field to operate on (e.g. "payload.value")
      op     — one of  +  -  *  /  %
      value  — right-hand operand (numeric literal)
    """

    def process(self, msg: dict) -> dict | None:
        field   = self.config.get("field", "payload.value") or "payload.value"
        op      = self.config.get("op", "+") or "+"
        operand = parse_value(self.config.get("value", 0))
        current = get_field(msg, field)
        try:
            current = float(current)
            operand = float(operand)
            if   op == "+": result = current + operand
            elif op == "-": result = current - operand
            elif op == "*": result = current * operand
            elif op == "/": result = current / operand if operand else 0.0
            elif op == "%": result = current % operand if operand else 0.0
            else:           result = current
            # keep integer representation when safe
            if result == int(result):
                result = int(result)
        except (TypeError, ValueError, ZeroDivisionError):
            log(f"[Math] ERROR: cannot apply '{op}' to {current!r}")
            return msg
        out = copy.deepcopy(msg)
        set_field(out, field, result)
        log(f"[Math] {field} {op} {operand} = {result}")
        return out


class SwitchNode(Node):
    """Route a message to one of four output ports by matching a field value.

    Outputs:
      0 — case 1 matches
      1 — case 2 matches
      2 — case 3 matches
      3 — default (no case matched)

    Config:
      field        — dotted path to inspect
      case1/2/3    — expected values (hex strings like "0x1F" are parsed)
    """

    def process(self, msg: dict) -> tuple:
        field  = self.config.get("field", "topic") or "topic"
        actual = get_field(msg, field)
        for i, key in enumerate(("case1", "case2", "case3")):
            raw = self.config.get(key, "").strip()
            if raw and compare(actual, "==", parse_value(raw)):
                log(f"[Switch] case{i + 1} ({raw}) matched on {field}")
                return (i, msg)
        log(f"[Switch] default")
        return (3, msg)


class ChangeNode(Node):
    """Forward a message only when a field's value changes.

    Config:
      field — dotted path to watch (e.g. "payload.value")
    """

    def __init__(self, node_id: str, config: dict) -> None:
        super().__init__(node_id, config)
        self._last = object()   # sentinel — never equal to any real value
        self._lock = threading.Lock()

    def process(self, msg: dict) -> dict | None:
        field   = self.config.get("field", "payload.value") or "payload.value"
        current = get_field(msg, field)
        with self._lock:
            if current == self._last:
                return None
            self._last = current
        log(f"[Change] {field} → {current!r}")
        return msg


class MergeNode(Node):
    """Two-input merge — passes any message from either input through unchanged."""

    def process(self, msg: dict) -> dict | None:
        return msg


# ── shared variable store (per-flow-process) ──────────────────────────────────

_VARS: dict[str, Any] = {}
_VARS_LOCK = threading.Lock()


class SetVarNode(Node):
    """Store a named value extracted from an incoming message.

    Config:
      name   — variable name to write
      field  — dotted path to read from (empty = whole payload)

    Passes the message through unchanged.
    """

    def process(self, msg: dict) -> dict | None:
        name  = self.config.get("name", "").strip()
        field = self.config.get("field", "").strip()
        if not name:
            return msg
        value = get_field(msg, field) if field else msg.get("payload")
        with _VARS_LOCK:
            _VARS[name] = value
        log(f"[SetVar] {name} ← {value!r}")
        return msg


class GetVarNode(Node):
    """Inject a stored variable's value into an outgoing message.

    Config:
      name   — variable name to read
      field  — dotted path to write the value into (e.g. "payload.value")
    """

    def process(self, msg: dict) -> dict | None:
        name  = self.config.get("name", "").strip()
        field = self.config.get("field", "payload.value") or "payload.value"
        if not name:
            return msg
        with _VARS_LOCK:
            value = _VARS.get(name)
        out = copy.deepcopy(msg)
        set_field(out, field, value)
        log(f"[GetVar] {name} → {field} = {value!r}")
        return out


class IfNode(Node):
    """Two-output conditional node.

    Evaluates a condition on the incoming message:
      - output 0 (IF)   — condition is True
      - output 1 (ELSE) — condition is False

    Config fields (same as FilterNode):
      field  — dotted path into msg, e.g. "payload.can_id"
      op     — ==  !=  >  <  contains
      value  — expected value (hex strings like "0x123" are parsed automatically)
    """

    def process(self, msg: dict) -> tuple:
        field    = self.config.get("field", "topic") or "topic"
        op       = self.config.get("op", "==") or "=="
        expected = parse_value(self.config.get("value", ""))
        actual   = get_field(msg, field)

        if compare(actual, op, expected):
            log(f"[If] TRUE  → {field} {op} {expected}")
            return (0, msg)   # output_1 → IF branch
        else:
            log(f"[If] FALSE → {field} {op} {expected}")
            return (1, msg)   # output_2 → ELSE branch


# ── sink nodes ────────────────────────────────────────────────────────────────

def _hex_bytes(s: Any) -> bytes:
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    if not s:
        return b""
    try:
        return bytes.fromhex(str(s).replace(":", "").replace(" ", ""))
    except ValueError:
        return b""


class CanOutNode(Node):
    def __init__(self, node_id: str, config: dict, client: BoAtClient) -> None:
        super().__init__(node_id, config)
        self._client = client

    def process(self, msg: dict) -> None:
        t = msg.get("_type")
        if t and t not in ("can_frame", "any"):
            log(f"[CAN Out] ERROR: received '{t}', expected 'can_frame'. Insert a conversion node.")
            return None
        iface  = self.config.get("iface") or get_field(msg, "payload.iface") or ""
        can_id = parse_value(self.config.get("can_id") or 0) or get_field(msg, "payload.can_id") or 0
        data   = _hex_bytes(self.config.get("data") or get_field(msg, "payload.data") or b"")
        data   = bytes(data[:8])
        frame  = can_pb2.CanFrame(iface=iface, can_id=int(can_id), dlc=len(data), data=data)
        try:
            self._client.can.SendCanFrame(can_pb2.SendCanFrameRequest(frame=frame))
            log(f"[CAN Out] 0x{int(can_id):X} [{len(data)}] on {iface}")
        except grpc.RpcError as e:
            log(f"[CAN Out] ERROR {e.code().name}")
        return None


class EthOutNode(Node):
    def __init__(self, node_id: str, config: dict, client: BoAtClient) -> None:
        super().__init__(node_id, config)
        self._client = client

    def process(self, msg: dict) -> None:
        t = msg.get("_type")
        if t and t not in ("eth_frame", "any"):
            log(f"[Eth Out] ERROR: received '{t}', expected 'eth_frame'. Insert a conversion node.")
            return None
        iface     = self.config.get("iface") or get_field(msg, "payload.iface") or ""
        ethertype = int(parse_value(self.config.get("ethertype") or 0x0800)
                        or get_field(msg, "payload.ethertype") or 0x0800)
        data      = _hex_bytes(self.config.get("data") or get_field(msg, "payload.data") or b"")
        frame     = ethernet_pb2.EthernetFrame(
            iface=iface, ethertype=ethertype,
            payload=bytes(data[:1500]),
            src_mac=_hex_bytes(self.config.get("src_mac", "")),
            dst_mac=_hex_bytes(self.config.get("dst_mac", "")),
        )
        try:
            self._client.ethernet.SendFrame(ethernet_pb2.SendEthernetFrameRequest(frame=frame))
            log(f"[Eth Out] 0x{ethertype:04X} len={len(data)} on {iface}")
        except grpc.RpcError as e:
            log(f"[Eth Out] ERROR {e.code().name}")
        return None


class BusOutNode(Node):
    def __init__(self, node_id: str, config: dict, client: BoAtClient) -> None:
        super().__init__(node_id, config)
        self._client = client

    def process(self, msg: dict) -> None:
        t = msg.get("_type")
        if t and t not in ("bus_signal", "any"):
            log(f"[Bus Out] ERROR: received '{t}', expected 'bus_signal'. Insert a conversion node.")
            return None
        name     = self.config.get("signal_name") or get_field(msg, "payload.name") or ""
        sig_type = self.config.get("signal_type", "number") or "number"
        value    = get_field(msg, "payload.value")
        sig      = bus_pb2.BusSignal(name=name, publisher="flow-executor")
        try:
            if sig_type == "number":   sig.number_value = float(value or 0)
            elif sig_type == "string": sig.string_value = str(value or "")
            elif sig_type == "bool":   sig.bool_value   = bool(value)
            elif sig_type == "bytes":  sig.bytes_value  = _hex_bytes(value)
            self._client.bus.Publish(bus_pb2.BusPublishRequest(signal=sig))
            log(f"[Bus Out] {name} = {value}")
        except grpc.RpcError as e:
            log(f"[Bus Out] ERROR {e.code().name}")
        return None


class DebugNode(Node):
    def process(self, msg: dict) -> None:
        label = self.config.get("label", "debug") or "debug"
        log(f"[{label}] {msg.get('payload', msg)}")
        return None


# ── conversion nodes ──────────────────────────────────────────────────────────

class CanToBytesNode(Node):
    """Pack a can_frame message into a value (bytes): 4B can_id BE + 1B dlc + data."""

    def process(self, msg: dict) -> dict | None:
        t = msg.get("_type")
        if t and t not in ("can_frame", "any"):
            log(f"[CAN→Bytes] WARN: expected 'can_frame', got '{t}'")
        p      = msg.get("payload", {})
        can_id = int(p.get("can_id") or 0)
        data   = bytes(p.get("data") or b"")
        dlc    = int(p.get("dlc") or len(data))
        packed = can_id.to_bytes(4, "big") + bytes([dlc]) + data
        out = copy.deepcopy(msg)
        out["_type"]   = "value"
        out["payload"] = {"value": packed, "name": "can_bytes"}
        log(f"[CAN→Bytes] packed {len(packed)}B (can_id=0x{can_id:X})")
        return out


class BytesToCanNode(Node):
    """Unpack a value (bytes) back into a can_frame: 4B can_id BE + 1B dlc + data."""

    def process(self, msg: dict) -> dict | None:
        t = msg.get("_type")
        if t and t not in ("value", "any"):
            log(f"[Bytes→CAN] WARN: expected 'value', got '{t}'")
        iface = self.config.get("iface", "") or get_field(msg, "payload.iface") or ""
        raw   = get_field(msg, "payload.value") or b""
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw) if raw else b""
        if len(raw) < 5:
            log(f"[Bytes→CAN] ERROR: payload too short ({len(raw)} B, need ≥5)")
            return None
        can_id = int.from_bytes(raw[:4], "big")
        dlc    = raw[4]
        data   = bytes(raw[5:5 + dlc])
        out = copy.deepcopy(msg)
        out["_type"]   = "can_frame"
        out["payload"] = {"can_id": can_id, "dlc": dlc, "data": data, "iface": iface}
        out["topic"]   = f"can/{iface}/0x{can_id:X}"
        log(f"[Bytes→CAN] can_id=0x{can_id:X} dlc={dlc}")
        return out


class ExtractFieldNode(Node):
    """Extract a single field from any message and emit it as a value."""

    def process(self, msg: dict) -> dict | None:
        field = self.config.get("field", "payload.value") or "payload.value"
        val   = get_field(msg, field)
        out = copy.deepcopy(msg)
        out["_type"]   = "value"
        out["payload"] = {"value": val, "name": field}
        log(f"[Extract] {field} = {val}")
        return out


class SetFieldNode(Node):
    """Set a field to a configured value and pass the message through."""

    def process(self, msg: dict) -> dict | None:
        field = self.config.get("field", "") or ""
        value = parse_value(self.config.get("value", ""))
        if not field:
            return msg
        out = copy.deepcopy(msg)
        set_field(out, field, value)
        log(f"[Set] {field} = {value}")
        return out


# ── graph ─────────────────────────────────────────────────────────────────────

_SOURCE_TYPES: dict[str, type] = {
    "can_in": CanInNode, "eth_in": EthInNode,
    "bus_in": BusInNode, "timer":  TimerNode,
    "inject": InjectNode,
}
_PROC_TYPES: dict[str, type] = {
    "filter": FilterNode, "transform": TransformNode,
    "counter": CounterNode, "delay": DelayNode,
    "if_node": IfNode,
    "math": MathNode, "switch_node": SwitchNode,
    "change": ChangeNode, "merge": MergeNode,
    "set_var": SetVarNode, "get_var": GetVarNode,
    "can_to_bytes": CanToBytesNode, "bytes_to_can": BytesToCanNode,
    "extract_field": ExtractFieldNode, "set_field": SetFieldNode,
}
_SINK_TYPES: dict[str, type] = {
    "can_out": CanOutNode, "eth_out": EthOutNode,
    "bus_out": BusOutNode, "debug":   DebugNode,
}
_CLIENT_SINKS = {"can_out", "eth_out", "bus_out"}


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}

    def dispatch(self, from_id: str, msg: dict, output_idx: int = 0) -> None:
        node = self.nodes.get(from_id)
        if not node or output_idx >= len(node.successors):
            return
        for succ in node.successors[output_idx]:
            result = succ.process(msg)
            if result is None:
                continue
            if isinstance(result, tuple):
                next_out, next_msg = result
                self.dispatch(succ.id, next_msg, next_out)
            else:
                self.dispatch(succ.id, result, 0)


def build_graph(flow_json: dict, client: BoAtClient) -> Graph:
    graph = Graph()
    df = flow_json["drawflow"]
    # Handle both the canonical format {"Home": {...}} and the legacy
    # double-nested format {"drawflow": {"Home": {...}}} from older saves.
    if "drawflow" in df:
        df = df["drawflow"]
    raw   = df["Home"]["data"]

    for nid, nd in raw.items():
        name   = nd["name"]
        config = nd.get("data", {})
        if name in _SOURCE_TYPES:
            graph.nodes[nid] = _SOURCE_TYPES[name](nid, config)
        elif name in _PROC_TYPES:
            graph.nodes[nid] = _PROC_TYPES[name](nid, config)
        elif name in _CLIENT_SINKS:
            graph.nodes[nid] = _SINK_TYPES[name](nid, config, client)
        elif name == "debug":
            graph.nodes[nid] = DebugNode(nid, config)
        else:
            log(f"[WARN] unknown node type '{name}' — skipped")

    for nid, nd in raw.items():
        if nid not in graph.nodes:
            continue
        for out_key, out_val in nd.get("outputs", {}).items():
            # Drawflow names outputs "output_1", "output_2", … → 0-based index
            try:
                out_idx = int(out_key.rsplit("_", 1)[1]) - 1
            except (ValueError, IndexError):
                out_idx = 0
            # Grow the successors list to accommodate this output index
            while len(graph.nodes[nid].successors) <= out_idx:
                graph.nodes[nid].successors.append([])
            for conn in out_val.get("connections", []):
                to_id = str(conn["node"])
                if to_id in graph.nodes:
                    graph.nodes[nid].successors[out_idx].append(graph.nodes[to_id])

    return graph


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: flow_executor.py <flow.json>", file=sys.stderr)
        sys.exit(1)

    flow_json = json.loads(Path(sys.argv[1]).read_text())
    name      = flow_json.get("meta", {}).get("name", Path(sys.argv[1]).stem)

    log(f"Flow '{name}' starting — connecting to {_GW}")
    client = BoAtClient(_GW)
    graph  = build_graph(flow_json, client)

    sources = [n for n in graph.nodes.values() if isinstance(n, tuple(_SOURCE_TYPES.values()))]
    log(f"Graph: {len(graph.nodes)} nodes, {len(sources)} sources")

    for node in sources:
        node.start(client, graph)

    log("Running — send SIGTERM to stop")
    stop = threading.Event()
    signal.signal(signal.SIGINT,  lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    log("Stopped.")


if __name__ == "__main__":
    main()
