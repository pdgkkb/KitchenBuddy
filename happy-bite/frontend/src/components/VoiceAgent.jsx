/* Happy Bite — the global voice agent.

   Lives on every screen (mounted once in App). Tap the floating mic, or say the
   wake word, and it holds a hands-free conversation: it listens, the chef
   answers out loud and can act — adjust stock, set a timer, save a recipe, or
   move you to another screen — then it listens again.

   It owns the microphone app-wide, so it hides itself while the full chef chat
   or cooking mode is open (those screens have their own voice), avoiding two
   recognisers fighting over the mic.

   THE WAKE WORD, AFTER FOUR ATTEMPTS AT IT:

   1. It used to refuse to arm without the local voice server. Nothing in
      wake.js talks to a server — it is the browser's own recogniser from start
      to finish — so that test demanded Whisper in order to use the one part of
      the stack that cannot use Whisper.
   2. Then it was a switch buried in a settings sheet, off by default, which is
      indistinguishable from broken if you never find the switch. It is now a
      LONG PRESS on the mic button, right where your thumb already is, and the
      settings toggle stays for people who prefer it.
   3. And it was silent about every way it can fail. Now its state is on the
      screen, under the button: armed, or the sentence saying why not.

   4. On by default now. Saying "Bob" is the whole point of a kitchen app you
      use with your hands full, and a feature you have to find a long press
      for is one most people never meet. It still holds the microphone open,
      and in Chrome the browser streams what it hears to Google to recognise
      it — the settings sheet says so, and a long press or that sheet turns it
      off. Only an explicit "off" counts as off: a household whose saved
      preferences predate this switch gets it on.

   "Bob, I'm drained. Give me 15 minutes." in one breath goes straight through
   as the request. "Bob" on its own chimes and listens for the next sentence. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as voice from "../lib/voice.js";
import * as wake from "../lib/wake.js";
import { parse as parseCommand } from "../lib/command.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { useApplyAction } from "../state/actions.jsx";
import { useChat } from "./Chat.jsx";
import { Icon } from "./Icon.jsx";
import "../styles/agent.css";

/* One turn on the browser's own recogniser — the fallback when the server has
   no Whisper. It ends itself when you stop talking, which is what makes a loop
   possible without voice-activity detection of our own. */
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
  const { k, setPref } = useKitchen();
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
  const [lastSaid, setLastSaid] = useState("");
  const [wakeState, setWakeState] = useState("off");   // off | starting | armed | error
  const [wakeWhy, setWakeWhy] = useState("");
  const activeRef = useRef(false);
  const listenRef = useRef(null);
  const wakeRef = useRef(null);
  const warnedRef = useRef(false);
  const pressRef = useRef(null);                       // long-press timer
  const heldRef = useRef(false);

  const handsFree = k.prefs?.handsFree !== false;
  const canListen = ui.server.voice || browserCanListen();

  /* Writing a recipe is not a conversation. While the create or link panel is
     up the person is watching a list of stages, and the recogniser is not free:
     it holds the microphone, runs an AudioContext and a per-frame level meter,
     and on the server path posts audio to Whisper every few seconds — all of it
     competing with the model on a machine already at its limit. */
  const working = ui.sheet?.type === "create" || ui.sheet?.type === "link";
  const hidden = !!(ui.chat || working || (ui.cooking && !ui.cookMin));
  const canListenRef = useRef(canListen); canListenRef.current = canListen;

  const stopWake = () => { try { wakeRef.current?.stop(); } catch { /* */ } wakeRef.current = null; };

  /* `preset` is something already heard — the words said straight after the
     wake word — handled as if this turn had just recognised it. */
  const oneTurn = useCallback((preset) => {
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
      if (!said) return again(onServer ? 0 : 500);

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
      setLastSaid("");
      if (!uiRef.current.server.chat) {
        uiRef.current.say("The chef server is offline. Start it, then try again.");
        return again(500);
      }
      const reply = await sendRef.current(said);
      if (reply && !speaksAlreadyRef.current) {
        setVstate("speaking");
        try { await voice.speak(reply, false); } catch { /* */ }
      }
      again(0);
    };

    const onError = (msg) => {
      if (!activeRef.current) return;
      if (msg) uiRef.current.say(msg);
      setTimeout(() => activeRef.current && oneTurn(), 800);
    };

    if (preset) { handle(preset); return; }

    listenRef.current = onServer
      ? voice.listenVAD({ onText: handle, onError: () => onError(null) })
      : browserTurn({ onText: handle, onError });
  }, []);

  const start = useCallback((said = "") => {
    if (!canListenRef.current) {
      uiRef.current.say(window.isSecureContext === false
        ? "Listening needs a secure connection — use localhost, or serve the app over https."
        : "This browser can't listen, and the local voice is off. Chrome, Edge and Safari can listen.");
      return;
    }
    if (!uiRef.current.server.voice && !warnedRef.current) {
      warnedRef.current = true;
      uiRef.current.say("Using the browser's voice — install Whisper and Kokoro for the good one.");
    }
    stopWake();
    activeRef.current = true;
    setActive(true);
    oneTurn(typeof said === "string" ? said.trim() : "");
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

  /* The wake word, armed by a setting and by nothing else — no server, no
     models. Not armed while the agent is already listening through that same
     recogniser: one at a time. */
  useEffect(() => {
    if (hidden || active || !handsFree) {
      stopWake();
      setWakeState("off");
      setWakeWhy("");
      return;
    }
    setWakeState("starting");
    setWakeWhy("");
    wakeRef.current = wake.startWakeWord({
      phrase: ui.server.wakeWord || wake.DEFAULT_WAKE,
      // A bare "Bob" gets the chime, so you know it's listening before you talk.
      onWake: async (rest) => { if (!rest) await voice.chime(); startRef.current(rest); },
      onStatus: (state, detail) => { setWakeState(state); if (detail) setWakeWhy(detail); },
      onHeard: (text, matched) => { if (!matched) setLastSaid(""); void text; },
      onError: (msg) => { if (msg) uiRef.current.say(msg); },
    });
    return () => stopWake();
  }, [hidden, active, handsFree, ui.server.wakeWord]);

  useEffect(() => { if (hidden && activeRef.current) stop(); }, [hidden, stop]);
  useEffect(() => () => stop(), [stop]);

  if (hidden) return null;

  const label = { listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" }[vstate];

  /* Long press = hands-free on or off. The switch used to live only in a
     settings sheet, which for a feature people describe as "not working" is
     the same as not existing. */
  const hold = () => {
    heldRef.current = false;
    pressRef.current = setTimeout(() => {
      heldRef.current = true;
      const next = !handsFree;
      setPref("handsFree", next);
      uiRef.current.say(next
        ? `Hands-free on — say "${wake.wakeLabel(ui.server.wakeWord)}". Chrome sends what it hears to Google while this is on.`
        : "Hands-free off.");
    }, 550);
  };
  const release = () => { clearTimeout(pressRef.current); };
  const tap = () => {
    if (heldRef.current) { heldRef.current = false; return; }   // that was the long press
    active ? stop() : start();   // a click event is not something that was said
  };

  const hint = active ? label
    : !handsFree ? null
    : wakeState === "armed" || wakeState === "starting" ? `Say “${wake.wakeLabel(ui.server.wakeWord)}”`
    : wakeState === "error" ? wakeWhy
    : null;

  return (
    <div className="agent">
      {hint && (
        <p className={"agent-hint" + (wakeState === "error" && !active ? " is-bad" : "")}
           role="status">{hint}</p>
      )}
      <button className={"agent-fab " + (active ? "is-live is-" + (vstate || "idle") : "")
                         + (!active && handsFree && wakeState === "armed" ? " is-armed" : "")}
              onClick={tap}
              onPointerDown={hold} onPointerUp={release} onPointerLeave={release}
              onContextMenu={(e) => e.preventDefault()}
              aria-pressed={active}
              aria-label={active ? "Stop talking to the chef" : "Talk to the chef. Press and hold to switch hands-free on or off."}
              title={active ? "Stop" : "Tap to talk. Hold to turn the wake word on or off."}>
        <Icon name={active ? "stop" : "mic"} size={26} />
      </button>
    </div>
  );
}
