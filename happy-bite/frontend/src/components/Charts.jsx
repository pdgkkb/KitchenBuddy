/* Happy Bite — the charts. Hand-drawn SVG, no chart library, no network.

   Borrowed from dashboard apps: rings for proportions, a gradient bar with
   a marker for "where on the scale", little bar strips for "how the week
   went". Every chart also says its number in words — a colour or a length
   is never the only carrier of meaning. */

import { useState } from "react";

const TAU = Math.PI * 2;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

/* One progress arc. Starts at twelve o'clock, rounded ends. */
export function Ring({ value, size = 64, stroke = 8, color = "var(--mint)", track = "var(--ring-track)", children, label }) {
  const r = (size - stroke) / 2;
  const c = TAU * r;
  const v = clamp(value || 0, 0, 1);
  return (
    <div className="ring" style={{ width: size, height: size }} role={label ? "img" : undefined} aria-label={label}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        {v > 0 && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
                  strokeLinecap="round" pathLength={1} strokeDasharray={`${Math.max(v, 0.001)} 1`}
                  style={{ "--v": Math.max(v, 0.001) }}
                  transform={`rotate(-90 ${size / 2} ${size / 2})`} className="ring-arc" />
        )}
      </svg>
      {children && <div className="ring-center">{children}</div>}
    </div>
  );
}

/* Concentric rings, outermost first. */
export function RingStack({ rings, size = 150, stroke = 13, gap = 4, children, label }) {
  return (
    <div className="ring" style={{ width: size, height: size }} role="img" aria-label={label}>
      {rings.map((r, i) => {
        const s = size - i * 2 * (stroke + gap);
        return (
          <div key={i} className="ring-layer" style={{ inset: i * (stroke + gap) }}>
            <Ring value={r.value} size={s} stroke={stroke} color={r.color} />
          </div>
        );
      })}
      {children && <div className="ring-center">{children}</div>}
    </div>
  );
}

/* Where a value sits on its scale. Butter to mint, with a knob.
   `centre` draws a tick at zero for scales that go negative. */
export function RangeBar({ value, min = 0, max = 1, left, right, shown }) {
  const pos = (clamp(value, min, max) - min) / (max - min || 1);
  const zero = min < 0 ? (0 - min) / (max - min) : null;
  return (
    <div className="range">
      <div className="range-track">
        {zero !== null && <span className="range-zero" style={{ left: `${zero * 100}%` }} />}
        <span className="range-knob" style={{ left: `${pos * 100}%` }}>
          <span className="range-value">{shown}</span>
        </span>
      </div>
      <div className="range-ends"><span>{left ?? min}</span><span>{right ?? max}</span></div>
    </div>
  );
}

/* A strip of small bars — the week at a glance. */
export function SparkBars({ values, color = "var(--mint)", hot = () => false, height = 34 }) {
  const top = Math.max(1, ...values);
  return (
    <div className="spark" style={{ height }} aria-hidden="true">
      {values.map((v, i) => (
        <span key={i} className="spark-bar"
              style={{ height: `${Math.max(8, (v / top) * 100)}%`,
                       background: v ? (hot(i) ? "var(--coral)" : color) : "var(--ring-track)" }} />
      ))}
    </div>
  );
}

/* What goes off when, over the next two weeks. The first four days sit
   in a coral band: that's the "use these first" window. Tap a day to see
   what's in it. */
export function ExpiryChart({ bins, nameOf }) {
  const [picked, setPicked] = useState(null);
  const W = 360, H = 150, padL = 8, padB = 30, padT = 14;
  const n = bins.length;
  const bw = (W - padL * 2) / n;
  const top = Math.max(2, ...bins.map(b => b.length));
  const y = (v) => H - padB - (v / top) * (H - padB - padT);
  const days = bins.map((_, i) => { const d = new Date(); d.setDate(d.getDate() + i); return d; });
  const letter = (d) => d.toLocaleDateString(undefined, { weekday: "narrow" });

  return (
    <div className="expiry">
      <svg viewBox={`0 0 ${W} ${H}`} className="expiry-svg" role="img"
           aria-label={`Items going off over the next ${n} days`}>
        <rect x={padL} y={padT - 6} width={bw * 4} height={H - padB - padT + 6} rx="10" className="expiry-zone" />
        <text x={padL + 8} y={padT + 10} className="expiry-zone-label">Use soon</text>
        {[0.5, 1].map(f => (
          <line key={f} x1={padL} x2={W - padL} y1={y(top * f)} y2={y(top * f)} className="expiry-grid" />
        ))}
        {bins.map((b, i) => {
          const x = padL + i * bw + bw * 0.2;
          const w = bw * 0.6;
          const on = picked === i;
          return (
            <g key={i} onClick={() => setPicked(on ? null : i)} className="expiry-col">
              <rect x={padL + i * bw} y={padT} width={bw} height={H - padT} fill="transparent" />
              {b.length > 0 && (
                <rect x={x} y={y(b.length)} width={w} height={H - padB - y(b.length)} rx={w / 2.4}
                      className={"expiry-bar" + (i < 4 ? " is-soon" : "") + (on ? " is-on" : "")} />
              )}
              {b.length > 0 && <text x={x + w / 2} y={y(b.length) - 6} className="expiry-count">{b.length}</text>}
              <text x={x + w / 2} y={H - 8} className={"expiry-day" + (i === 0 ? " is-today" : "")}>
                {letter(days[i])}
              </text>
            </g>
          );
        })}
        <line x1={padL} x2={W - padL} y1={H - padB} y2={H - padB} className="expiry-axis" />
      </svg>
      <p className="expiry-note" aria-live="polite">
        {picked === null
          ? "Tap a day to see what goes off."
          : bins[picked].length
            ? `${picked === 0 ? "Today (or already past)" : days[picked].toLocaleDateString(undefined, { weekday: "long" })}: ${bins[picked].map(nameOf).join(", ")}`
            : "Nothing goes off that day."}
      </p>
    </div>
  );
}

/* The last seven days, one ring each: filled when something was cooked. */
export function WeekStrip({ week }) {
  return (
    <ol className="week" aria-label="Meals cooked this week">
      {week.map((d, i) => {
        const isToday = i === week.length - 1;
        return (
          <li key={d.key} className={"week-day" + (isToday ? " is-today" : "")}>
            <span className="week-letter">{d.date.toLocaleDateString(undefined, { weekday: "narrow" })}</span>
            <Ring value={d.cooked ? 1 : 0} size={42} stroke={5}
                  color={isToday ? "var(--sky)" : "var(--mint)"}
                  label={`${d.date.toLocaleDateString(undefined, { weekday: "long" })}: ${d.cooked} cooked`}>
              <span className="week-num">{d.date.getDate()}</span>
            </Ring>
          </li>
        );
      })}
    </ol>
  );
}
