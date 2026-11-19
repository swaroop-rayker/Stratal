// Forms generated from a stage's JSON Schema (DESIGN-SYSTEM d, "Forms from JSON schema";
// UI-2.3's pattern, used in phase 1 by the Start pipeline drawer). rjsf does the work;
// these templates give it the design system's field: the name in mono with its type as a
// badge, the range, default and description as helper text, errors in their place.
import Form from "@rjsf/core";
import type {
  BaseInputTemplateProps,
  ErrorSchema,
  FieldTemplateProps,
  ObjectFieldTemplateProps,
  RJSFSchema,
  UiSchema,
  WidgetProps,
} from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import type { ChangeEvent } from "react";

import { Icon } from "./ui";

type Values = Record<string, unknown>;

function typeName(schema: RJSFSchema): string {
  if (schema.enum) return "enum";
  if (schema.type === "integer") return "int";
  if (schema.type === "number") return "float";
  if (schema.type === "boolean") return "bool";
  if (schema.type === "array") return "list";
  if (typeof schema.type === "string") return schema.type;
  return "value";
}

/** A value as text; objects as JSON, never "[object Object]". */
export function show(value: unknown): string {
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}

function helper(schema: RJSFSchema): string {
  const parts: string[] = [];
  const low = schema.minimum ?? schema.exclusiveMinimum;
  const high = schema.maximum ?? schema.exclusiveMaximum;
  if (low !== undefined && high !== undefined) parts.push(`${String(low)}–${String(high)}`);
  else if (low !== undefined) parts.push(`at least ${String(low)}`);
  else if (high !== undefined) parts.push(`at most ${String(high)}`);
  if (schema.default !== undefined && schema.default !== null) {
    const value = Array.isArray(schema.default) ? schema.default.join(", ") : show(schema.default);
    parts.push(`default ${value}`);
  }
  const head = parts.join(" · ");
  const tail = schema.description ?? "";
  return head && tail ? `${head}. ${tail}` : head || tail;
}

function FieldTemplate(props: FieldTemplateProps) {
  const { id, schema, children, rawErrors, hidden, fieldPathId } = props;
  if (fieldPathId.path.length === 0) return children; // the root object
  if (hidden || schema.readOnly) return null; // set by the system, not by the user
  const name = String(fieldPathId.path[fieldPathId.path.length - 1]);
  const error = rawErrors?.[0];
  return (
    <div className={error ? "field error" : "field"}>
      <label htmlFor={id}>
        <span className="mono">{name}</span>
        <span className="type">{typeName(schema)}</span>
      </label>
      {children}
      <small id={`${id}__help`}>
        {error ? (
          <>
            <Icon name="x" style={{ width: 12, height: 12 }} />
            {error}
          </>
        ) : (
          helper(schema)
        )}
      </small>
    </div>
  );
}

function ObjectFieldTemplate({ properties }: ObjectFieldTemplateProps) {
  return <>{properties.map((p) => p.content)}</>;
}

function BaseInputTemplate(props: BaseInputTemplateProps) {
  const { id, schema, disabled, readonly, onChange, onBlur, onFocus, rawErrors, placeholder } = props;
  const value: unknown = props.value;
  const numeric = schema.type === "integer" || schema.type === "number";
  const parse = (raw: string): unknown => {
    if (raw === "") return undefined;
    if (!numeric) return raw;
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  };
  return (
    <input
      id={id}
      className={numeric ? "input mono" : "input"}
      inputMode={numeric ? "decimal" : undefined}
      value={value === undefined || value === null ? "" : show(value)}
      placeholder={placeholder || undefined}
      disabled={disabled || readonly}
      aria-invalid={rawErrors?.length ? true : undefined}
      aria-describedby={`${id}__help`}
      onChange={(event: ChangeEvent<HTMLInputElement>) => {
        onChange(parse(event.target.value));
      }}
      onBlur={(event) => {
        onBlur(id, event.target.value);
      }}
      onFocus={(event) => {
        onFocus(id, event.target.value);
      }}
    />
  );
}

function SelectWidget({ id, options, value, disabled, onChange }: WidgetProps) {
  const choices = (options.enumOptions ?? []) as { label: string; value: unknown }[];
  return (
    <select
      id={id}
      className="select mono"
      value={String(value)}
      disabled={disabled}
      aria-describedby={`${id}__help`}
      onChange={(event) => {
        onChange(choices.find((c) => String(c.value) === event.target.value)?.value);
      }}
    >
      {choices.map((c) => (
        <option key={String(c.value)} value={String(c.value)}>
          {c.label}
        </option>
      ))}
    </select>
  );
}

function CheckboxWidget({ id, value, disabled, onChange, label }: WidgetProps) {
  return (
    <label className="checks">
      <input
        id={id}
        type="checkbox"
        checked={Boolean(value)}
        disabled={disabled}
        onChange={(event) => {
          onChange(event.target.checked);
        }}
      />
      {label}
    </label>
  );
}

function CheckboxesWidget({ id, options, value, disabled, onChange }: WidgetProps) {
  const choices = (options.enumOptions ?? []) as { label: string; value: unknown }[];
  const chosen = Array.isArray(value) ? (value as unknown[]) : [];
  return (
    <div className="checks" id={id} role="group">
      {choices.map((c) => (
        <label key={String(c.value)}>
          <input
            type="checkbox"
            checked={chosen.includes(c.value)}
            disabled={disabled}
            onChange={(event) => {
              const next = event.target.checked
                ? choices.filter((o) => o.value === c.value || chosen.includes(o.value))
                : choices.filter((o) => o.value !== c.value && chosen.includes(o.value));
              onChange(next.map((o) => o.value));
            }}
          />
          <span className="mono">{c.label}</span>
        </label>
      ))}
    </div>
  );
}

/** Arrays of enum values are shown as checkboxes; everything else uses its default widget.
 * ``placeholders`` puts a hint in an empty field (SCR-08: "inferred: 3"). */
function uiSchemaFor(schema: RJSFSchema, placeholders: Record<string, string> = {}): UiSchema {
  const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
  for (const [name, text] of Object.entries(placeholders)) ui[name] = { "ui:placeholder": text };
  const defs = (schema.$defs ?? {}) as Record<string, RJSFSchema>;
  for (const [name, prop] of Object.entries(schema.properties ?? {})) {
    if (typeof prop === "boolean" || prop.type !== "array" || typeof prop.items !== "object") continue;
    const items = prop.items as RJSFSchema;
    const ref = items.$ref?.split("/").pop();
    const target = ref ? defs[ref] : items;
    if (target?.enum) ui[name] = { "ui:widget": "checkboxes" };
  }
  return ui;
}

export function SchemaForm({ schema, values, onChange, serverErrors, placeholders }: {
  schema: RJSFSchema;
  values: Values;
  onChange: (values: Values, valid: boolean) => void;
  serverErrors?: Record<string, string>;
  placeholders?: Record<string, string>;
}) {
  const extra: ErrorSchema = {};
  for (const [field, message] of Object.entries(serverErrors ?? {})) {
    (extra as Record<string, unknown>)[field] = { __errors: [message] };
  }
  return (
    <Form
      schema={schema}
      uiSchema={uiSchemaFor(schema, placeholders)}
      formData={values}
      validator={validator}
      liveValidate="onChange"
      showErrorList={false}
      transformErrors={(list) =>
        list.map((e) => {
          const limit = (e.params as { limit?: number } | undefined)?.limit;
          if (e.name === "minimum" && limit !== undefined) return { ...e, message: `must be at least ${String(limit)}` };
          if (e.name === "maximum" && limit !== undefined) return { ...e, message: `must be at most ${String(limit)}` };
          return e;
        })
      }
      extraErrors={extra}
      templates={{ FieldTemplate, ObjectFieldTemplate, BaseInputTemplate }}
      widgets={{ SelectWidget, CheckboxWidget, CheckboxesWidget }}
      onChange={(event) => {
        onChange((event.formData ?? {}) as Values, event.errors.length === 0);
      }}
      tagName="div"
    >
      <></>
    </Form>
  );
}
