/* Happy Bite — the chef you can talk to.

   One conversation engine, two places: the full-screen chat from the round
   button, and the panel beside the steps in cooking mode. Replies stream
   in as they're written; a pasted recipe link is read by the server and
   comes back as a card you can save or start cooking. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api.js";
import * as voice from "../lib/voice.js";
import { Icon } from "./Icon.jsx";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { ref } from "../core/engine.js";

let voiceAgreed = false;                    // asked once per session, never stored

export function useChat(context, greeting) {
  const { k } = useKitchen();
  const [messages, setMessages] = useState(() => greeting ? [{ id: "hello", role: "assistant", content: greeting }] : []);
  const [busy, setBusy] = useState(false);
  const abort = useRef(null);
  const live = useRef(messages);
  live.current = messages;

  const patchLast = (fn) => setMessages(ms => ms.map((m, i) => i === ms.length - 1 ? { ...m, ...fn(m) } : m));

  const send = useCallback(async (text) => {
    const clean = text.trim();
    if (!clean || busy) return;
    const history = [...live.current, { id: "u" + Date.now(), role: "user", content: clean }];
    setMessages([...history, { id: "a" + Date.now(), role: "assistant", content: "", pending: true }]);
    setBusy(true);
    abort.current = new AbortController();
    let reply = "";
    try {
      await api.chat({
        messages: history.filter(m => m.id !== "hello").slice(-24).map(({ role, content }) => ({ role, content })),
        context: context(),
        custom: k.customs
      }, (ev) => {
        if (ev.type === "delta") { reply += ev.text; patchLast(() => ({ content: reply, status: null })); }
        else if (ev.type === "attachment") patchLast(() => ({ attachment: ev.recipe, method: ev.method }));
        else if (ev.type === "status") patchLast(() => ({ status: ev.text }));
        else if (ev.type === "error") patchLast(() => ({ error: ev.message }));
      }, abort.current.signal);
      if (reply && k.prefs.speakReplies) voice.speak(reply, false);
    } catch (e) {
      if (e.name !== "AbortError") patchLast(() => ({ error: e.message }));
    } finally {
      patchLast(() => ({ pending: false }));
      setBusy(false);
    }
  }, [busy, context, k.customs, k.prefs.speakReplies]);

  const stop = () => abort.current?.abort();
  return { messages, send, busy, stop };
}

export function ChatThread({ messages, onSaveRecipe, onCookRecipe }) {
  const end = useRef(null);
  const last = messages[messages.length - 1];
  useEffect(() => { end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); },
    [messages.length, last?.content?.length]);

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
              : m.pending && !m.status && <span className="typing" aria-label="Writing"><i /><i /><i /></span>}
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
      {recipe.remotePhoto && <img className="linkcard-img" src={recipe.remotePhoto} alt="" />}
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
