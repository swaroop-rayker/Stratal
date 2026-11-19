// SCR-08's graph edits (ADR-0017 rule 1): the canvas and the list view share these.
import { describe, expect, test } from "vitest";

import {
  addAfter,
  connect,
  emptyGraph,
  type Graph,
  layout,
  moveNode,
  newId,
  removeNode,
  setInputs,
} from "./graph";

function chainOf(...types: string[]): Graph {
  let graph = emptyGraph("t");
  for (const type of types) graph = addAfter(graph, null, { type })[0];
  return graph;
}

describe("graph edits", () => {
  test("test_BUILD_1_1_layers_are_added_in_a_chain_before_the_output", () => {
    const graph = chainOf("conv2d", "relu", "global_avg_pool");
    expect(graph.nodes.map((n) => n.id)).toEqual(["c1", "r1", "g1"]);
    expect(graph.edges).toEqual(
      expect.arrayContaining([["input", "c1"], ["c1", "r1"], ["r1", "g1"], ["g1", "output"]]),
    );
    expect(graph.edges).toHaveLength(4);
  });

  test("test_BUILD_1_1_adding_after_a_selected_node_inserts_into_the_chain", () => {
    const [graph, id] = addAfter(chainOf("conv2d", "global_avg_pool"), "c1", { type: "batch_norm" });
    expect(id).toBe("bn1");
    expect(graph.edges).toEqual(expect.arrayContaining([["c1", "bn1"], ["bn1", "g1"]]));
    expect(graph.edges).not.toContainEqual(["c1", "g1"]);
  });

  test("test_BUILD_1_9_a_skip_source_keeps_its_skip_when_a_layer_is_inserted_after_it", () => {
    let graph = chainOf("conv2d", "relu", "add", "global_avg_pool"); // c1 r1 add1 g1
    graph = connect(graph, "c1", "add1");
    const [next] = addAfter(graph, "c1", { type: "batch_norm" });
    expect(next.edges).toContainEqual(["c1", "add1"]); // the skip stays
    expect(next.edges).toEqual(expect.arrayContaining([["c1", "bn1"], ["bn1", "r1"]]));
  });

  test("removing a node joins its neighbours", () => {
    const graph = removeNode(chainOf("conv2d", "relu", "global_avg_pool"), "r1");
    expect(graph.edges).toEqual(expect.arrayContaining([["c1", "g1"]]));
    expect(graph.nodes.map((n) => n.id)).toEqual(["c1", "g1"]);
  });

  test("moving swaps neighbours in a plain chain and refuses elsewhere", () => {
    const graph = chainOf("conv2d", "relu", "global_avg_pool");
    const moved = moveNode(graph, "r1", "up");
    expect(moved?.edges).toEqual(expect.arrayContaining([["input", "r1"], ["r1", "c1"], ["c1", "g1"]]));
    expect(moveNode(graph, "c1", "up")).toBeNull(); // the input does not move
  });

  test("a merge's inputs are set as a list", () => {
    const graph = setInputs(chainOf("conv2d", "relu", "add"), "add1", ["r1", "c1"]);
    expect(graph.edges.filter(([, b]) => b === "add1")).toEqual([["r1", "add1"], ["c1", "add1"]]);
  });

  test("new IDs never collide", () => {
    const graph = chainOf("conv2d", "conv2d");
    expect(newId("conv2d", graph)).toBe("c3");
    expect(newId("relu", graph)).toBe("r1");
  });

  test("test_BUILD_1_9_layout_ranks_by_longest_path_so_skips_span_ranks", () => {
    let graph = chainOf("conv2d", "relu", "add", "global_avg_pool");
    graph = connect(graph, "c1", "add1");
    const rank = Object.fromEntries(layout(graph).map((p) => [p.id, p.rank]));
    expect(rank).toEqual({ input: 0, c1: 1, r1: 2, add1: 3, g1: 4, output: 5 });
  });
});
