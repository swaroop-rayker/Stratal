// SCR-01 region 5 and frame D: the health dot (GET /health every 30 s) and the banner
// shown while a dependency is down, with the problem's code and suggested action.
import { useQuery } from "@tanstack/react-query";

import { ApiError, api, call } from "../api/client";
import { Badge, Button, Icon } from "../components/ui";

const SERVICES: Record<string, string> = {
  postgres: "Postgres",
  redis: "Redis",
  data_volume: "Data volume",
};

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => call(() => api.GET("/api/v1/health")),
    refetchInterval: 30_000,
    retry: false,
  });
}

/** The services a failed check names, read from its detail ("Health check failed: postgres."). */
function failedServices(error: ApiError): string[] {
  if (error.problem.status === 0) return ["API"];
  const names = Object.keys(SERVICES).filter((key) => error.problem.detail.includes(key));
  return names.length ? names.map((key) => SERVICES[key] ?? key) : ["A dependency"];
}

export function HealthDot() {
  const health = useHealth();
  if (health.error instanceof ApiError) {
    const down = failedServices(health.error);
    return (
      <div className="health down" role="alert">
        <Icon name="x" />
        <span className="label">{`${down.join(", ")} down`}</span>
      </div>
    );
  }
  return (
    <div className="health" role="status">
      <Icon name="heart" />
      <span className="label">{health.isSuccess ? "All systems ok" : "Checking…"}</span>
    </div>
  );
}

export function HealthBanner() {
  const health = useHealth();
  if (!(health.error instanceof ApiError)) return null;
  const { problem } = health.error;
  const down = failedServices(health.error);
  const title =
    problem.status === 0 ? "The API is unreachable" : `${down.join(", ")} ${down.length > 1 ? "are" : "is"} unreachable`;
  return (
    <div className="banner" role="alert">
      <Icon name="alert" />
      <b>{title}</b>
      <Badge tone="err">{problem.code}</Badge>
      {problem.suggested_action ? (
        <span className="why">
          <b style={{ display: "inline", color: "var(--text-primary)" }}>Do:</b>{" "}
          {problem.suggested_action}
        </span>
      ) : null}
      <span className="spacer" />
      <Button
        size="sm"
        disabled={health.isFetching}
        onClick={() => {
          void health.refetch();
        }}
      >
        <Icon name="refresh" />
        {health.isFetching ? "Retrying…" : "Retry"}
      </Button>
    </div>
  );
}
