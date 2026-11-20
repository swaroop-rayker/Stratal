// Layer types for SCR-08, read from GET /model-specs/schema (UI-2.3): the palette, the
// defaults a new layer starts with, the inspector's form and a node's one-line summary.
// Nothing here lists a layer's fields by hand, so a field added to a layer's Pydantic
// model appears in its form with no change here (UI-2.3.AC1).
import type { RJSFSchema } from "@rjsf/utils";

import type { DatasetType } from "../../api/builder";
import type { LayerSpec } from "../../lib/graph";

export interface LayerType {
  type: string;
  title: string;
  group: string;
  /** The form's schema: the layer's fields without ``type``, nullable fields flattened. */
  form: RJSFSchema;
  /** Fields left empty to be inferred from the input (``in_features``, ``in_channels``). */
  inferred: string[];
}

const GROUPS: [string, string[]][] = [
  ["Dense", ["linear"]],
  ["Convolution", ["conv2d", "max_pool2d", "avg_pool2d", "global_avg_pool"]],
  ["Shape and norm", ["batch_norm", "layer_norm", "dropout", "flatten"]],
  ["Activation", ["relu", "leaky_relu", "gelu", "tanh", "sigmoid", "softmax"]],
  ["Merge", ["add", "concat"]],
  ["Sequence", ["embedding", "positional_encoding", "attention", "lstm", "gru", "rnn"]],
];

const TITLES: Record<string, string> = {
  linear: "Linear", conv2d: "Conv2d", max_pool2d: "MaxPool2d", avg_pool2d: "AvgPool2d",
  global_avg_pool: "Global avg pool", batch_norm: "BatchNorm", layer_norm: "LayerNorm",
  dropout: "Dropout", flatten: "Flatten", embedding: "Embedding", rnn: "RNN", lstm: "LSTM",
  gru: "GRU", attention: "Attention", positional_encoding: "Positional encoding", relu: "ReLU",
  leaky_relu: "LeakyReLU", gelu: "GELU", tanh: "Tanh", sigmoid: "Sigmoid", softmax: "Softmax",
  add: "Add", concat: "Concat",
};

/** What each layer needs from the dataset type (DATA-5.5): a family it accepts, or tokens. */
const NEEDS: Record<string, { family?: string; tokens?: boolean }> = {
  conv2d: { family: "cnn" }, max_pool2d: { family: "cnn" }, avg_pool2d: { family: "cnn" },
  rnn: { family: "rnn" }, lstm: { family: "rnn" }, gru: { family: "rnn" },
  attention: { family: "transformer" }, positional_encoding: { family: "transformer" },
  embedding: { tokens: true },
};

type Def = RJSFSchema & { properties?: Record<string, RJSFSchema> };

function flatten(prop: RJSFSchema): [RJSFSchema, boolean] {
  const options = (prop.anyOf ?? []) as RJSFSchema[];
  const real = options.filter((o) => o.type !== "null");
  if (options.length === 0 || real.length !== 1) return [prop, false];
  const rest: RJSFSchema = { ...prop };
  delete rest.anyOf;
  return [{ ...rest, ...real[0], default: undefined }, true];
}

/** Every layer type in the schema, in palette order. */
export function layerTypes(layerSchema: Record<string, unknown>): LayerType[] {
  const defs = (layerSchema.$defs ?? {}) as Record<string, Def>;
  const found = new Map<string, LayerType>();
  for (const def of Object.values(defs)) {
    const typeProp = def.properties?.type;
    const type = typeProp?.const ?? typeProp?.default;
    if (typeof type !== "string") continue;
    const properties: Record<string, RJSFSchema> = {};
    const inferred: string[] = [];
    for (const [name, prop] of Object.entries(def.properties ?? {})) {
      if (name === "type") continue;
      const [flat, nullable] = flatten(prop);
      properties[name] = flat;
      if (nullable && (name.startsWith("in_") || name === "num_embeddings")) inferred.push(name);
    }
    found.set(type, {
      type,
      title: TITLES[type] ?? def.title ?? type,
      group: GROUPS.find(([, types]) => types.includes(type))?.[0] ?? "Other",
      form: { type: "object", properties, $defs: layerSchema.$defs as RJSFSchema["$defs"] },
      inferred,
    });
  }
  const order = GROUPS.flatMap(([, types]) => types);
  return [...found.values()].sort(
    (a, b) => (order.indexOf(a.type) + 1 || 999) - (order.indexOf(b.type) + 1 || 999),
  );
}

/** A new layer of ``type`` with its schema's defaults filled in. */
export function newLayer(spec: LayerType): LayerSpec {
  const layer: LayerSpec = { type: spec.type };
  for (const [name, prop] of Object.entries(spec.form.properties ?? {})) {
    const value = typeof prop === "object" ? prop.default : undefined;
    if (value !== undefined && value !== null) layer[name] = value;
  }
  return layer;
}

/** Why a layer is greyed out for this dataset type, or ``null`` if it is offered (DATA-5.5). */
export function unavailable(type: string, datasetType: DatasetType | null): string | null {
  const need = NEEDS[type];
  if (need === undefined || datasetType === null) return null;
  const takes = `${datasetType.title} take ${datasetType.families.join(" or ")} models`;
  if (need.family !== undefined && !datasetType.families.includes(need.family as never)) return takes;
  if (need.tokens && datasetType.input_kind !== "tokens") return `${datasetType.title} have no tokens to embed`;
  return null;
}

/** A node's key fields in a few characters, for its card: "32 · k3 · p1 · no bias". */
export function summary(layer: LayerSpec): string {
  const v = layer as Record<string, unknown>;
  const n = (key: string) => {
    const value = v[key];
    return typeof value === "number" || typeof value === "string" ? String(value) : "";
  };
  switch (layer.type) {
    case "linear":
      return `→ ${n("out_features")}`;
    case "conv2d":
      return `${n("out_channels")} · k${n("kernel_size")} · p${n("padding")}${v.bias === false ? " · no bias" : ""}`;
    case "max_pool2d":
    case "avg_pool2d":
      return `k${n("kernel_size")}`;
    case "dropout":
      return `p ${n("p")}`;
    case "embedding":
      return n("embedding_dim");
    case "attention":
      return `${n("num_heads")} heads${v.causal === true ? " · causal" : ""}`;
    case "rnn":
    case "lstm":
    case "gru":
      return `${n("hidden_size")}${v.bidirectional === true ? " ×2" : ""} · ${n("output")}`;
    default:
      return "";
  }
}
