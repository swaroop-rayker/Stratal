// Queries and mutations for the model builder (SCR-08, SCR-09; API.md §3.11, ADR-0017).
// Validation runs on every edit, debounced, and keeps the last result on screen while the
// next one is computed (SCR-08 frame F).
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { Graph } from "../lib/graph";
import { api, call, type Schemas } from "./client";

export type SpecSchema = Schemas["SpecSchema"];
export type ValidateResponse = Schemas["ValidateResponse"];
export type SpecReport = NonNullable<ValidateResponse["report"]>;
export type DatasetEnds = Schemas["DatasetEnds"];
export type NodeReport = Schemas["NodeReport"];
export type SpecProblem = Schemas["Problem"];
export type DatasetType = Schemas["DatasetTypeOut"];
export type Optimizer = "adam" | "sgd" | "sgd_momentum";
type GraphBody = Schemas["GraphSpec"];

export const builderKeys = {
  schema: ["builder", "schema"] as const,
  types: ["builder", "types"] as const,
  templates: (type: string | null) => ["builder", "templates", type] as const,
  validate: (version: string, graph: string, optimizer: Optimizer) =>
    ["builder", "validate", version, graph, optimizer] as const,
};

export function useSpecSchema() {
  return useQuery({
    queryKey: builderKeys.schema,
    queryFn: () => call(() => api.GET("/api/v1/model-specs/schema")),
    staleTime: Infinity,
  });
}

export function useDatasetTypes() {
  return useQuery({
    queryKey: builderKeys.types,
    queryFn: () => call(() => api.GET("/api/v1/dataset-types")),
    staleTime: Infinity,
  });
}

export function useTemplates(datasetType: string | null) {
  return useQuery({
    queryKey: builderKeys.templates(datasetType),
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/model-templates", {
          params: { query: { dataset_type: datasetType ?? undefined } },
        }),
      ),
  });
}

/** A template as the list endpoint returns it. */
export type Template = NonNullable<ReturnType<typeof useTemplates>["data"]>[number];

/** A value that settles ``ms`` after its last change. */
export function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSettled(value);
    }, ms);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, ms]);
  return settled;
}

/** The spec between the version's two locked ends (BUILD-1.3 to 1.6), 250 ms after an edit. */
export function useValidate(version: string | null, graph: Graph, optimizer: Optimizer) {
  const body = useDebounced(JSON.stringify(graph), 250);
  return useQuery({
    queryKey: builderKeys.validate(version ?? "", body, optimizer),
    queryFn: () =>
      call(() =>
        api.POST("/api/v1/model-specs/validate", {
          body: {
            graph: JSON.parse(body) as GraphBody,
            dataset_version: version ?? "",
            optimizer,
          },
        }),
      ),
    enabled: version !== null,
    placeholderData: keepPreviousData,
  });
}

export function useSaveTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; description: string; dataset_type: string; spec: Graph }) =>
      call(() => api.POST("/api/v1/model-templates", { body: { ...input, spec: input.spec as GraphBody } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["builder", "templates"] });
    },
  });
}

export function useDeleteTemplate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      call(() => api.DELETE("/api/v1/model-templates/{template_id}", { params: { path: { template_id: id } } })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["builder", "templates"] });
    },
  });
}

/** The parameter count of a template on this dataset's ends, for the start screen. */
export function useTemplateCount(version: string | null, spec: Graph) {
  return useValidate(version, spec, "adam");
}
