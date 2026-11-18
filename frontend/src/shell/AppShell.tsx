// SCR-01: the frame every screen sits in (docs/SCREENS.md §3, approved mockup SCR-01_v1).
import { useQueryClient } from "@tanstack/react-query";
import { Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router";

import { useGlobalFeed } from "../api/stream";
import { Modal, ModalClose } from "../components/Modal";
import { Icon, type IconName } from "../components/ui";
import { HealthBanner, HealthDot } from "./Health";
import { type Crumb, CrumbContext, useShortcuts, useTheme } from "./hooks";
import { JobIndicator, useActiveJobs } from "./JobIndicator";
import { ResourceStrip } from "./ResourceStrip";
import { Toasts } from "./Toasts";

const NAV: { group: string; to: string; label: string; icon: IconName; keys: string }[] = [
  { group: "Data", to: "/datasets", label: "Datasets", icon: "db", keys: "g d" },
  { group: "Jobs", to: "/jobs", label: "Jobs", icon: "list", keys: "g j" },
  { group: "Models", to: "/models", label: "Models", icon: "grid", keys: "g m" },
];

const SHORTCUTS: [string, ReactNode][] = [
  ["g d", "Datasets"],
  ["/", "Filter this list"],
  ["g j", "Jobs"],
  ["g m", "Models"],
  ["j", <>Next row (<kbd>k</kbd> previous)</>],
  ["g a", "Active job"],
  ["Enter", "Open the row"],
  ["t", "Switch theme"],
  ["Esc", "Close a dialog or drawer"],
  ["?", "This help"],
  ["a", <>Accept in review (<kbd>r</kbd> reject)</>],
];

function Crumbs({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav className="crumbs" aria-label="Location">
      {crumbs.map((crumb, i) => {
        const last = i === crumbs.length - 1;
        const text = crumb.id ? <span className="mono">{crumb.label}</span> : crumb.label;
        const body = i === 0 ? <b>{text}</b> : text;
        return (
          <Fragment key={`${crumb.label}-${String(i)}`}>
            {i > 0 ? <Icon name="chevron" /> : null}
            {crumb.to && !last ? (
              <Link to={crumb.to}>{body}</Link>
            ) : crumb.id ? (
              <button
                type="button"
                className="btn ghost sm"
                style={{ height: "auto", padding: 0, color: "inherit" }}
                title="Copy"
                aria-label={`${crumb.label}, copy`}
                onClick={() => void navigator.clipboard.writeText(crumb.label)}
              >
                {body}
              </button>
            ) : (
              <span aria-current={last ? "page" : undefined}>{body}</span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

export function AppShell() {
  const connection = useGlobalFeed();
  const client = useQueryClient();
  const wasDown = useRef(false);
  // Back from an outage: what changed meanwhile is read again (the stream replays events,
  // but the lists were last read before it dropped).
  useEffect(() => {
    if (connection === "reconnecting") wasDown.current = true;
    if (connection === "live" && wasDown.current) {
      wasDown.current = false;
      void client.invalidateQueries();
    }
  }, [connection, client]);
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  const [help, setHelp] = useState(false);
  const [, toggleTheme] = useTheme();
  const navigate = useNavigate();
  const active = useActiveJobs();

  const bindings = useMemo(
    () => ({
      "g d": () => void navigate("/datasets"),
      "g j": () => void navigate("/jobs"),
      "g m": () => void navigate("/models"),
      "g a": () => {
        const job = active.data?.items[0];
        if (job) void navigate(`/jobs/${job.id}`);
      },
      t: toggleTheme,
      "?": () => {
        setHelp(true);
      },
    }),
    [navigate, toggleTheme, active.data],
  );
  useShortcuts(bindings);

  return (
    <CrumbContext.Provider value={setCrumbs}>
      <div className="shell">
        <nav className="side" aria-label="Main">
          <div className="brand">
            <Icon name="layers" />
            <span className="label">Stratal</span>
          </div>
          {NAV.map((item) => (
            <Fragment key={item.to}>
              <div className="group">{item.group}</div>
              <NavLink className="nav" to={item.to} aria-label={item.label}>
                <Icon name={item.icon} />
                <span className="label">{item.label}</span>
                <kbd>{item.keys}</kbd>
                <span className="tip rail-tip" aria-hidden="true">
                  {item.label} · {item.keys}
                </span>
              </NavLink>
            </Fragment>
          ))}
          <div className="foot">
            <HealthDot />
            <NavLink className="nav" to="/settings" aria-label="Settings">
              <Icon name="sliders" />
              <span className="label">Settings</span>
              <span className="tip rail-tip" aria-hidden="true">
                Settings
              </span>
            </NavLink>
            <button type="button" className="nav" onClick={toggleTheme} aria-label="Switch theme">
              <Icon name="theme" />
              <span className="label">Theme</span>
              <kbd>t</kbd>
            </button>
            <button
              type="button"
              className="nav"
              onClick={() => {
                setHelp(true);
              }}
              aria-label="Keyboard shortcuts"
            >
              <Icon name="keys" />
              <span className="label">Shortcuts</span>
              <kbd>?</kbd>
            </button>
          </div>
        </nav>
        <header className="top">
          <Crumbs crumbs={crumbs} />
          <span className="spacer" />
          <JobIndicator />
        </header>
        <main className="content" id="content">
          <Outlet />
        </main>
        <ResourceStrip connection={connection} />
      </div>
      <HealthBanner />
      <Toasts />
      <Modal
        open={help}
        onOpenChange={setHelp}
        title="Keyboard shortcuts"
        width={520}
        icon={<Icon name="keys" />}
        footer={
          <ModalClose asChild>
            <button type="button" className="btn sm">
              Close <kbd>Esc</kbd>
            </button>
          </ModalClose>
        }
      >
        <div className="keys">
          {SHORTCUTS.map(([keys, what]) => (
            <Fragment key={keys}>
              <kbd>{keys}</kbd>
              <span>{what}</span>
            </Fragment>
          ))}
        </div>
        <p style={{ margin: "var(--sp-3) 0 0", fontSize: "var(--fs-12)" }}>
          Everything also works with Tab and Enter. Focus returns to where you were when this
          closes.
        </p>
      </Modal>
    </CrumbContext.Provider>
  );
}
