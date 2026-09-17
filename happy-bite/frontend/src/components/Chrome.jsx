/* The furniture: sheet, toast, timer bar, dish image, tab bar. */

import { useEffect, useState } from "react";
import { Icon } from "./Icon.jsx";
import { tintFor } from "../core/engine.js";
import { useUI } from "../state/ui.jsx";

export function Sheet({ title, children, onClose, wide = false }) {
  useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="sheet">
      <div className="sheet-scrim" onClick={onClose} />
      <section className={"sheet-panel" + (wide ? " is-wide" : "")} role="dialog" aria-modal="true" aria-label={title}>
        <header className="sheet-head">
          <h2 className="sheet-title">{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" /></button>
        </header>
        <div className="sheet-body">{children}</div>
      </section>
    </div>
  );
}

export function Toast() {
  const { toast } = useUI();
  if (!toast) return null;
  return <div className="toast" role="status">{toast.text}</div>;
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.35, 0.7].forEach((t) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; o.connect(g); g.connect(ctx.destination);
      g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.25);
      o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.3);
    });
  } catch { /* no audio, the bar still says Done */ }
}

export function TimerBar() {
  const { timer, stopTimer, say } = useUI();
  const secondsLeft = () => (timer ? Math.max(0, Math.ceil((timer.end - Date.now()) / 1000)) : 0);
  const [left, setLeft] = useState(secondsLeft);
  useEffect(() => {
    setLeft(secondsLeft());
    if (!timer) return;
    // Checked 4x a second for accuracy; React skips the render unless the second changed.
    const h = setInterval(() => setLeft(secondsLeft()), 250);
    return () => clearInterval(h);
  }, [timer]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (timer && left === 0 && !timer.rang) { timer.rang = true; beep(); say("Timer finished: " + timer.label); }
  }, [left, timer, say]);
  if (!timer) return null;
  const frac = 1 - left / timer.total;
  return (
    <div className={"timer-bar" + (left === 0 ? " is-done" : "")} role="timer">
      <span className="timer-fill" style={{ transform: `scaleX(${frac})` }} />
      <span className="timer-clock">{left === 0 ? "Done" : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`}</span>
      <span className="timer-label">{timer.label}</span>
      <button className="timer-stop" onClick={stopTimer}>{left === 0 ? "Dismiss" : "Stop"}</button>
    </div>
  );
}

/* The dish picture, or a tinted plate with its initial when there's none.
   The tint comes from the name, so a dish keeps its colour week to week. */
export function DishImage({ recipe, className = "dish-photo", priority = false, children }) {
  const [broken, setBroken] = useState(false);
  const src = recipe.photo || recipe.remotePhoto;
  useEffect(() => setBroken(false), [src]);
  return (
    <div className={className} style={{ "--tint": tintFor(recipe.name) }}>
      <svg className="dish-fallback" viewBox="0 0 64 64" aria-hidden="true">
        <circle cx="32" cy="32" r="19" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.55" />
        <circle cx="32" cy="32" r="11" fill="none" stroke="currentColor" strokeWidth="2.5" opacity="0.35" />
        <path d="M13 20v9a3 3 0 0 0 3 3v13" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" opacity="0.55" />
        <path d="M51 20c-3 0-5 3-5 7s2 6 5 6v12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" opacity="0.55" />
      </svg>
      {src && !broken && <img src={src} alt="" decoding="async" onError={() => setBroken(true)}
                              {...(priority ? { fetchpriority: "high" } : { loading: "lazy" })} />}
      {children}
    </div>
  );
}

const TABS = [
  { id: "today", label: "Today", icon: "home" },
  { id: "kitchen", label: "Kitchen", icon: "box" },
  null,
  { id: "recipes", label: "Recipes", icon: "book" },
  { id: "shopping", label: "Shopping", icon: "cart" }
];

/* Four tabs around one round button, the way a phone dashboard does it.
   The button doesn't navigate: it opens a tray of things to *do*. */
export function TabBar() {
  const { tab, setTab, openChat, openSheet, server, scanReceipt } = useUI();
  const [tray, setTray] = useState(false);
  const act = (fn) => { setTray(false); fn(); };

  return (
    <>
      {tray && <div className="tray-scrim" onClick={() => setTray(false)} />}
      {tray && (
        <div className="tray" role="menu">
          <button className="tray-item" role="menuitem" onClick={() => act(() => openChat())}>
            <Icon name="chef" /> <span>Ask the chef</span>
          </button>
          <button className="tray-item" role="menuitem" onClick={() => act(() => openSheet("create"))}>
            <Icon name="spark" /> <span>Create a recipe</span>
          </button>
          <button className="tray-item" role="menuitem" onClick={() => act(() => openSheet("link"))}>
            <Icon name="link" /> <span>Add from a link</span>
          </button>
          <button className="tray-item" role="menuitem" onClick={() => act(() => scanReceipt())}>
            <Icon name="receipt" /> <span>Scan a receipt</span>
          </button>
          {!server.chat && <p className="tray-note">The chef, pictures and links need the server. Everything else works without it.</p>}
        </div>
      )}
      <nav className="tabs" aria-label="Sections">
        {TABS.map((t, i) => t ? (
          <button key={t.id} className="tab" aria-current={tab === t.id ? "page" : undefined}
                  onClick={() => { setTray(false); setTab(t.id); }}>
            <Icon name={t.icon} size={26} />
            <span>{t.label}</span>
          </button>
        ) : (
          <div key={i} className="tab-fab-slot">
            <button className={"tab-fab" + (tray ? " is-open" : "")} onClick={() => setTray(!tray)}
                    aria-expanded={tray} aria-label={tray ? "Close" : "Add or ask"}>
              <Icon name="plus" size={30} />
            </button>
          </div>
        ))}
      </nav>
    </>
  );
}
