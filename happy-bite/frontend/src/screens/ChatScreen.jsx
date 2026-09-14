/* The chef, full screen. Type, or turn on hands-free conversation: it listens,
   the chef answers out loud (local Kokoro voice), then it listens again — no
   buttons between turns. Tell it what you used, ask it to cook, or ask it to
   move around the app; it does. */

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../lib/api.js";
import * as voice from "../lib/voice.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { useApplyAction } from "../state/actions.jsx";
import { Icon } from "../components/Icon.jsx";
import { useChat, ChatThread, Composer } from "../components/Chat.jsx";
import "../styles/chat-actions.css";

const GREETING = "What are we cooking? Tell me what you fancy, ask about a technique, or paste a link. Turn on the mic and just talk — say what you used, ask me to cook something, or tell me to show you a screen.";

const STATE_LABEL = { listening: "Listening…", thinking: "Thinking…", speaking: "Speaking…" };

export default function ChatScreen() {
  const { k, saveRecipe, setPref } = useKitchen();
  const ui = useUI();
  const applyAction = useApplyAction();
  const [drafting, setDrafting] = useState(false);
  const [convo, setConvo] = useState(false);
  const [vState, setVState] = useState("");           // listening | thinking | speaking | ""
  const serves = k.diners.length || 2;
  const context = useCallback(() => ({ mode: "general", serves, stock: stockForServer(k.stock) }), [k.stock, serves]);
  const chat = useChat(context, GREETING, applyAction);
  const sendRef = useRef(chat.send); sendRef.current = chat.send;
  const convoRef = useRef(false);
  const listenRef = useRef(null);

  useEffect(() => { if (ui.chat?.seed && ui.server.chat) chat.send(ui.chat.seed); }, []); // eslint-disable-line

  /* One turn of the hands-free loop: listen -> send (which speaks the reply) -> repeat. */
  const turn = useCallback(() => {
    if (!convoRef.current) return;
    setVState("listening");
    listenRef.current = voice.listenVAD({
      onText: async (text) => {
        listenRef.current = null;
        if (!convoRef.current) return;
        const said = (text || "").trim();
        if (!said) { turn(); return; }                 // heard nothing — keep listening
        setVState("thinking");
        await sendRef.current(said);                    // streams the reply, then speaks it (Kokoro)
        if (convoRef.current) turn();                   // your turn again
      },
      onError: (m) => { ui.say(m); if (convoRef.current) setTimeout(turn, 800); },
    });
  }, [ui]);

  const startConvo = () => {
    if (!ui.server.voice) {
      ui.say("Turn on the local voice first (Whisper + Kokoro) — see the setup notes.");
      return;
    }
    setPref("speakReplies", true);
    convoRef.current = true;
    setConvo(true);
    turn();
  };

  const stopConvo = useCallback(() => {
    convoRef.current = false;
    setConvo(false);
    setVState("");
    try { listenRef.current?.stop(); } catch { /* ignore */ }
    listenRef.current = null;
    voice.stopSpeaking();
  }, []);

  useEffect(() => () => stopConvo(), [stopConvo]);      // stop when the chat closes

  const userTurns = chat.messages.filter(m => m.role === "user").length;

  const draft = async () => {
    setDrafting(true);
    try {
      const { recipe } = await api.generateRecipe({
        options: false,
        conversation: chat.messages.filter(m => m.id !== "hello" && m.content).map(({ role, content }) => ({ role, content })),
        stock: stockForServer(k.stock), serves, custom: k.customs
      });
      ui.closeChat();
      ui.openSheet("draft", { draft: recipe });
    } catch (e) { ui.say(e.message); }
    setDrafting(false);
  };

  return (
    <div className="chatscreen" role="dialog" aria-label="The chef">
      <header className="cook-head">
        <button className="icon-btn" onClick={ui.closeChat} aria-label="Close"><Icon name="close" /></button>
        <div className="cook-title">
          <p className="cook-name">The chef</p>
          <p className="chat-model">{ui.server.chat ? (ui.server.local ? "Running on this network" : "Online assistant") : "Off"}</p>
        </div>
        <button className={"icon-btn icon-btn-lg" + (convo ? " is-live" : "")} aria-pressed={convo}
                onClick={() => convo ? stopConvo() : startConvo()}
                aria-label={convo ? "Stop talking" : "Start talking"}>
          <Icon name={convo ? "stop" : "mic"} />
        </button>
        <button className={"icon-btn" + (k.prefs.speakReplies ? " is-on" : "")} aria-pressed={k.prefs.speakReplies}
                onClick={() => setPref("speakReplies", !k.prefs.speakReplies)}
                aria-label={k.prefs.speakReplies ? "Stop reading replies aloud" : "Read replies aloud"}>
          <Icon name={k.prefs.speakReplies ? "speaker" : "mute"} />
        </button>
      </header>

      {ui.server.chat ? (
        <div className="chat-body">
          {convo && (
            <p className="sub-note voice-status" aria-live="polite">
              <span className={"voice-dot is-" + (vState || "idle")} /> {STATE_LABEL[vState] || "Hands-free on — just talk. Tap the square to stop."}
            </p>
          )}
          <ChatThread messages={chat.messages}
                      onSaveRecipe={(r) => { saveRecipe(r); ui.say("Saved to your recipes"); }}
                      onCookRecipe={(r) => { saveRecipe(r); ui.closeChat(); ui.startCooking(r, serves); }} />
          {userTurns >= 2 && !chat.busy && (
            <button className="btn btn-small btn-ghost draft-btn" onClick={draft} disabled={drafting}>
              <Icon name="spark" size={20} /> {drafting ? "Writing the recipe…" : "Turn this into a recipe"}
            </button>
          )}
          <Composer onSend={chat.send} busy={chat.busy} onStop={chat.stop}
                    placeholder="Ask, paste a link, or tap the mic and talk"
                    suggestions={userTurns ? [] : ["What can I make with what's going off?", "I used 2 eggs and 100 ml of milk", "Show me the shopping list"]} />
        </div>
      ) : (
        <div className="offline-note is-page">
          <Icon name="chef" size={48} />
          <p><b>The chef needs the server.</b> Start it with a key in <code>backend/.env</code> — or point it at a model on this network.</p>
          <p>Tonight's dish, the kitchen, receipts and shopping all work without it.</p>
          <button className="btn btn-ghost" onClick={ui.refreshServer}>Check again</button>
        </div>
      )}
    </div>
  );
}
