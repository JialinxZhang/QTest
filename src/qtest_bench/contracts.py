from __future__ import annotations

import hashlib
import json
import math
import os
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class QTestError(Exception):
    """An actionable framework error, safe to show in reports."""


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise QTestError("JSON must be serializable and contain only finite numbers") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QTestError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(data: str | bytes):
    def invalid(value):
        raise QTestError(f"Non-finite JSON number: {value}")
    try:
        result = json.loads(data, object_pairs_hook=_pairs, parse_constant=invalid)
        canonical(result)  # Also catches finite-looking numeric literals that overflow.
        return result
    except (ValueError, UnicodeError) as exc:
        raise QTestError(f"Invalid JSON: {exc}") from exc


def read_json(path: str | Path):
    return loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    # Windows readers (including the live monitor) briefly hold handles without
    # FILE_SHARE_DELETE. Retry only the local atomic replacement, never a QPU call.
    for attempt in range(41):
        try:
            temp.replace(path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 40:
                raise
            time.sleep(0.025)


def require(condition: bool, message: str):
    if not condition:
        raise QTestError(message)


def number(value, name, low=None, high=None):
    require(type(value) in (float, int) and math.isfinite(value), f"{name} must be finite numeric")
    require(low is None or value >= low, f"{name} is below {low}")
    require(high is None or value <= high, f"{name} is above {high}")
    return value


def integer(value, name, low, high):
    require(type(value) is int, f"{name} must be an integer")
    return number(value, name, low, high)


def keys(value, required, optional=()):
    require(isinstance(value, dict), "Expected an object")
    require(set(required) <= value.keys(), f"Missing fields: {set(required) - value.keys()}")
    require(value.keys() <= set(required) | set(optional), f"Unknown fields: {value.keys() - set(required) - set(optional)}")


def validate_suite(suite):
    schema = loads(files("qtest_bench").joinpath("schemas/suite.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(suite), key=lambda e: str(list(e.path)))
    require(not errors, "Suite schema: " + (errors[0].message if errors else ""))
    canonical(suite)
    # JSON Schema permits 1.0 as an integer; Python loop/index contracts require int.
    integer(suite["seed"], "seed", 0, 2147483647)
    integer(suite["limits"]["max_jobs"], "max_jobs", 1, 10000)
    integer(suite["limits"]["max_shots"], "max_shots", 1, 1000000000)
    ids = [c["id"] for c in suite["cases"]]
    require(len(set(ids)) == len(ids), "Case IDs must be unique")
    from .benchmarks import get_benchmark
    for case in suite["cases"]:
        integer(case["repetitions"], "repetitions", 1, 100)
        integer(case["shots"], "shots", 1, 1000000)
        get_benchmark(case["benchmark"]).validate(case["parameters"])
    return suite


def validate_device(device):
    keys(device, ["adapter", "adapter_version", "device_id", "kind", "technology", "capabilities"])
    for field in ("adapter", "adapter_version", "device_id", "technology"):
        require(isinstance(device[field], str) and 0 < len(device[field]) <= 200, f"Invalid {field}")
    require(device["kind"] in ("simulator", "qpu"), "Device kind must be simulator or qpu")
    require(isinstance(device["capabilities"], list) and all(isinstance(c, str) for c in device["capabilities"]), "Invalid capabilities")
    canonical(device)


def validate_measurement(workload, result):
    keys(result, ["counts", "attempted_shots", "accepted_shots", "resources", "provenance"])
    requested = workload["shots"]
    require(type(result["attempted_shots"]) is int and result["attempted_shots"] == requested,
            "attempted_shots must equal the requested attempts; hidden resampling is disallowed")
    integer(result["accepted_shots"], "accepted_shots", 0, requested)
    counts = result["counts"]
    require(isinstance(counts, dict), "counts must be an object")
    for bitstring, count in counts.items():
        require(isinstance(bitstring, str), "Count keys must be strings")
        integer(count, "count", 1, requested)
        if workload["kind"] in ("gate.sample.v1", "analog.evolve.v1"):
            n = workload["n_qubits"]
            require(len(bitstring) == n and set(bitstring) <= {"0", "1"}, "Invalid bitstring; qubit 0 is leftmost")
        elif workload["kind"] == "photon.fock.v1":
            parts = bitstring.split(",")
            require(len(parts) == len(workload["input_occupation"]), "Invalid photon pattern width")
            require(all(p.isdigit() and str(int(p)) == p for p in parts), "Photon counts must be canonical nonnegative integers")
            require(sum(map(int, parts)) == sum(workload["input_occupation"]), "Accepted Fock patterns must conserve the specified photon number")
        else:
            raise QTestError("Unknown measurement workload")
    require(sum(counts.values()) == result["accepted_shots"], "Counts must sum to accepted_shots")
    resources = result["resources"]
    keys(resources, ["qpu_seconds", "execution_seconds", "timing_scope"])
    for field in ("qpu_seconds", "execution_seconds"):
        if resources[field] is not None:
            number(resources[field], field, 0)
    require(isinstance(resources["timing_scope"], str), "timing_scope must be explicit")
    require(isinstance(result["provenance"], dict), "provenance must be an object")
    canonical(result)
    return result
