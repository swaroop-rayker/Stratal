// The Train a model drawer (SCREENS.md v3; mockup SCR-09v2_v1, frames A–C, G, H), shared by
// SCR-11, SCR-03 and SCR-09 (ML-1.1, BUILD-1.3, UI-2.1). Step 1 picks the version a model trains
// on (a split, or a tokenize version for labelled text); step 2 the kinds that version allows:
// a network for every trainable type (→ SCR-08), a classical model for tables only (→ SCR-09).
// A kind that is not allowed stays visible, disabled, with its reason in words.
import { type KeyboardEvent, useState } from "react";
import { useNavigate } from "react-router";

import { useDatasetTypes, useValidate } from "../../api/builder";
import { type Schemas } from "../../api/client";
import { useDatasets, useVersion, useVersions } from "../../api/queries";
import { Drawer } from "../../components/Drawer";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Button, Icon, Skeleton } from "../../components/ui";
import { count } from "../../lib/format";
import { emptyGraph } from "../../lib/graph";
import { StartPipelineDrawer } from "../datasets/StartPipelineDrawer";

export type Kind = "network" | "classical";

type Version = Schemas["Version"];
type Ends = Schemas["DatasetEnds"];

const EMPTY = emptyGraph();

/** Versions a model trains on: split versions, and tokenize versions for labelled text (whose
 * tokens and vocabulary fix the input; its split version is refused by the builder). */
export function trainable(v: Pick<Version, "stage" | "dtype">): boolean {
  return v.stage === "tokenize" || (v.stage === "split" && v.dtype !== "labelled_text");
}

/** Classification or regression, as SCR-09 reads it from the version's ends. */
export function taskOf(ends: Pick<Ends, "classes" | "output_size">): "classification" | "regression" {
  return ends.classes.length === 0 && ends.output_size === 1 ? "regression" : "classification";
}

function splits(counts: Record<string, unknown> | undefined): [string, number][] {
  const split = counts?.split as { splits?: Record<string, { rows?: number }> } | undefined;
  const order = ["train", "val", "test"];
  return Object.entries(split?.splits ?? {})
    .map(([k, v]): [string, number] => [k, v.rows ?? 0])
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
}

function inputText(ends: Ends): string {
  const dims = (ends.input as { dims?: number[] } | null)?.dims;
  const input = ends.dataset_type === "tabular" && dims?.length === 1 ? `${String(dims[0])} features` : ends.input_text ?? "?";
  const output = taskOf(ends) === "regression" ? "1 value" : `${String(ends.output_size ?? "?")} classes`;
  return `${input} → ${output}`;
}

/** What SCR-09 holds when the drawer is opened by its Change…: the current kind, and the
 * network that choosing Classical would leave behind (none for a builder draft: it stays). */
export interface KindChange {
  current: Kind | null;
  leftBehind: { what: string; draft: boolean } | null;
  onPick: (kind: Kind) => void;
}

export function TrainDrawer({ open, onOpenChange, version, change, returnFocus }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Step 1 filled with this version: the drawer opens at step 2. */
  version?: string | null;
  change?: KindChange;
  returnFocus?: () => HTMLElement | null;
}) {
  const [pipeline, setPipeline] = useState<{ versionId: string; dataset: string } | null>(null);
  return (
    <>
      <Drawer
        open={open && pipeline === null}
        onOpenChange={onOpenChange}
        title={change?.current ? "Change the model kind" : "Train a model"}
        className="train-drawer"
        returnFocus={returnFocus}
      >
        {open ? <Steps version={version ?? null} change={change} onClose={() => { onOpenChange(false); }} onPipeline={setPipeline} /> : null}
      </Drawer>
      {pipeline ? (
        <StartPipelineDrawer
          open
          from={{ mode: "version", versionId: pipeline.versionId, dataset: pipeline.dataset }}
          onOpenChange={(o) => { if (!o) { setPipeline(null); onOpenChange(false); } }}
        />
      ) : null}
    </>
  );
}

function Steps({ version: fixed, change, onClose, onPipeline }: {
  version: string | null;
  change?: KindChange;
  onClose: () => void;
  onPipeline: (p: { versionId: string; dataset: string }) => void;
}) {
  const navigate = useNavigate();
  const [step, setStep] = useState<1 | 2>(fixed ? 2 : 1);
  const [slug, setSlug] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(fixed);
  const [kindChoice, setKind] = useState<Kind | null>(null);

  const fixedInfo = useVersion(fixed ?? "", fixed !== null);
  const dataset = slug ?? fixedInfo.data?.dataset ?? null;
  const datasets = useDatasets();
  const versions = useVersions(dataset ?? undefined);
  const options = (versions.data?.items ?? []).filter(trainable);
  const info = useVersion(picked ?? "", picked !== null);
  const validation = useValidate(picked, EMPTY, "adam");
  const ends = validation.data?.dataset.version_id === picked ? validation.data.dataset : null;
  const types = useDatasetTypes();
  const type = types.data?.find((t) => t.name === ends?.dataset_type) ?? null;

  // Which kinds this version allows, and why not.
  const problem = ends ? ends.problem ?? (ends.trainable ? null : type?.note ?? "This version cannot be trained.") : null;
  const typeTitle = (type?.title ?? ends?.dataset_type ?? "").toLowerCase();
  const holds = info.data?.stage === "split" ? "split" : "version";
  const reasons: Record<Kind, string | null> = {
    network: problem ? (ends?.trainable === false ? (type ? `${type.title} are not trained by the builder.` : "This type is not trained by the builder.") : problem) : null,
    classical: ends?.dataset_type === "tabular" && !problem ? null : problem ? "Classical models train on tables." : `Classical models train on tables. This ${holds} holds ${typeTitle}.`,
  };
  const allowed = (["network", "classical"] as const).filter((k) => reasons[k] === null);
  const firstAllowed = change?.current && allowed.includes(change.current) ? change.current : allowed[0] ?? null;
  const kind = kindChoice && allowed.includes(kindChoice) ? kindChoice : firstAllowed;
  const families = (type?.families ?? []).map((f) => f.toUpperCase().replace("TRANSFORMER", "transformer")).join(", ").replace(/, ([^,]*)$/, " or $1");

  const sameVersion = picked === fixed;
  const leftBehind = change && sameVersion && change.current === "network" && kind === "classical" ? change.leftBehind : null;

  function go() {
    if (!picked || !kind) return;
    // On SCR-09 for this version, a change of kind stays on the page; with no kind yet, a network
    // still goes to the builder first.
    if (change && sameVersion && (change.current !== null || kind === "classical")) {
      change.onPick(kind);
      onClose();
      return;
    }
    void navigate(kind === "network" ? `/models/build?version=${picked}` : `/runs/new?version=${picked}&kind=classical`);
    onClose();
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (event.key !== "Enter" || target.closest("button, select, a")) return;
    event.preventDefault();
    if (step === 1 && picked) setStep(2);
    else if (step === 2) go();
  }

  const stepbar = (
    <div className="stepbar" aria-label="Steps">
      <span className={step === 2 ? "st2 done" : "st2"} aria-current={step === 1 ? "step" : undefined}><i>{step === 2 ? <Icon name="check" /> : "1"}</i>Data</span>
      <Icon name="chevron" />
      <span className="st2" aria-current={step === 2 ? "step" : undefined}><i>2</i>Kind</span>
    </div>
  );

  const facts = picked ? (
    info.isError || validation.isError ? null : ends && info.data ? (
      <dl className="vfacts" aria-label="The version">
        <dt>Type</dt><dd>{type?.title ?? ends.dataset_type}</dd>
        {ends.problem === null ? <><dt>Task</dt><dd><Badge>{taskOf(ends)}</Badge>{taskOf(ends) === "classification" ? ` ${String(ends.output_size ?? "?")} classes` : null}</dd></> : null}
        {ends.classes.length > 0 && ends.classes.length <= 6 ? <><dt>Classes</dt><dd className="mono">{ends.classes.join(", ")}</dd></> : null}
        {ends.input_text ? <><dt>Input → output</dt><dd className="mono">{inputText(ends)}</dd></> : null}
        {splits(info.data.counts).length ? <><dt>Splits</dt><dd className="mono">{splits(info.data.counts).map(([k, v]) => `${k} ${count(v)}`).join(" · ")}</dd></> : null}
      </dl>
    ) : (
      <div className="vfacts" aria-busy="true"><Skeleton width={80} /><Skeleton width={200} /><Skeleton width={80} /><Skeleton width={160} /></div>
    )
  ) : null;

  const loadError = info.error ?? validation.error ?? types.error;
  const errorState = loadError ? (
    <ErrorState error={loadError} title={step === 1 ? "The version could not be loaded" : "The model kinds could not be loaded"}
      onRetry={() => { void info.refetch(); void validation.refetch(); void types.refetch(); }} />
  ) : null;

  let body;
  if (step === 1) {
    const latest = versions.data?.items[0];
    body = datasets.data && datasets.data.items.length === 0 ? (
      <EmptyState icon="db" title="No datasets yet." action={<Button size="sm" onClick={() => { void navigate("/datasets"); onClose(); }}>Open Datasets</Button>}>
        Put a folder under <span className="mono">.\sources</span> and ingest it.
      </EmptyState>
    ) : datasets.isError ? (
      <ErrorState error={datasets.error} title="Datasets could not be loaded" onRetry={() => void datasets.refetch()} />
    ) : (
      <>
        <div className="field">
          <label htmlFor="train-dataset">Dataset</label>
          <select id="train-dataset" className="select mono" value={dataset ?? ""} onChange={(e) => { setSlug(e.target.value || null); setPicked(null); setKind(null); }}>
            <option value="">Choose a dataset…</option>
            {(datasets.data?.items ?? []).map((d) => <option key={d.slug} value={d.slug}>{d.slug}</option>)}
          </select>
        </div>
        {dataset && versions.data && options.length === 0 ? (
          <EmptyState icon="info" title={`No version of ${dataset} to train on yet.`}
            action={latest ? <Button size="sm" onClick={() => { onPipeline({ versionId: latest.version_id, dataset }); }}>Continue the pipeline</Button> : undefined}>
            {latest ? <>Its latest is <span className="mono">{latest.version_id}</span> ({latest.stage}). </> : null}Run the pipeline to its split stage.
          </EmptyState>
        ) : (
          <div className="field">
            <label htmlFor="train-version">Version to train on</label>
            <select id="train-version" className="select mono" value={picked ?? ""} disabled={!dataset} onChange={(e) => { setPicked(e.target.value || null); setKind(null); }}>
              <option value="">{dataset ? "Choose a version…" : "—"}</option>
              {options.map((v) => <option key={v.version_id} value={v.version_id}>{v.version_id} · {v.stage} · {count(v.row_count)} rows · {v.created_at.slice(0, 10)}</option>)}
              {picked && !options.some((v) => v.version_id === picked) ? <option value={picked}>{picked}</option> : null}
            </select>
            <small>Split versions; for text, tokenize versions. Newest first.</small>
          </div>
        )}
        {errorState ?? facts}
      </>
    );
  } else {
    body = (
      <>
        <div className="chosen">
          <span>
            <b>{info.data?.dataset ?? "…"}</b> · <span className="mono">{picked}</span>
            {info.data ? <> · {info.data.stage}</> : null}
            {ends ? <> · {type?.title.toLowerCase() ?? ends.dataset_type}{ends.input_text ? <> · <span className="mono">{inputText(ends)}</span></> : null}</> : null}
          </span>
          <span className="spacer" />
          <Button size="sm" variant="ghost" onClick={() => { setStep(1); }}>Change</Button>
        </div>
        {errorState ?? (!ends || !types.data ? (
          <div aria-busy="true" style={{ display: "flex", flexDirection: "column", gap: "var(--sp-2)" }}><Skeleton width={360} height={64} /><Skeleton width={360} height={64} /></div>
        ) : (
          <>
            {problem ? (
              <div className="callout info"><Icon name="info" /><span><b>Nothing can be trained on this version.</b> {problem}</span></div>
            ) : null}
            <fieldset className="kinds">
              <legend>What kind of model?</legend>
              <KindCard kind="network" title="Network" checked={kind === "network"} reason={reasons.network} current={change?.current === "network"} onPick={setKind}
                what={`Layers you stack in the builder${families ? ` (${families})` : ""}, or a template. GPU or CPU, with a VRAM check.`}
                to="Next: the builder, then the run setup" icon="branch" />
              <KindCard kind="classical" title="Classical" checked={kind === "classical"} reason={reasons.classical} current={change?.current === "classical"} onPick={setKind}
                what="A scikit-learn estimator, such as a random forest, with its parameters. CPU, seconds."
                to="Next: the run setup, no builder" icon="sliders" />
            </fieldset>
            {leftBehind ? (
              <div className="callout warn" role="status">
                <Icon name="alert" />
                <span>
                  {leftBehind.draft
                    ? <><b>The network will not be used for this run.</b> The builder's draft for this split stays in the builder.</>
                    : <><b>The network from {leftBehind.what} will not be used.</b> That run and its versions are untouched.</>}
                </span>
              </div>
            ) : null}
          </>
        ))}
      </>
    );
  }

  const primary = step === 1
    ? <Button variant="primary" disabled={!picked || !ends} onClick={() => { setStep(2); }}>Next<Icon name="chevron" /></Button>
    : change?.current && sameVersion
      ? <Button variant="primary" disabled={!kind} onClick={go}>{kind === "classical" ? "Use Classical" : "Use Network"}</Button>
      : <Button variant="primary" disabled={!kind} onClick={go}>{kind === "classical" ? "Set up the run" : kind === "network" ? "Open the builder" : "Next"}{kind ? <Icon name="chevron" /> : null}</Button>;

  return (
    <div className="train-steps" onKeyDown={onKey}>
      {stepbar}
      <div className="steps-body">{body}</div>
      <div className="steps-foot">
        {step === 2 && !fixed ? <Button variant="ghost" onClick={() => { setStep(1); }}>Back</Button> : null}
        <Button variant="ghost" onClick={onClose}>{leftBehind ? "Keep the network" : "Cancel"}</Button>
        <span className="spacer" />
        {primary}
      </div>
    </div>
  );
}

function KindCard({ kind, title, what, to, icon, checked, reason, current, onPick }: {
  kind: Kind;
  title: string;
  what: string;
  to: string;
  icon: "branch" | "sliders";
  checked: boolean;
  reason: string | null;
  current: boolean;
  onPick: (kind: Kind) => void;
}) {
  const off = reason !== null;
  return (
    <label className="kc" aria-disabled={off || undefined}>
      <input type="radio" name="train-kind" value={kind} checked={checked && !off} disabled={off} onChange={() => { onPick(kind); }} />
      <b>{title}{off ? <Badge>not for this split</Badge> : current ? <Badge>current</Badge> : null}</b>
      {off ? (
        <span className="why"><Icon name="info" /><span>{reason}</span></span>
      ) : (
        <>
          <span className="what">{what}</span>
          <span className="to"><Icon name={icon} />{to}</span>
        </>
      )}
    </label>
  );
}
