// Editing a model graph on SCR-08 (ADR-0017 rule 1): plain functions over the spec, so the
// canvas and the list view make the same changes and both can be tested without a DOM.
// The server decides validity (POST /model-specs/validate); these only keep the shape sane.

export interface LayerSpec {
  type: string;
  [field: string]: unknown;
}

export interface GraphNode {
  id: string;
  layer: LayerSpec;
}

export interface Graph {
  spec_version: 1;
  label: string;
  nodes: GraphNode[];
  edges: [string, string][];
}

export const INPUT = "input";
export const OUTPUT = "output";
const MERGES = new Set(["add", "concat"]);

const PREFIX: Record<string, string> = {
  linear: "l", conv2d: "c", max_pool2d: "p", avg_pool2d: "ap", global_avg_pool: "g",
  batch_norm: "bn", layer_norm: "ln", dropout: "d", flatten: "f", embedding: "e",
  rnn: "rnn", lstm: "lstm", gru: "gru", attention: "att", positional_encoding: "pe",
  relu: "r", leaky_relu: "lr", gelu: "gelu", tanh: "tanh", sigmoid: "sig", softmax: "sm",
  add: "add", concat: "cat",
};

export function emptyGraph(label = "model"): Graph {
  return { spec_version: 1, label, nodes: [], edges: [[INPUT, OUTPUT]] };
}

export function isMerge(graph: Graph, id: string): boolean {
  const node = graph.nodes.find((n) => n.id === id);
  return node !== undefined && MERGES.has(node.layer.type);
}

/** A short unused ID for a new layer: ``c3`` for the third convolution's slot. */
export function newId(type: string, graph: Graph): string {
  const prefix = PREFIX[type] ?? type.slice(0, 3);
  const used = new Set(graph.nodes.map((n) => n.id));
  for (let i = 1; ; i += 1) {
    const id = `${prefix}${String(i)}`;
    if (!used.has(id)) return id;
  }
}

export function inputsOf(graph: Graph, id: string): string[] {
  return graph.edges.filter(([, to]) => to === id).map(([from]) => from);
}

export function outputsOf(graph: Graph, id: string): string[] {
  return graph.edges.filter(([from]) => from === id).map(([, to]) => to);
}

/** The node that feeds the output, where a new layer goes when nothing is selected. */
export function lastNode(graph: Graph): string {
  return inputsOf(graph, OUTPUT)[0] ?? INPUT;
}

/** Insert ``layer`` after ``after``, taking over its edge to the next layer in the chain
 * (not its skip edges into merges, unless that is all it has). Returns the new graph and ID. */
export function addAfter(graph: Graph, after: string | null, layer: LayerSpec): [Graph, string] {
  const from = after ?? lastNode(graph);
  const id = newId(layer.type, graph);
  const outs = outputsOf(graph, from);
  const chain = outs.filter((to) => !isMerge(graph, to));
  const next = chain[0] ?? outs[0];
  const edges = graph.edges.filter(([a, b]) => !(a === from && b === next));
  edges.push([from, id]);
  if (next !== undefined) edges.push([id, next]);
  const nodes = [...graph.nodes];
  const at = nodes.findIndex((n) => n.id === from);
  nodes.splice(at + 1, 0, { id, layer });
  return [{ ...graph, nodes, edges }, id];
}

/** Remove a node; its first input takes over its outputs, so a chain stays joined. */
export function removeNode(graph: Graph, id: string): Graph {
  const ins = inputsOf(graph, id);
  const outs = outputsOf(graph, id);
  const edges = graph.edges.filter(([a, b]) => a !== id && b !== id);
  const source = ins[0];
  if (source !== undefined) {
    for (const to of outs) {
      if (!edges.some(([a, b]) => a === source && b === to)) edges.push([source, to]);
    }
  }
  return { ...graph, nodes: graph.nodes.filter((n) => n.id !== id), edges };
}

/** Swap a node with its neighbour in a plain chain (one edge in, one edge out on both).
 * Returns ``null`` where that is not a plain chain. */
export function moveNode(graph: Graph, id: string, direction: "up" | "down"): Graph | null {
  const [upper, lower] = direction === "up" ? [inputsOf(graph, id)[0], id] : [id, outputsOf(graph, id)[0]];
  if (upper === undefined || lower === undefined) return null;
  if ([upper, lower].some((n) => n === INPUT || n === OUTPUT)) return null;
  const plain = (n: string) => inputsOf(graph, n).length === 1 && outputsOf(graph, n).length === 1;
  if (!plain(upper) || !plain(lower) || outputsOf(graph, upper)[0] !== lower) return null;
  const before = inputsOf(graph, upper)[0];
  const after = outputsOf(graph, lower)[0];
  if (before === undefined || after === undefined) return null;
  const drop = new Set([`${before}>${upper}`, `${upper}>${lower}`, `${lower}>${after}`]);
  const edges = graph.edges.filter(([a, b]) => !drop.has(`${a}>${b}`));
  edges.push([before, lower], [lower, upper], [upper, after]);
  const nodes = [...graph.nodes];
  const i = nodes.findIndex((n) => n.id === upper);
  const j = nodes.findIndex((n) => n.id === lower);
  const a = nodes[i];
  const b = nodes[j];
  if (a === undefined || b === undefined) return null;
  nodes[i] = b;
  nodes[j] = a;
  return { ...graph, nodes, edges };
}

/** Replace a node's incoming edges. */
export function setInputs(graph: Graph, id: string, inputs: string[]): Graph {
  const edges = graph.edges.filter(([, b]) => b !== id);
  for (const from of inputs) edges.push([from, id]);
  return { ...graph, edges };
}

/** Add an edge, e.g. a skip into a merge; ignores self-loops and duplicates. */
export function connect(graph: Graph, from: string, to: string): Graph {
  if (from === to || graph.edges.some(([a, b]) => a === from && b === to)) return graph;
  return { ...graph, edges: [...graph.edges, [from, to]] };
}

export function setLayer(graph: Graph, id: string, layer: LayerSpec): Graph {
  return { ...graph, nodes: graph.nodes.map((n) => (n.id === id ? { ...n, layer } : n)) };
}

export interface Placed {
  id: string;
  rank: number;
  slot: number;
  /** How many nodes share this rank. */
  of: number;
}

/** Automatic layout: each node's rank is its longest path from the input, so a skip edge
 * spans ranks; nodes on one rank sit side by side. The output is one rank below the last. */
export function layout(graph: Graph): Placed[] {
  const ids = [INPUT, ...graph.nodes.map((n) => n.id), OUTPUT];
  const rank = new Map<string, number>(ids.map((id) => [id, 0]));
  // Longest path by relaxation; a cycle (a spec problem) is cut off after |V| rounds.
  for (let round = 0; round < ids.length; round += 1) {
    let changed = false;
    for (const [a, b] of graph.edges) {
      const next = (rank.get(a) ?? 0) + 1;
      if (b !== INPUT && next > (rank.get(b) ?? 0) && next <= ids.length) {
        rank.set(b, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const deepest = Math.max(0, ...graph.nodes.map((n) => rank.get(n.id) ?? 0));
  rank.set(OUTPUT, Math.max(rank.get(OUTPUT) ?? 0, deepest + 1));
  rank.set(INPUT, 0);
  const byRank = new Map<number, string[]>();
  for (const id of ids) {
    const r = rank.get(id) ?? 0;
    byRank.set(r, [...(byRank.get(r) ?? []), id]);
  }
  return ids.map((id) => {
    const r = rank.get(id) ?? 0;
    const row = byRank.get(r) ?? [id];
    return { id, rank: r, slot: row.indexOf(id), of: row.length };
  });
}

/** The draft of a spec, kept per dataset version for this browser session (SCR-08 decision 9). */
export function draftKey(versionId: string): string {
  return `stratal.builder.${versionId}`;
}

export function loadDraft(versionId: string): Graph | null {
  try {
    const raw = sessionStorage.getItem(draftKey(versionId));
    return raw ? (JSON.parse(raw) as Graph) : null;
  } catch {
    return null;
  }
}

export function saveDraft(versionId: string, graph: Graph): void {
  try {
    sessionStorage.setItem(draftKey(versionId), JSON.stringify(graph));
  } catch {
    // Storage can be full or refused; the draft then lives only in memory.
  }
}
