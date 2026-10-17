/**
 * The instrument primitives: the few building blocks every view is made of (docs/DESIGN.md).
 *
 *   Panel      a hairline container with a mono-caps title, an optional figure number and an aside
 *   Figure     a chart or schematic with its caption ("FIG. 2  Equity, net of costs")
 *   Readout    a big number with its unit and label, like a gauge on a bench instrument
 *   KeyValues  label/value pairs in mono: the inspector grid
 *   Tag        a state in a word: CRIT, WARN, INFO, OK (never colour alone)
 *   Hatch      a hatched band: an inactive area, or "nothing here yet"
 *   InlineText text with `code` and **bold** spans, for messages that quote commands
 */
import { Fragment, type ReactNode } from "react";

export function Panel({
  title,
  fig,
  aside,
  actions,
  marks,
  className,
  id,
  children,
}: {
  title?: ReactNode;
  fig?: string;
  aside?: ReactNode;
  actions?: ReactNode;
  marks?: boolean;
  className?: string;
  id?: string;
  children?: ReactNode;
}) {
  return (
    <section id={id} className={`card${marks ? " marks" : ""}${className ? ` ${className}` : ""}`}>
      {title || aside || actions ? (
        <div className="panel-head">
          <h2>
            {fig ? <span className="fig">{fig}</span> : null}
            {title}
          </h2>
          {aside || actions ? (
            <span className="row tight">
              {aside ? <span className="aside">{aside}</span> : null}
              {actions}
            </span>
          ) : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Figure({
  fig,
  title,
  aside,
  className,
  children,
}: {
  fig?: string;
  title: ReactNode;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`card figure${className ? ` ${className}` : ""}`}>
      <div className="caption">
        {fig ? <span className="fig">{fig}</span> : null}
        <span>{title}</span>
        {aside ? <span className="aside">{aside}</span> : null}
      </div>
      {children}
    </section>
  );
}

export type Tone = "accent" | "pos" | "neg";

export function Readout({
  label,
  value,
  unit,
  note,
  tone,
  size,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  note?: ReactNode;
  tone?: Tone;
  size?: "sm";
}) {
  return (
    <div className={`readout${tone ? ` ${tone}` : ""}${size ? ` ${size}` : ""}`}>
      <div className="value">
        {value}
        {unit ? <span className="unit">{unit}</span> : null}
      </div>
      <div className="label">{label}</div>
      {note ? <div className="note">{note}</div> : null}
    </div>
  );
}

/** A row of readouts separated by hairlines. */
export function Readouts({ children }: { children: ReactNode }) {
  return <div className="readouts">{children}</div>;
}

export function KeyValues({ rows, columns = 1 }: { rows: [ReactNode, ReactNode][]; columns?: 1 | 2 }) {
  return (
    <dl className={`kv-grid${columns === 2 ? " two" : ""}`}>
      {rows.map(([k, v], i) => (
        <Fragment key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export type TagKind = "crit" | "warn" | "info" | "ok" | "ink";

export function Tag({ kind = "info", children, title }: { kind?: TagKind; children: ReactNode; title?: string }) {
  return (
    <span className={`tag ${kind}`} title={title}>
      {children}
    </span>
  );
}

export function Hatch({ className }: { className?: string }) {
  return <div className={`hatch${className ? ` ${className}` : ""}`} aria-hidden="true" />;
}

/** `code` and **bold** spans inside a plain sentence (status lines, gate details, risk messages). */
export function InlineText({ text }: { text: string }) {
  const out: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = pattern.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    out.push(tok.startsWith("`") ? <code key={i++}>{tok.slice(1, -1)}</code> : <b key={i++}>{tok.slice(2, -2)}</b>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}
