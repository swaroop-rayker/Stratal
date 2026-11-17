// Query hooks shared by the screens. Keys start with the resource, so a `job` message can
// invalidate everything a finished stage may have changed (datasets, versions).
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { ApiError, api, call } from "./client";

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

export function useVersion(id: string) {
  return useQuery({
    queryKey: keys.version(id),
    queryFn: () =>
      call(() => api.GET("/api/v1/versions/{version_id}", { params: { path: { version_id: id } } })),
    retry: false,
  });
}

export function useLineage(id: string) {
  return useQuery({
    queryKey: ["versions", id, "lineage"],
    queryFn: () =>
      call(() =>
        api.GET("/api/v1/versions/{version_id}/lineage", { params: { path: { version_id: id } } }),
      ),
  });
}

export function useStats(id: string) {
  return useQuery({
    queryKey: ["versions", id, "stats"],
    queryFn: () =>
      call(() => api.GET("/api/v1/versions/{version_id}/stats", { params: { path: { version_id: id } } })),
    staleTime: Infinity, // a version never changes (INV-01)
  });
}

export interface RowFilters {
  status?: "accepted" | "rejected" | "duplicate" | "filtered";
  reason_code?: string;
  split?: "train" | "val" | "test";
  dup_group_id?: string;
  q?: string;
}

export const ROW_PAGE = 200;

export function useRows(id: string, filters: RowFilters, enabled = true) {
  return useInfiniteQuery({
    queryKey: ["versions", id, "rows", filters],
    queryFn: ({ pageParam }) =>
      call(() =>
        api.GET("/api/v1/versions/{version_id}/rows", {
          params: {
            path: { version_id: id },
            query: { ...filters, limit: ROW_PAGE, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: Infinity,
    enabled,
  });
}

/** The newest report of a version, or null when it has none (404). */
export function useLatestReport(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["versions", id, "report"],
    queryFn: async () => {
      try {
        return await call(() =>
          api.GET("/api/v1/versions/{version_id}/reports/latest", {
            params: { path: { version_id: id }, query: { kind: "phase1_dataset" } },
          }),
        );
      } catch (error) {
        if (error instanceof ApiError && error.problem.status === 404) return null;
        throw error;
      }
    },
    enabled,
  });
}
