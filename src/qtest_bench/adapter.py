"""Vendor boundary. Plugins are installed code selected by the operator, never by a bundle."""
from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.metadata import entry_points
import time
import uuid

from .contracts import QTestError, require
from .numerics import analog_probabilities, fock_probabilities, gate_probabilities, sample


class Adapter(ABC):
    @abstractmethod
    def describe(self) -> dict: ...

    @abstractmethod
    def submit(self, workload: dict, request_id: str) -> str: ...

    @abstractmethod
    def status(self, job_id: str) -> str: ...

    @abstractmethod
    def result(self, job_id: str) -> dict: ...

    @abstractmethod
    def cancel(self, job_id: str) -> None: ...


class LocalAdapter(Adapter):
    """Ideal, bounded reference simulator. No hardware performance claim."""
    def __init__(self, config=None):
        require(not config, "The local adapter takes no configuration")
        self.jobs = {}
        self.requests = {}

    def describe(self):
        return {"adapter": "local", "adapter_version": "0.1.0", "device_id": "numpy-ideal-reference",
                "kind": "simulator", "technology": "classical_simulation",
                "capabilities": ["gate.sample.v1", "analog.evolve.v1", "photon.fock.v1"]}

    def submit(self, workload, request_id):
        if request_id in self.requests:
            return self.requests[request_id]
        started = time.perf_counter()
        calculators = {"gate.sample.v1": gate_probabilities, "analog.evolve.v1": analog_probabilities,
                       "photon.fock.v1": fock_probabilities}
        require(workload["kind"] in calculators, "Unsupported workload")
        if "n_qubits" in workload:
            require(workload["n_qubits"] <= 10, "Local reference limited to 10 qubits")
        probabilities = calculators[workload["kind"]](workload)
        result = {"counts": sample(probabilities, workload["shots"], workload["seed"]),
                  "attempted_shots": workload["shots"], "accepted_shots": workload["shots"],
                  "resources": {"qpu_seconds": None, "execution_seconds": time.perf_counter() - started,
                                "timing_scope": "local_simulation_wall"},
                  "provenance": {"model": "ideal", "postselection": "none", "mitigation": "none"}}
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = result
        self.requests[request_id] = job_id
        return job_id

    def status(self, job_id):
        return "completed" if job_id in self.jobs else "failed"

    def result(self, job_id):
        return self.jobs[job_id]

    def cancel(self, job_id):
        pass  # Local jobs have already completed synchronously.


def load_adapter(name, config=None):
    if name == "local":
        return LocalAdapter(config)
    plugins = list(entry_points(group="qtest_bench.adapters", name=name))
    require(len(plugins) == 1, f"Expected exactly one installed adapter named {name!r}; found {len(plugins)}")
    try:
        adapter = plugins[0].load()(config or {})
    except Exception as exc:
        raise QTestError(f"Adapter initialization failed ({type(exc).__name__}); inspect local SDK logs") from None
    require(isinstance(adapter, Adapter), "Adapter plugin must subclass qtest_bench.adapter.Adapter")
    return adapter
