/* Happy Bite — the global voice agent.

   Lives on every screen (mounted once in App). Tap the floating mic, or say the
   wake word, and it holds a hands-free conversation: it listens (local Whisper),
   the chef answers out loud (local Kokoro) and can act — adjust stock, set a
   timer, save a recipe, or move you to another screen — then it listens again.

   It owns the microphone app-wide, so it hides itself while the full chef chat
   or cooking mode is open (those screens have their own voice), avoiding two
   recognisers fighting over the mic. Requires local voice (SPEECH_PROVIDER=local)
   for hearing; without it, the button explains what to switch on. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as voice from "../lib/voice.js";
import * as wake from "../lib/wake.js";
import { parse as parseCommand } from "../lib/command.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { useApplyAction } from "../state/actions.jsx";
import { useChat } from "./Chat.jsx";
import { Icon } from "./Icon.jsx";

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
  const uiRef = useRef(ui); uiRef.current = ui;

  const [active, setActive] = useState(false);
  const [vstate, setVstate] = useState("");            // listening | thinking | speaking
  const [lastSaid, setLastSaid] = useState("");        // shown in the card for instant replies
  const activeRef = useRef(false);
  const listenRef = useRef(null);
  const wakeRef = useRef(null);

  // Full-screen chat and full-screen cooking own the mic themselves. But when
  // cooking is MINIMISED the agent stays live, so "go back to the cooking
  // recipe" still works.
  const hidden = !!(ui.chat || (ui.cooking && !ui.cookMin)) || !ui.server.chat;

  const stopWake = () => { try { wakeRef.current?.stop(); } catch { /* */ } wakeRef.current = null; };

  const oneTurn = useCallback(() => {
    if (!activeRef.current) return;
    setVstate("listening");
    listenRef.current = voice.listenVAD({
      onText: async (t) => {
        listenRef.current = null;
        if (!activeRef.current) return;
        const said = (t || "").trim();
        if (!said) { oneTurn(); return; }
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
            try { await voice.speak(cmd.say, voiceServerRef.current); } catch { /* */ }
          }
          if (activeRef.current) oneTurn();
          return;
        }
        setVstate("thinking");
        setLastSaid("");                                // fall back to the chef's own reply
        await sendRef.current(said);                    // streams reply, then Kokoro speaks it
        if (activeRef.current) oneTurn();
      },
      onError: () => { if (activeRef.current) setTimeout(oneTurn, 800); },
    });
  }, []);

  const start = useCallback(() => {
    if (!ui.server.voice) { ui.say("Turn on the local voice (Whisper + Kokoro) to talk hands-free."); return; }
    stopWake();
    activeRef.current = true;
    setActive(true);
    oneTurn();
  }, [ui, oneTurn]);

  const stop = useCallback(() => {
    activeRef.current = false;
    setActive(false);
    setVstate("");
    try { listenRef.current?.stop(); } catch { /* */ }
    listenRef.current = null;
    voice.stopSpeaking();
  }, []);

  const startRef = useRef(start); startRef.current = start;

  /* Best-effort wake word while idle (armed only when the agent is visible and
     local voice is on). The browser recogniser can be flaky, so the tap button
     is always the reliable way in. */
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
  const shown = lastSaid || lastChef?.content || "";
  const label = { listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" }[vstate];

  return (
    <div className="agent">
      {active && (
        <div className="agent-card" role="status" aria-live="polite">
          <span className={"agent-state is-" + (vstate || "idle")}>{label || "Go ahead…"}</span>
          {shown && <p className="agent-said">{shown.slice(0, 160)}</p>}
        </div>
      )}
      <button className={"agent-fab" + (active ? " is-live" : "")}
              onClick={() => (active ? stop() : start())}
              aria-pressed={active}
              aria-label={active ? "Stop talking to the chef" : "Talk to the chef"}
              title={ui.server.wakeWord ? `Tap, or say "${ui.server.wakeWord}"` : "Talk to the chef"}>
        <Icon name={active ? "stop" : "mic"} size={26} />
      </button>
    </div>
  );
}
