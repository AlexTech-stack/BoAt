import importlib


def test_generated_stub_modules_import_from_boat_v1_package():
    modules = [
        "boat.v1.common_pb2",
        "boat.v1.fault_pb2",
        "boat.v1.fault_pb2_grpc",
        "boat.v1.metrics_pb2",
        "boat.v1.metrics_pb2_grpc",
        "boat.v1.plugin_pb2",
        "boat.v1.plugin_pb2_grpc",
        "boat.v1.replay_pb2",
        "boat.v1.replay_pb2_grpc",
        "boat.v1.scenario_pb2",
        "boat.v1.scenario_pb2_grpc",
        "boat.v1.signal_pb2",
        "boat.v1.signal_pb2_grpc",
        "boat.v1.simulation_pb2",
        "boat.v1.simulation_pb2_grpc",
        "boat.v1.trace_pb2",
        "boat.v1.trace_pb2_grpc",
    ]
    for module_name in modules:
        importlib.import_module(module_name)


def test_boat_client_loads_all_service_stubs(boat_client):
    assert boat_client.simulation is not None
    assert boat_client.signal is not None
    assert boat_client.scenario is not None
    assert boat_client.replay is not None
    assert boat_client.plugin is not None
    assert boat_client.metrics is not None
    assert boat_client.trace is not None
    assert boat_client.fault is not None
