"""Graph specs and their validation (ADR-0017 rules 1, 4, 5 and 7).

A spec is nodes (a layer each) and directed edges, between two fixed nodes,
``input`` and ``output``, whose shapes come from the dataset. Validation never
raises for a bad spec: it returns every problem it can find, in words, each with
its node and a stable code, plus what it could work out (shapes, parameter
counts, the memory estimate) for the parts that are valid (BUILD-1.4 to
BUILD-1.6). A spec whose fields are out of range does not get this far: Pydantic
refuses it with field errors (BUILD-1.2).
"""

import hashlib
import heapq
import itertools
import json
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from stratal.build.layers import Family, Layer, head_parameters
from stratal.build.shapes import Shape, SpecProblem

INPUT, OUTPUT = "input", "output"
MAX_NODES, MAX_EDGES = 200, 400
NODE_ID = r"^[a-z][a-z0-9_]{0,31}$"
FAMILY_ORDER: tuple[Family, ...] = ("transformer", "rnn", "cnn")
"""A graph with layers of several families takes the first in this order (ADR-0017 rule 5)."""

Optimizer = Literal["adam", "sgd", "sgd_momentum"]
OPTIMIZER_BYTES_PER_PARAMETER: dict[Optimizer, int] = {"adam": 8, "sgd_momentum": 4, "sgd": 0}
"""Adam keeps two fp32 values per parameter; SGD with momentum one; plain SGD none."""
BYTES = 4  # fp32


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=NODE_ID, description="Short name, unique in the spec.")
    layer: Layer

    @field_validator("id")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value in (INPUT, OUTPUT):
            raise ValueError(f"'{value}' is the name of a fixed node")
        return value


class GraphSpec(BaseModel):
    """A model as layers and edges (ADR-0017 rule 1)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec_version: Literal[1] = 1
    label: str = Field(
        default="model",
        min_length=1,
        max_length=64,
        description="The model's name in lists. Not part of its identity.",
    )
    nodes: list[Node] = Field(default_factory=list[Node], max_length=MAX_NODES)
    edges: list[tuple[str, str]] = Field(
        default_factory=list[tuple[str, str]], max_length=MAX_EDGES
    )


# ---------------------------------------------------------------------------- report


class Problem(BaseModel):
    node_id: str | None
    code: str
    message: str


class NodeReport(BaseModel):
    id: str
    type: str
    inputs: list[str]
    shape: dict[str, Any] | None
    """The node's output shape, or ``None`` where it could not be worked out."""
    shape_text: str | None
    parameters: int | None


class Head(BaseModel):
    in_features: int
    out_features: int
    parameters: int


class Memory(BaseModel):
    optimizer: Optimizer
    weights_bytes: int
    gradients_bytes: int
    optimizer_bytes: int
    total_bytes: int
    activation_bytes_per_sample: int
    """Roughly what a training step keeps per sample for the backward pass: an estimate."""


class SpecReport(BaseModel):
    valid: bool
    input: dict[str, Any]
    output_size: int
    nodes: list[NodeReport]
    head: Head | None
    total_parameters: int | None
    family: Family | None
    memory: Memory | None
    problems: list[Problem]
    structure_hash: str | None


# ---------------------------------------------------------------------------- structure


def _order(ids: list[str], edges: list[tuple[str, str]]) -> tuple[list[str], set[str]]:
    """Kahn's topological order of ``ids``, ties by ID; and the nodes left in cycles."""
    known = set(ids)
    indegree = dict.fromkeys(ids, 0)
    after: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        if dst in known and (src in known or src == INPUT):
            after[src].append(dst)
            if src != INPUT:
                indegree[dst] += 1
    ready = [i for i in ids if indegree[i] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for nxt in after[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, nxt)
    return order, known - set(order)


def canonical(spec: GraphSpec) -> GraphSpec:
    """The one JSON form of a structure: nodes in topological order (ties by ID), edges
    sorted by their ends' positions (ADR-0017 rule 1, BUILD-1.7). A cyclic graph keeps
    its nodes sorted by ID."""
    ids = [n.id for n in spec.nodes]
    order, stuck = _order(ids, spec.edges)
    sequence = order + sorted(stuck)
    rank = {node: i for i, node in enumerate(sequence)}
    rank[INPUT], rank[OUTPUT] = -1, len(sequence)
    by_id = {n.id: n for n in spec.nodes}
    edges = sorted(set(spec.edges), key=lambda e: (rank.get(e[0], 10**6), rank.get(e[1], 10**6), e))
    return spec.model_copy(update={"nodes": [by_id[i] for i in sequence], "edges": edges})


def structure_hash(spec: GraphSpec) -> str:
    """SHA-256 of the canonical structure, without ``label`` (ADR-0017 rule 1)."""
    form = canonical(spec)
    body = {
        "spec_version": form.spec_version,
        "nodes": [n.model_dump(mode="json") for n in form.nodes],
        "edges": [list(e) for e in form.edges],
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identity(spec: GraphSpec) -> dict[str, Any]:
    """The spec as it enters a lineage key: canonical, without ``label``."""
    form = canonical(spec).model_dump(mode="json")
    form.pop("label")
    return form


def _problem(node_id: str | None, code: str, message: str) -> Problem:
    return Problem(node_id=node_id, code=code, message=message)


def _structure_problems(spec: GraphSpec) -> list[Problem]:
    problems: list[Problem] = []
    ids = [n.id for n in spec.nodes]
    seen: set[str] = set()
    for node_id in ids:
        if node_id in seen:
            problems.append(_problem(node_id, "duplicate_id", f"two nodes are called '{node_id}'"))
        seen.add(node_id)
    endpoints = seen | {INPUT, OUTPUT}
    for src, dst in spec.edges:
        for end in (src, dst):
            if end not in endpoints:
                problems.append(
                    _problem(None, "unknown_node", f"an edge names '{end}', which is not a node")
                )
        if src == OUTPUT or dst == INPUT:
            problems.append(
                _problem(None, "edge_direction", f"{src} → {dst}: nothing leaves output, "
                         "nothing enters input")
            )
        if src == dst:
            problems.append(_problem(src, "cycle", f"'{src}' feeds itself"))
    if len(set(spec.edges)) != len(spec.edges):
        problems.append(_problem(None, "duplicate_edge", "an edge is listed twice"))
    return problems


def _reach(start: str, links: dict[str, list[str]]) -> set[str]:
    seen, stack = {start}, [start]
    while stack:
        for nxt in links.get(stack.pop(), []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


# ---------------------------------------------------------------------------- validate


def validate_graph(
    spec: GraphSpec,
    *,
    input_shape: Shape,
    output_size: int,
    optimizer: Optimizer = "adam",
    families: tuple[Family, ...] | None = None,
) -> SpecReport:
    """Every problem of ``spec`` between the dataset's two ends, and what is known of the rest.

    ``families`` are those the dataset type accepts (DATA-5.5); a graph of another family
    is a problem."""
    problems = _structure_problems(spec)
    family = family_of(spec)
    if families is not None and family not in families:
        problems.append(
            _problem(
                None,
                "incompatible_family",
                f"a {family} model does not fit this dataset type, which takes "
                f"{', '.join(families)}",
            )
        )
    form = canonical(spec) if not problems else spec
    nodes = {n.id: n for n in form.nodes}
    edges = sorted(set(form.edges)) if not problems else form.edges
    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        incoming[dst].append(src)
        outgoing[src].append(dst)

    order, stuck = _order(list(nodes), list(edges))
    for node_id in sorted(stuck):
        problems.append(_problem(node_id, "cycle", f"'{node_id}' is part of a cycle"))
    from_input = _reach(INPUT, outgoing)
    to_output = _reach(OUTPUT, incoming)
    for node_id in order:
        if node_id not in from_input:
            problems.append(
                _problem(node_id, "unreachable", f"nothing from the input reaches '{node_id}'")
            )
        elif node_id not in to_output:
            problems.append(
                _problem(node_id, "dangling", f"'{node_id}' does not lead to the output")
            )
    if not outgoing.get(INPUT):
        problems.append(_problem(None, "unreachable", "the input is not connected"))
    if len(incoming.get(OUTPUT, [])) != 1:
        count = len(incoming.get(OUTPUT, []))
        problems.append(
            _problem(None, "output_inputs", f"the output takes exactly one edge, got {count}",
            )
        )

    shapes: dict[str, Shape] = {INPUT: input_shape}
    counts: dict[str, int] = {}
    reports: list[NodeReport] = []
    for node_id in order + sorted(stuck):
        node = nodes[node_id]
        sources = input_order(incoming.get(node_id, []))
        report = NodeReport(
            id=node_id,
            type=node.layer.type,
            inputs=sources,
            shape=None,
            shape_text=None,
            parameters=None,
        )
        reports.append(report)
        if node_id in stuck or not sources:
            continue
        if not node.layer.merges and len(sources) > 1:
            problems.append(
                _problem(
                    node_id,
                    "too_many_inputs",
                    f"'{node_id}' has {len(sources)} inputs; join them with add or concat first",
                )
            )
            continue
        if not all(s in shapes for s in sources):
            continue  # an upstream problem is already reported; do not cascade
        try:
            applied = node.layer.apply([shapes[s] for s in sources])
        except SpecProblem as problem:
            problems.append(_problem(node_id, problem.code, f"{node_id}: {problem.message}"))
            continue
        shapes[node_id] = applied.shape
        counts[node_id] = applied.parameters
        report.shape = applied.shape.as_json()
        report.shape_text = applied.shape.describe()
        report.parameters = applied.parameters

    head: Head | None = None
    last = incoming.get(OUTPUT, [])
    if len(last) == 1 and last[0] in shapes:
        final = shapes[last[0]]
        if final.kind != "vector":
            problems.append(
                _problem(
                    last[0],
                    "output_not_vector",
                    f"the model must end in a vector, got {final.describe()}: flatten "
                    "or pool before the output",
                )
            )
        else:
            features = final.dims[0]
            head = Head(
                in_features=features,
                out_features=output_size,
                parameters=head_parameters(features, output_size),
            )

    valid = not problems and head is not None and len(counts) == len(nodes)
    total = sum(counts.values()) + head.parameters if valid and head is not None else None
    memory = None
    if total is not None:
        activations = sum(s.elements for s in shapes.values()) + output_size
        weights = BYTES * total
        extra = OPTIMIZER_BYTES_PER_PARAMETER[optimizer] * total
        memory = Memory(
            optimizer=optimizer,
            weights_bytes=weights,
            gradients_bytes=weights,
            optimizer_bytes=extra,
            total_bytes=2 * weights + extra,
            activation_bytes_per_sample=2 * BYTES * activations,
        )
    return SpecReport(
        valid=valid,
        input=input_shape.as_json(),
        output_size=output_size,
        nodes=reports,
        head=head,
        total_parameters=total,
        family=family,
        memory=memory,
        problems=problems,
        structure_hash=structure_hash(spec) if valid else None,
    )


def input_order(sources: list[str]) -> list[str]:
    """A merge's inputs in a fixed order, ``input`` first, then by ID; ``concat`` joins them
    in this order, here and in the compiled module."""
    return sorted(sources, key=lambda s: (s != INPUT, s))


def family_of(spec: GraphSpec) -> Family:
    """``transformer``, ``rnn`` or ``cnn`` if any layer is one, else ``mlp`` (ADR-0017 rule 5)."""
    found = {f for n in spec.nodes if (f := n.layer.family()) is not None}
    for family in FAMILY_ORDER:
        if family in found:
            return family
    return "mlp"


def chain(label: str, *layers: Any) -> GraphSpec:
    """A chain of layers from input to output, IDs ``n1``, ``n2``, … (templates, tests)."""
    nodes = [Node(id=f"n{i}", layer=layer) for i, layer in enumerate(layers, start=1)]
    ids = [INPUT, *(n.id for n in nodes), OUTPUT]
    return GraphSpec(label=label, nodes=nodes, edges=list(itertools.pairwise(ids)))
