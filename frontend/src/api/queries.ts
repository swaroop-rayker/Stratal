// Query hooks shared by the screens. Keys start with the resource, so a `job` message can
// invalidate everything a finished stage may have changed (datasets, versions).
import { useQuery } from "@tanstack/react-query";

import { api, call } from "./client";

export const keys = {
  datasets: ["datasets"] as const,
  dataset: (slug: string) => ["datasets", slug] as const,
  versions: (slug: string) => ["datasets", slug, "versions"] as const,
  version: (id: string) => ["versions", id] as const,
  sources: ["sources"] as const,
  schemas: ["config", "schemas"] as const,
  schema: (name: string) => ["config", "schemas", name] as const,
  defaults: ["config", "defaults"] as const,
};

export function useDatasets() {
  return useQuery({
    queryKey: keys.datasets,
    queryFn: () => call(() => api.GET("/api/v1/datasets", { params: { query: { limit: 500 } } })),
  });
}

export function useDataset(slug: string | undefined) {
  return useQuery({
    queryKey: keys.dataset(slug ?? ""),
    queryFn: () =>
      call(() => api.GET("/api/v1/datasets/{slug}", { params: { path: { slug: slug ?? "" } } })),
    enabled: slug !== undefined,
  });
}

export function useVersions(slug: string | undefined) {
  return useQuery({
    queryKey: keys.versions(slug ?? ""),
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/datasets/{slug}/versions", {
          params: { path: { slug: slug ?? "" }, query: { limit: 500 } },
        }),
      ),
    enabled: slug !== undefined,
  });
}

export function useSources(enabled: boolean) {
  return useQuery({
    queryKey: keys.sources,
    queryFn: () => call(() => api.GET("/api/v1/sources")),
    enabled,
  });
}

export function useStageSchemas(enabled: boolean) {
  return useQuery({
    queryKey: keys.schemas,
    queryFn: () => call(() => api.GET("/api/v1/config/schemas")),
    enabled,
    staleTime: Infinity,
  });
}

export function useStageDefaults(enabled: boolean) {
  return useQuery({
    queryKey: keys.defaults,
    queryFn: () => call(() => api.GET("/api/v1/config/defaults")),
    enabled,
    staleTime: Infinity,
  });
}

export function useStageSchema(name: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.schema(name),
    queryFn: () =>
      call(() => api.GET("/api/v1/config/schemas/{name}", { params: { path: { name } } })),
    enabled,
    staleTime: Infinity,
  });
}
