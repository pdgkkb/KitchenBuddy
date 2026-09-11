/* The chef, full screen. Paste a link from any cooking site and it reads
   it; talk a dish through and it turns the conversation into a recipe. */

import { useCallback, useEffect, useState } from "react";
import * as api from "../lib/api.js";
import { useKitchen, stockForServer } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "../components/Icon.jsx";
import { useChat, ChatThread, Composer } from "../components/Chat.jsx";

const GREETING = "What are we cooking? Tell me what you fancy, ask about a technique, or paste a link to a recipe you found — I'll read it and help you make it with what you've got.";

export default function ChatScreen() {
  const { k, saveRecipe, setPref } = useKitchen();
  const ui = useUI();
  const [drafting, setDrafting] = useState(false);
  const serves = k.diners.length || 2;
  const context = useCallback(() => ({ mode: "general", serves, stock: stockForServer(k.stock) }), [k.stock, serves]);
  const chat = useChat(context, GREETING);

  useEffect(() => { if (ui.chat?.seed && ui.server.chat) chat.send(ui.chat.seed); }, []); // eslint-disable-line

  const userTurns = chat.messages.filter(m => m.role === "user").length;

  const draft = async () => {
    setDrafting(true);
    try {
      const { recipe } = await api.generateRecipe({
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
        <button className={"icon-btn" + (k.prefs.speakReplies ? " is-on" : "")} aria-pressed={k.prefs.speakReplies}
                onClick={() => setPref("speakReplies", !k.prefs.speakReplies)}
                aria-label={k.prefs.speakReplies ? "Stop reading replies aloud" : "Read replies aloud"}>
          <Icon name={k.prefs.speakReplies ? "speaker" : "mute"} />
        </button>
      </header>

      {ui.server.chat ? (
        <div className="chat-body">
          <ChatThread messages={chat.messages}
                      onSaveRecipe={(r) => { saveRecipe(r); ui.say("Saved to your recipes"); }}
                      onCookRecipe={(r) => { saveRecipe(r); ui.closeChat(); ui.startCooking(r, serves); }} />
          {userTurns >= 2 && !chat.busy && (
            <button className="btn btn-small btn-ghost draft-btn" onClick={draft} disabled={drafting}>
              <Icon name="spark" size={20} /> {drafting ? "Writing the recipe…" : "Turn this into a recipe"}
            </button>
          )}
          <Composer onSend={chat.send} busy={chat.busy} onStop={chat.stop}
                    placeholder="Ask, or paste a recipe link"
                    suggestions={userTurns ? [] : ["What can I make with what's going off?", "Something comforting, under 30 minutes", "How do I stop pasta sticking?"]} />
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
