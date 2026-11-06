"""Small exact references, deliberately bounded; qubit 0 is the leftmost bit."""
from __future__ import annotations

import itertools
import math
import numpy as np

from .contracts import require

X = np.array([[0, 1], [1, 0]], dtype=complex)
Z = np.diag([1, -1]).astype(complex)
I = np.eye(2, dtype=complex)
H = np.array([[1, 1], [1, -1]], dtype=complex) / np.sqrt(2)


def tensor(operators):
    result = np.array([[1]], dtype=complex)
    for op in operators:
        result = np.kron(result, op)
    return result


def ising_matrix(n, hx, hz, edges):
    result = np.zeros((2**n, 2**n), dtype=complex)
    for q in range(n):
        for coefficient, op in ((hx[q], X), (hz[q], Z)):
            result += coefficient * tensor([op if j == q else I for j in range(n)])
    for a, b, coefficient in edges:
        result += coefficient * tensor([Z if j in (a, b) else I for j in range(n)])
    return result


def analog_probabilities(work):
    n = work["n_qubits"]
    eigenvalues, vectors = np.linalg.eigh(ising_matrix(n, work["hx"], work["hz"], work["edges"]))
    initial = np.zeros(2**n, dtype=complex)
    initial[int(work["initial"], 2)] = 1
    state = vectors @ (np.exp(-1j * eigenvalues * work["time"]) * (vectors.conj().T @ initial))
    return {format(i, f"0{n}b"): float(abs(a)**2) for i, a in enumerate(state)}


def gate_probabilities(work):
    n = work["n_qubits"]
    state = np.zeros(2**n, dtype=complex)
    state[0] = 1
    indices = np.arange(2**n)
    for gate in work["gates"]:
        name, q = gate["name"], gate["qubits"]
        if name in ("cx", "rzz"):
            a, b = q
            if name == "cx":
                target = indices ^ (((indices >> (n - 1 - a)) & 1) << (n - 1 - b))
                state = state[target]
            else:
                parity = ((indices >> (n - 1 - a)) ^ (indices >> (n - 1 - b))) & 1
                state *= np.exp(-0.5j * gate["angle"] * (1 - 2 * parity))
        else:
            if name == "h":
                op = H
            elif name == "x":
                op = X
            elif name == "rz":
                op = np.diag(np.exp(-0.5j * gate["angle"] * np.array([1, -1])))
            elif name == "rx":
                angle = gate["angle"] / 2
                op = np.cos(angle) * I - 1j * np.sin(angle) * X
            elif name == "ry":
                angle = gate["angle"] / 2
                op = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
            else:
                raise ValueError(f"Unsupported reference gate: {name}")
            shaped = np.moveaxis(state.reshape([2] * n), q[0], 0)
            shaped = (op @ shaped.reshape(2, -1)).reshape([2] * n)
            state = np.moveaxis(shaped, 0, q[0]).reshape(-1)
    return {format(i, f"0{n}b"): float(abs(a)**2) for i, a in enumerate(state)}


def spin_correlations(probabilities, n):
    """Connected equal-time Z correlations C_ij = <Z_i Z_j> - <Z_i><Z_j>."""
    moment = [[0.0] * n for _ in range(n)]
    magnetization = [0.0] * n
    for bits, prob in probabilities.items():
        z = [1 - 2 * int(bit) for bit in bits]
        for i in range(n):
            magnetization[i] += prob * z[i]
            for j in range(i, n):
                moment[i][j] += prob * z[i] * z[j]
    connected = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            connected[i][j] = connected[j][i] = moment[i][j] - magnetization[i] * magnetization[j]
    return connected


def chain_structure_factor(correlations, wavevectors):
    """S(q,t) = (1/n) sum_ij cos(q (i - j)) C_ij on a 1D unit-spacing chain."""
    n = len(correlations)
    phases = np.arange(n)
    return [float(np.sum(np.array(correlations) * np.cos(q * (phases[:, None] - phases[None, :]))) / n)
            for q in wavevectors]


def occupations(total, modes):
    if modes == 1:
        yield (total,)
    else:
        for k in range(total + 1):
            for rest in occupations(total - k, modes - 1):
                yield (k,) + rest


def permanent(matrix):
    n = len(matrix)
    return sum((np.prod([matrix[i, p[i]] for i in range(n)])
                for p in itertools.permutations(range(n))), start=0j)


def fock_probabilities(work):
    unitary = np.array(work["unitary_real"]) + 1j * np.array(work["unitary_imag"])
    incoming = work["input_occupation"]
    columns = [i for i, number in enumerate(incoming) for _ in range(number)]
    result = {}
    input_factor = math.prod(math.factorial(x) for x in incoming)
    for outgoing in occupations(sum(incoming), len(incoming)):
        rows = [i for i, number in enumerate(outgoing) for _ in range(number)]
        amplitude = permanent(unitary[np.ix_(rows, columns)])
        probability = abs(amplitude)**2 / (input_factor * math.prod(math.factorial(x) for x in outgoing))
        result[",".join(map(str, outgoing))] = float(probability)
    return result


def sample(probabilities, shots, seed):
    labels = list(probabilities)
    p = np.maximum(0, np.array(list(probabilities.values()), dtype=float))
    require(abs(p.sum() - 1) < 1e-8, "Reference probabilities do not normalize")
    counts = np.random.default_rng(seed).multinomial(shots, p / p.sum())
    return {label: int(count) for label, count in zip(labels, counts) if count}


def distribution(measurement):
    accepted = measurement["accepted_shots"]
    require(accepted > 0, "No accepted samples; cannot compute a score")
    return {key: value / accepted for key, value in measurement["counts"].items()}


def mean_and_se(measurement, observable):
    p = distribution(measurement)
    mean = sum(prob * observable(bits) for bits, prob in p.items())
    variance = max(0.0, sum(prob * observable(bits)**2 for bits, prob in p.items()) - mean**2)
    n = measurement["accepted_shots"]
    # Unbiased sample variance / n; n=1 does not identify a standard error.
    return float(mean), (float(np.sqrt(variance / (n - 1))) if n > 1 else None)


def tvd(measurement, reference):
    p = distribution(measurement)
    return float(sum(abs(p.get(k, 0) - reference.get(k, 0)) for k in p.keys() | reference.keys()) / 2)
