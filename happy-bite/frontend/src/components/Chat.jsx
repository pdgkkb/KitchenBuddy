/* Happy Bite — the chef you can talk to.

   One conversation engine, two places: the full-screen chat from the round
   button, and the panel beside the steps in cooking mode. Replies stream
   in as they're written; a pasted recipe link is read by the server and
   comes back as a card you can save or start cooking.

   The chef can also change the kitchen — lower the milk, save a recipe, set
   a timer. Those arrive as `action` events mid-stream; `onAction` carries
   each one out and hands back a one-line receipt shown under the reply. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api.js";
import * as voice from "../lib/voice.js";
import * as sq from "../lib/speechqueue.js";
import { parseStock } from "../lib/stockcmd.js";
import { Icon } from "./Icon.jsx";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { ref } from "../core/engine.js";

let voiceAgreed = false;                    // asked once per session, never stored

export function useChat(context, greeting, onAction) {
  const { k } = useKitchen();
  const { server } = useUI();
  const [messages, setMessages] = useState(() => greeting ? [{ id: "hello", role: "assistant", content: greeting }] : []);
  const [busy, setBusy] = useState(false);
  /* The guard against two sends at once. A ref, not the `busy` state: after
     "stop Bob" the corrected question is sent straight away, before React has
     re-rendered, and a `busy` captured in the old closure refused it. */
  const busyRef = useRef(false);
  const abort = useRef(null);
  const live = useRef(messages);
  live.current = messages;
  const act = useRef(onAction);
  act.current = onAction;

  const patchLast = (fn) => setMessages(ms => ms.map((m, i) => i === ms.length - 1 ? { ...m, ...fn(m) } : m));

  /* Tokens arrive a dozen or more a second. Rendering each one re-rendered the
     whole screen that owns this hook — all of cooking mode, mid-recipe — for a
     change nobody can read that fast. Text is collected and drawn once a frame. */
  const pending = useRef("");
  const frame = useRef(0);
  const flush = () => {
    frame.current = 0;
    const text = pending.current;
    patchLast(() => ({ content: text, status: null }));
  };

  const send = useCallback(async (text) => {
    const clean = String(text || "").trim();
    if (!clean || busyRef.current) return "";
    const history = [...live.current, { id: "u" + Date.now(), role: "user", content: clean }];

    /* Stock changes and stock questions are answered here, instantly, without
       the server — see lib/stockcmd.js. The exchange still goes into the
       thread, so the chef sees it in the history of the next real question. */
    const local = parseStock(clean, k.stock);
    if (local) {
      const receipt = local.action && act.current ? act.current(local.action) : null;
      setMessages([...history, { id: "a" + Date.now(), role: "assistant", content: local.say,
                                 actions: receipt ? [receipt] : [] }]);
      if (k.prefs.speakReplies) {
        try { await voice.speak(local.say, server.voice); } catch { /* ignore */ }
      }
      return local.say;
    }

    setMessages([...history, { id: "a" + Date.now(), role: "assistant", content: "", pending: true }]);
    busyRef.current = true;
    setBusy(true);
    abort.current = new AbortController();
    let reply = "";
    /* Speak the first sentence while the rest is still being written.
       speechqueue.js was written to do exactly this and was never wired to
       anything — its own header says "Chat.jsx feeds it unconditionally",
       and Chat.jsx did not import it. So the chef sat in silence for the
       whole reply and only then opened its mouth, which on a 15-token-a-second
       model is most of a minute of standing over a pan wondering. */
    const spoken = k.prefs.speakReplies ? sq.arm({ useServer: server.voice }) : null;
    try {
      await api.chat({
        messages: history.filter(m => m.id !== "hello").slice(-24).map(({ role, content }) => ({ role, content })),
        context: context(),
        custom: k.customs
      }, (ev) => {
        if (ev.type === "delta") {
          reply += ev.text; sq.feed(ev.text); pending.current = reply;
          if (!frame.current) frame.current = requestAnimationFrame(flush);
        }
        else if (ev.type === "action") {
          const receipt = act.current ? act.current(ev.action) : null;
          if (receipt) patchLast(m => ({ actions: [...(m.actions || []), receipt] }));
        }
        else if (ev.type === "attachment") patchLast(() => ({ attachment: ev.recipe, method: ev.method }));
        else if (ev.type === "status") patchLast(() => ({ status: ev.text }));
        else if (ev.type === "error") patchLast(() => ({ error: ev.message }));
      }, abort.current.signal);
    } catch (e) {
      if (e.name !== "AbortError") patchLast(() => ({ error: e.message }));
    } finally {
      if (frame.current) { cancelAnimationFrame(frame.current); flush(); }
      patchLast(() => ({ pending: false }));
      busyRef.current = false;
      setBusy(false);
    }
    /* Wait for the speaking to FINISH, so a hands-free loop knows when it is
       its turn to listen again. The queue has been reading it out sentence by
       sentence since the first full stop, so there is nothing left to say —
       only to wait for. Saying it again here is what the echo was. */
    if (spoken) {
      sq.end();
      try { await spoken; } catch { /* ignore */ }
    } else if (reply && k.prefs.speakReplies) {
      try { await voice.speak(reply, server.voice); } catch { /* ignore */ }
    }
    return reply;
  }, [context, k.customs, k.stock, k.prefs.speakReplies, server.voice]);

  const stop = () => { abort.current?.abort(); sq.cancel(); voice.stopSpeaking(); };
  return { messages, send, busy, stop };
}

export function ChatThread({ messages, onSaveRecipe, onCookRecipe }) {
  const end = useRef(null);
  const last = messages[messages.length - 1];
  useEffect(() => { end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); },
    [messages.length, last?.content?.length, last?.actions?.length]);

  return (
    <div className="thread" aria-live="polite">
      {messages.map(m => (
        <div key={m.id} className={"bubble-row is-" + m.role}>
          {m.role === "assistant" && <span className="bubble-avatar"><Icon name="chef" size={22} /></span>}
          <div className={"bubble" + (m.error ? " is-error" : "")}>
            {m.status && <p className="bubble-status">{m.status}</p>}
            {m.attachment && <LinkCard recipe={m.attachment} method={m.method}
                                       onSave={onSaveRecipe} onCook={onCookRecipe} />}
            {m.content
              ? m.content.split(/\n{2,}/).map((p, i) => <p key={i}>{p}</p>)
              : m.pending && !m.status && !(m.actions || []).length && <span className="typing" aria-label="Writing"><i /><i /><i /></span>}
            {(m.actions || []).map((a, i) => (
              <div key={i} className="action-receipt">
                <span className="action-tick" aria-hidden>✓</span><span>{a.text}</span>
                {a.recipe && (
                  <button className="btn btn-small btn-ghost" onClick={() => onCookRecipe(a.recipe)}>Cook it</button>
                )}
              </div>
            ))}
            {m.error && <p className="bubble-err">{m.error}</p>}
          </div>
        </div>
      ))}
      <div ref={end} />
    </div>
  );
}

function LinkCard({ recipe, method, onSave, onCook }) {
  const [saved, setSaved] = useState(false);
  const tracked = recipe.needs.length;
  return (
    <div className="linkcard">
      {recipe.remotePhoto && <img className="linkcard-img" src={recipe.remotePhoto} alt="" loading="lazy" decoding="async" />}
      <div className="linkcard-body">
        <p className="linkcard-site">From {recipe.source?.site}</p>
        <p className="linkcard-name">{recipe.name}</p>
        <p className="linkcard-meta">
          {recipe.minutes} min, {recipe.steps.length} steps. {tracked} ingredient{tracked === 1 ? "" : "s"} matched to your kitchen
          {recipe.extras?.length ? `, ${recipe.extras.length} kept as written` : ""}.
          {method === "plain" && " Read as written — no heat or cues added."}
        </p>
        <div className="linkcard-actions">
          <button className="btn btn-small btn-primary" onClick={() => onCook(recipe)}>Cook this</button>
          <button className="btn btn-small btn-ghost" disabled={saved}
                  onClick={() => { onSave(recipe); setSaved(true); }}>{saved ? "Saved" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}

export function Composer({ onSend, busy, onStop, placeholder = "Ask anything", suggestions = [], disabled }) {
  const { server, say } = useUI();
  const [text, setText] = useState("");
  const [listening, setListening] = useState(null);
  const [consent, setConsent] = useState(false);

  const submit = (t = text) => { if (t.trim()) { onSend(t); setText(""); } };

  const startListening = () => {
    setConsent(false);
    voiceAgreed = true;
    const ctl = voice.listen({
      useServer: server.voice,
      onText: (t) => submit(t),
      onError: (msg) => say(msg),
      onEnd: () => setListening(null)
    });
    setListening(ctl);
  };

  const mic = () => {
    if (listening) { listening.stop(); return; }
    const state = voice.listenState(server.voice);
    if (state !== "ok") { say(voice.LISTEN_REASON[state]); return; }
    if (!voiceAgreed) { setConsent(true); return; }
    startListening();
  };

  return (
    <div className="composer">
      {consent && (
        <div className="consent">
          <p>{server.voice
            ? "Your voice is sent to the server's speech service to be written down, then discarded."
            : "Your browser's speech recognition sends the audio to its maker's servers. Everything else stays on this device."}</p>
          <div className="consent-actions">
            <button className="btn btn-small btn-primary" onClick={startListening}>Use the microphone</button>
            <button className="btn btn-small btn-ghost" onClick={() => setConsent(false)}>I'll type</button>
          </div>
        </div>
      )}
      {suggestions.length > 0 && !busy && (
        <div className="chips chips-scroll suggest">
          {suggestions.map(s => (
            <button key={s} className="chip chip-sm" disabled={disabled} onClick={() => submit(s)}>{s}</button>
          ))}
        </div>
      )}
      <div className="composer-row">
        <button className={"icon-btn icon-btn-lg" + (listening ? " is-live" : "")} onClick={mic}
                disabled={disabled} aria-label={listening ? "Stop listening" : "Speak"}>
          <Icon name={listening ? "stop" : "mic"} />
        </button>
        <input className="composer-input" value={text} disabled={disabled}
               placeholder={listening ? "Listening…" : placeholder} enterKeyHint="send"
               onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && submit()} />
        {busy
          ? <button className="icon-btn icon-btn-lg is-mint" onClick={onStop} aria-label="Stop"><Icon name="stop" /></button>
          : <button className="icon-btn icon-btn-lg is-mint" onClick={() => submit()} disabled={disabled || !text.trim()}
                    aria-label="Send"><Icon name="send" /></button>}
      </div>
    </div>
  );
}

/* Shared by both chat surfaces: turn a found or discussed recipe into one
   of yours, keeping only ingredient ids this kitchen knows. */
export function knownOnly(recipe) {
  return { ...recipe, needs: recipe.needs.filter(n => ref(n.id).name) };
}
