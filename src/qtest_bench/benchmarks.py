"""Versioned pilot protocols. App controllers include training and fresh final evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .contracts import QTestError, integer, keys, number, require
from .numerics import (I, X, Z, analog_probabilities, chain_structure_factor, distribution, fock_probabilities,
                       mean_and_se, spin_correlations, tensor, tvd)


def vector(values, n, name):
    require(isinstance(values, list) and len(values) == n, f"{name} must have length {n}")
    for value in values:
        number(value, name, -100, 100)


def validate_edges(edges, n, weighted=False):
    require(isinstance(edges, list), "edges must be a list")
    seen = set()
    for edge in edges:
        require(isinstance(edge, list) and len(edge) == (3 if weighted else 2), "Invalid edge")
        a, b = edge[:2]
        integer(a, "edge vertex", 0, n - 1)
        integer(b, "edge vertex", 0, n - 1)
        require(a < b and (a, b) not in seen, "Use unique edges with a < b")
        seen.add((a, b))
        if weighted:
            number(edge[2], "coupling", -100, 100)


def validate_hamiltonian(p):
    keys(p, ["n_qubits", "hx", "hz", "edges", "initial", "times"])
    n = integer(p["n_qubits"], "n_qubits", 1, 8)
    vector(p["hx"], n, "hx")
    vector(p["hz"], n, "hz")
    validate_edges(p["edges"], n, True)
    require(isinstance(p["initial"], str) and len(p["initial"]) == n and set(p["initial"]) <= {"0", "1"}, "Invalid initial bitstring")
    require(isinstance(p["times"], list) and 1 <= len(p["times"]) <= 30, "Use 1..30 times")
    for t in p["times"]:
        number(t, "time", 0, 100)


def run_hamiltonian(p, session, seed):
    values = []
    for t in p["times"]:
        work = {k: p[k] for k in ("n_qubits", "hx", "hz", "edges", "initial")}
        work.update(kind="analog.evolve.v1", time=t)
        measured = session.execute(work, f"trajectory:t={t}")
        exact = analog_probabilities(work)
        errors = []
        for q in range(p["n_qubits"]):
            observable = lambda bits, q=q: int(bits[q])
            mean, _ = mean_and_se(measured, observable)
            reference = sum(prob * observable(bits) for bits, prob in exact.items())
            errors.append(abs(mean - reference))
        values.append({"time": t, "population_mae": float(np.mean(errors)),
                       "empirical_tvd": tvd(measured, exact),
                       "acceptance": measured["accepted_shots"] / measured["attempted_shots"]})
    return {"mean_population_mae": float(np.mean([v["population_mae"] for v in values])),
            "trajectory": values, "reference": "R1: exact Ising state evolution; hbar=1"}


def validate_fock(p):
    keys(p, ["unitary_real", "unitary_imag", "input_occupation"])
    incoming = p["input_occupation"]
    require(isinstance(incoming, list) and 2 <= len(incoming) <= 6, "Use 2..6 optical modes")
    for v in incoming:
        integer(v, "occupation", 0, 4)
    require(1 <= sum(incoming) <= 4, "Exact pilot reference supports 1..4 photons")
    m = len(incoming)
    for field in ("unitary_real", "unitary_imag"):
        require(isinstance(p[field], list) and len(p[field]) == m, "Invalid unitary rows")
        for row in p[field]:
            vector(row, m, field)
    u = np.array(p["unitary_real"]) + 1j * np.array(p["unitary_imag"])
    require(np.allclose(u.conj().T @ u, np.eye(m), atol=1e-8, rtol=0), "Optical matrix must be unitary")


def run_fock(p, session, seed):
    work = dict(p, kind="photon.fock.v1")
    measured = session.execute(work, "final:fock_samples")
    collision = lambda label: int(any(int(x) > 1 for x in label.split(",")))
    collision_rate, se = mean_and_se(measured, collision)
    return {"empirical_tvd": tvd(measured, fock_probabilities(work)),
            "collision_probability": collision_rate, "collision_se": se,
            "acceptance": measured["accepted_shots"] / measured["attempted_shots"],
            "reference": "R1: ideal fixed-photon-number Fock distribution; NOT GBS",
            "comparison_scope": "conditional on accepted fixed-N patterns; acceptance reported separately"}


def validate_mis(p):
    keys(p, ["n_vertices", "edges", "penalty", "depth", "iterations"])
    n = integer(p["n_vertices"], "n_vertices", 2, 10)
    validate_edges(p["edges"], n)
    number(p["penalty"], "penalty", 1.000001, 100)
    integer(p["depth"], "depth", 1, 3)
    integer(p["iterations"], "iterations", 2, 50)


def mis_feasible(bits, edges):
    return all(not (bits[a] == bits[b] == "1") for a, b in edges)


def mis_objective(bits, p):
    """MIS penalty objective in graph-score units."""
    return -bits.count("1") + p["penalty"] * sum(bits[a] == bits[b] == "1" for a, b in p["edges"])


def mis_circuit(p, angles):
    n = p["n_vertices"]
    gates = [{"name": "h", "qubits": [q]} for q in range(n)]
    degree = [sum(q in e for e in p["edges"]) for q in range(n)]
    for gamma, beta in np.array(angles).reshape(-1, 2):
        for q in range(n):
            gates.append({"name": "rz", "qubits": [q], "angle": float(2 * gamma * (0.5 - p["penalty"] * degree[q] / 4))})
        for a, b in p["edges"]:
            gates.append({"name": "rzz", "qubits": [a, b], "angle": float(gamma * p["penalty"] / 2)})
        gates.extend({"name": "rx", "qubits": [q], "angle": float(2 * beta)} for q in range(n))
    return {"kind": "gate.sample.v1", "n_qubits": n, "gates": gates}


def optimize(parameters, iterations, seed, evaluate):
    """Frozen pilot optimizer: one uniform initial point, Gaussian local proposals thereafter."""
    rng = np.random.default_rng(seed)
    best = rng.uniform(-np.pi, np.pi, parameters)
    best_value = float("inf")
    trace = []
    for i in range(iterations):
        candidate = best.copy() if i == 0 else best + rng.normal(0, 0.7 / np.sqrt(i), parameters)
        value = evaluate(candidate, i)
        trace.append(float(value))
        if value < best_value:
            best, best_value = candidate, value
    return best, trace


def run_mis(p, session, seed):
    def evaluate(angles, iteration):
        measurement = session.execute(mis_circuit(p, angles), f"train:{iteration}")
        return mean_and_se(measurement, lambda bits: mis_objective(bits, p))[0]
    best, trace = optimize(2 * p["depth"], p["iterations"], seed, evaluate)
    measured = session.execute(mis_circuit(p, best), "final:independent_samples")
    probs = distribution(measured)
    feasible = {bits: prob for bits, prob in probs.items() if mis_feasible(bits, p["edges"])}
    optimal = max(format(i, f"0{p['n_vertices']}b").count("1") for i in range(2**p["n_vertices"])
                  if mis_feasible(format(i, f"0{p['n_vertices']}b"), p["edges"]))
    best_bits = max(feasible, key=lambda b: (b.count("1"), b)) if feasible else None
    size = best_bits.count("1") if best_bits is not None else None
    return {"feasible_probability": sum(feasible.values()), "best_feasible_size": size,
            "best_solution": best_bits, "exact_optimum": optimal,
            "best_ratio": size / optimal if size is not None else None,
            "optimal_sample_probability": sum(prob for bits, prob in feasible.items() if bits.count("1") == optimal),
            "acceptance": measured["accepted_shots"] / measured["attempted_shots"],
            "training_objective": trace, "reference": "R1: exhaustive original-graph MIS; no repair or local search"}


def validate_material_dynamics(p):
    validate_hamiltonian(p)
    require(p["n_qubits"] >= 2, "Material dynamics needs at least 2 spins for correlations")


def run_material_dynamics(p, session, seed):
    """A3 pilot: reconstruct C_ij(t) and S(q,t) on a frozen 1D chain, not just populations."""
    n = p["n_qubits"]
    wavevectors = [2 * np.pi * k / n for k in range(n)]
    correlation_errors, structure_errors, values = [], [], []
    for t in p["times"]:
        work = {k: p[k] for k in ("n_qubits", "hx", "hz", "edges", "initial")}
        work.update(kind="analog.evolve.v1", time=t)
        measured = session.execute(work, f"trajectory:t={t}")
        exact = analog_probabilities(work)
        measured_c = spin_correlations(distribution(measured), n)
        exact_c = spin_correlations(exact, n)
        pair_errors = [abs(measured_c[i][j] - exact_c[i][j]) for i in range(n) for j in range(i, n)]
        measured_s = chain_structure_factor(measured_c, wavevectors)
        exact_s = chain_structure_factor(exact_c, wavevectors)
        q_errors = [abs(a - b) for a, b in zip(measured_s, exact_s)]
        correlation_errors.extend(pair_errors)
        structure_errors.extend(q_errors)
        values.append({"time": t, "correlation_mae": float(np.mean(pair_errors)),
                       "structure_factor_mae": float(np.mean(q_errors)),
                       "empirical_tvd": tvd(measured, exact),
                       "acceptance": measured["accepted_shots"] / measured["attempted_shots"]})
    return {"mean_correlation_mae": float(np.mean(correlation_errors)),
            "mean_structure_factor_mae": float(np.mean(structure_errors)),
            "wavevector_grid": "q_k = 2*pi*k/n, k = 0..n-1; unit lattice spacing; 1D chain",
            "trajectory": values,
            "reference": "R1: exact 1D Ising chain evolution; connected equal-time Z correlations",
            "scope": "pilot spin-chain fixture; NOT a real-material prediction"}


def validate_vqe(p):
    keys(p, ["coefficients", "iterations"])
    keys(p["coefficients"], ["II", "ZI", "IZ", "ZZ", "XX"])
    for v in p["coefficients"].values():
        number(v, "coefficient", -100, 100)
    integer(p["iterations"], "iterations", 2, 50)


def vqe_circuit(angles, basis):
    gates = [{"name": "ry", "qubits": [q], "angle": float(angles[q])} for q in range(2)]
    gates.append({"name": "cx", "qubits": [0, 1]})
    gates.extend({"name": "ry", "qubits": [q], "angle": float(angles[q + 2])} for q in range(2))
    if basis == "X":
        gates.extend({"name": "h", "qubits": [q]} for q in range(2))
    return {"kind": "gate.sample.v1", "n_qubits": 2, "gates": gates}


def run_vqe(p, session, seed):
    c = p["coefficients"]
    def energy(angles, role):
        z = session.execute(vqe_circuit(angles, "Z"), f"{role}:Z")
        x = session.execute(vqe_circuit(angles, "X"), f"{role}:X")
        zvalue, zse = mean_and_se(z, lambda bits: c["ZI"] * (-1)**int(bits[0]) + c["IZ"] * (-1)**int(bits[1]) + c["ZZ"] * (-1)**(int(bits[0]) + int(bits[1])))
        xvalue, xse = mean_and_se(x, lambda bits: c["XX"] * (-1)**(int(bits[0]) + int(bits[1])))
        se = float(np.hypot(zse, xse)) if zse is not None and xse is not None else None
        return c["II"] + zvalue + xvalue, se
    best, trace = optimize(4, p["iterations"], seed, lambda angles, i: energy(angles, f"train:{i}")[0])
    final_energy, final_se = energy(best, "final:independent_samples")
    matrices = {"I": I, "X": X, "Z": Z}
    matrix = sum(coefficient * tensor([matrices[x] for x in term]) for term, coefficient in c.items())
    ground = float(np.linalg.eigvalsh(matrix)[0])
    return {"energy": float(final_energy), "energy_se": final_se, "reference_ground_energy": ground,
            "absolute_energy_error": abs(float(final_energy) - ground),
            "below_ground_by_more_than_3se": bool(final_se is not None and final_energy < ground - 3 * final_se),
            "training_energy": trace, "reference": "R1: exact two-qubit eigensolver",
            "scope": "toy integration fixture; NOT a molecular chemistry benchmark"}


@dataclass(frozen=True)
class Benchmark:
    title: str
    capability: str
    validate: object
    run: object
    jobs: object
    layer: str


REGISTRY = {
    "kernel.hamiltonian.v1": Benchmark("Ising Hamiltonian evolution", "analog.evolve.v1", validate_hamiltonian, run_hamiltonian, lambda p: len(p["times"]), "L1"),
    "kernel.boson_fock.v1": Benchmark("Ideal Fock boson sampling", "photon.fock.v1", validate_fock, run_fock, lambda p: 1, "L1"),
    "application.mis.v1": Benchmark("MIS with full QAOA optimization", "gate.sample.v1", validate_mis, run_mis, lambda p: p["iterations"] + 1, "L2"),
    "application.vqe_toy.v1": Benchmark("Toy VQE with full hybrid loop", "gate.sample.v1", validate_vqe, run_vqe, lambda p: 2 * (p["iterations"] + 1), "L2-fixture"),
    "application.material_dynamics.v1": Benchmark("Material dynamics: spin correlations and structure factor", "analog.evolve.v1", validate_material_dynamics, run_material_dynamics, lambda p: len(p["times"]), "L2"),
}


def get_benchmark(name):
    require(name in REGISTRY, f"Unknown or unimplemented benchmark: {name}")
    return REGISTRY[name]
