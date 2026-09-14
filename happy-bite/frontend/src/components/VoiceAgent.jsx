/* Happy Bite — the global voice agent.

   Lives on every screen (mounted once in App). Tap the floating mic, or say the
   wake word, and it holds a hands-free conversation: it listens, the chef
   answers out loud and can act — adjust stock, set a timer, save a recipe, or
   move you to another screen — then it listens again.

   It owns the microphone app-wide, so it hides itself while the full chef chat
   or cooking mode is open (those screens have their own voice), avoiding two
   recognisers fighting over the mic.

   TWO THINGS CHANGED HERE, both of which made it silent:

   1. It never spoke the chef's replies. `useChat` only speaks when
      `prefs.speakReplies` is on, and that pref is false by default and is only
      ever set by ChatScreen and CookMode. This file's old comment claimed
      "streams reply, then Kokoro speaks it" — it didn't. It now speaks the
      reply itself when the pref is off, and stays out of the way when it's on,
      so the reply is never read out twice.

   2. It refused to start at all without the local models. The browser has its
      own recogniser and its own voice; they are worse, and Chrome's recogniser
      sends audio to Google, so local is still the right default — but "worse"
      beats "nothing". Without Whisper and Kokoro it now falls back and says so
      once, rather than showing a toast and doing nothing. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as voice from "../lib/voice.js";
import * as wake from "../lib/wake.js";
import { parse as parseCommand } from "../lib/command.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { useApplyAction } from "../state/actions.jsx";
import { useChat } from "./Chat.jsx";
import { Icon } from "./Icon.jsx";

/* One turn on the browser's own recogniser — the fallback when the server has
   no Whisper. It ends itself when you stop talking, which is what makes a loop
   possible without voice-activity detection of our own. Kept here rather than
   in lib/voice.js so this is a single drop-in file. */
function browserTurn({ onText, onError, onStart }) {
  const Rec = typeof window !== "undefined" &&
              (window.SpeechRecognition || window.webkitSpeechRecognition);
  if (!Rec) {
    onError?.("This browser can't listen. Chrome, Edge and Safari can.");
    return { stop() {} };
  }
  const rec = new Rec();
  rec.lang = navigator.language || "en-GB";
  rec.interimResults = false;
  rec.continuous = false;

  let done = false;
  const finish = (text) => { if (!done) { done = true; onText?.(text); } };

  rec.onstart = () => onStart?.();
  rec.onresult = (e) => finish(e.results[0][0].transcript || "");
  rec.onerror = (e) => {
    // Silence and a cancelled turn are not errors — they're an empty turn, and
    // the loop should just listen again.
    if (e.error === "no-speech" || e.error === "aborted") return finish("");
    done = true;
    onError?.(e.error === "not-allowed"
      ? "Microphone access was refused. Check the site's permissions."
      : "Listening failed (" + e.error + ").");
  };
  rec.onend = () => finish("");

  try { rec.start(); } catch { done = true; onError?.("Listening wouldn't start."); }
  return { stop() { done = true; try { rec.stop(); } catch { /* already stopped */ } } };
}

const browserCanListen = () =>
  typeof window !== "undefined" &&
  !!(window.SpeechRecognition || window.webkitSpeechRecognition) &&
  window.isSecureContext;

export default function VoiceAgent() {
  const { k } = useKitchen();
  const ui = useUI();
  const applyAction = useApplyAction();
  const serves = (k.diners && k.diners.length) || 2;
  const context = useCallback(
    () => ({ mode: "general", serves, stock: stockForServer(k.stock || []) }),
    [k.stock, serves]
  );
  const chat = useChat(context, null, applyAction);
  const sendRef = useRef(chat.send); sendRef.current = chat.send;
  const stockRef = useRef(k.stock); stockRef.current = k.stock;
  const bookRef = useRef(k.book); bookRef.current = k.book;
  const applyRef = useRef(applyAction); applyRef.current = applyAction;
  const voiceServerRef = useRef(ui.server.voice); voiceServerRef.current = ui.server.voice;
  const speaksAlreadyRef = useRef(false); speaksAlreadyRef.current = !!k.prefs?.speakReplies;
  const uiRef = useRef(ui); uiRef.current = ui;

  const [active, setActive] = useState(false);
  const [vstate, setVstate] = useState("");            // listening | thinking | speaking
  const [lastSaid, setLastSaid] = useState("");        // shown in the card for instant replies
  const activeRef = useRef(false);
  const listenRef = useRef(null);
  const wakeRef = useRef(null);
  const warnedRef = useRef(false);                     // the "browser voice" note, said once

  // Local models when we have them, the browser when we don't.
  const canListen = ui.server.voice || browserCanListen();

  // Full-screen chat and full-screen cooking own the mic themselves. But when
  // cooking is MINIMISED the agent stays live, so "go back to the cooking
  // recipe" still works.
  const hidden = !!(ui.chat || (ui.cooking && !ui.cookMin));
  const canListenRef = useRef(canListen); canListenRef.current = canListen;

  const stopWake = () => { try { wakeRef.current?.stop(); } catch { /* */ } wakeRef.current = null; };

  const oneTurn = useCallback(() => {
    if (!activeRef.current) return;
    const onServer = voiceServerRef.current;
    setVstate("listening");

    const again = (pause) => {
      if (!activeRef.current) return;
      if (pause) setTimeout(() => activeRef.current && oneTurn(), pause);
      else oneTurn();
    };

    const handle = async (t) => {
      listenRef.current = null;
      if (!activeRef.current) return;
      const said = (t || "").trim();
      // An empty browser turn comes back instantly; pausing keeps it from
      // spinning the recogniser in a tight loop.
      if (!said) return again(onServer ? 0 : 500);

      // Instant, no-model path for the things people say most: navigation and
      // reading the kitchen out loud. Answers immediately and reliably; only
      // real questions fall through to the chef model below.
      const openId = uiRef.current.sheet?.type === "recipe" ? uiRef.current.sheet.props.id : null;
      const cmd = parseCommand(said, {
        stock: stockRef.current,
        recipes: (bookRef.current || []).map(r => ({ id: r.id, name: r.name })),
        openRecipeId: openId,
      });
      if (cmd) {
        setLastSaid(cmd.say || "");
        if (cmd.resumeCooking) uiRef.current.resumeCooking();
        else if (cmd.action) applyRef.current(cmd.action);
        if (cmd.say) {
          setVstate("speaking");
          try { await voice.speak(cmd.say, onServer); } catch { /* */ }
        }
        return again(0);
      }

      setVstate("thinking");
      setLastSaid("");                                  // fall back to the chef's own reply
      if (!uiRef.current.server.chat) {
        uiRef.current.say("The chef server is offline. Start it, then try again.");
        return again(500);
      }
      const reply = await sendRef.current(said);

      /* Say it. `useChat` only does this when prefs.speakReplies is on, which it
         isn't unless another screen turned it on — so without this the agent
         listened, thought, answered on screen, and said nothing at all. When
         the pref IS on, useChat has already spoken and we keep quiet. */
      if (reply && !speaksAlreadyRef.current) {
        setVstate("speaking");
        // Browser speech starts much sooner than waiting for a complete local
        // Kokoro render. Server voice remains available in full chat/cook mode.
        try { await voice.speak(reply, false); } catch { /* */ }
      }
      again(0);
    };

    const onError = (msg) => {
      if (!activeRef.current) return;
      if (msg) uiRef.current.say(msg);
      setTimeout(() => activeRef.current && oneTurn(), 800);
    };

    listenRef.current = onServer
      ? voice.listenVAD({ onText: handle, onError: () => onError(null) })
      : browserTurn({ onText: handle, onError });
  }, []);

  const start = useCallback(() => {
    if (!canListenRef.current) {
      uiRef.current.say(window.isSecureContext === false
        ? "Listening needs a secure connection — use localhost, or serve the app over https."
        : "This browser can't listen, and the local voice is off. Chrome, Edge and Safari can listen.");
      return;
    }
    /* No local models: still works, just not as well. Said once per session so
       it's information rather than nagging. */
    if (!ui.server.voice && !warnedRef.current) {
      warnedRef.current = true;
      uiRef.current.say("Using the browser's voice — install Whisper and Kokoro for the good one.");
    }
    stopWake();
    activeRef.current = true;
    setActive(true);
    oneTurn();
  }, [oneTurn]);

  const stop = useCallback(() => {
    activeRef.current = false;
    setActive(false);
    setVstate("");
    try { listenRef.current?.stop(); } catch { /* */ }
    listenRef.current = null;
    voice.stopSpeaking();
  }, []);

  const startRef = useRef(start); startRef.current = start;

  /* Best-effort wake word while idle. The browser recogniser can be flaky, so
     the tap button is always the reliable way in. Not armed when the agent is
     already listening through that same recogniser — one at a time. */
  useEffect(() => {
    if (hidden || active || !ui.server.voice) { stopWake(); return; }
    if (wake.wakeSupported() !== "ok") return;
    wakeRef.current = wake.startWakeWord({
      phrase: ui.server.wakeWord || "hey chef",
      onWake: () => startRef.current(),
      onError: () => {},                                 // stay quiet; the button still works
    });
    return () => stopWake();
  }, [hidden, active, ui.server.voice, ui.server.wakeWord]);

  /* If the chat/cook screen opens or the app leaves, drop any live turn. */
  useEffect(() => { if (hidden && activeRef.current) stop(); }, [hidden, stop]);
  useEffect(() => () => stop(), [stop]);

  if (hidden) return null;

  const lastChef = [...chat.messages].reverse().find(m => m.role === "assistant" && m.content);
  const label = { listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" }[vstate];

  return (
    <div className="agent">
      <button className={"agent-fab " + (active ? "is-live is-" + (vstate || "idle") : "")}
              onClick={() => (active ? stop() : start())}
              aria-pressed={active}
              aria-label={active ? "Stop talking to the chef" : "Talk to the chef"}
              title={label || (ui.server.wakeWord ? `Tap, or say "${ui.server.wakeWord}"` : "Talk to the chef")}>
        <Icon name={active ? "stop" : "mic"} size={26} />
      </button>
    </div>
  );
}