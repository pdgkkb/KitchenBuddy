/* Happy Bite — the shell. Tabs, the sheet on top, cooking mode and the
   chat over everything when they're open, plus the global voice agent.

   Two small additions: the screen is wrapped in a keyed element so a tab
   change animates on mount (no transition library — React already remounts it,
   CSS does the rest), and the boot state says what it's doing instead of
   showing a bare spinner. Both are governed by the bgAnim preference and by
   the OS reduce-motion setting; see styles/waiting.css. */

import { useEffect } from "react";
import { KitchenProvider, useKitchen } from "./state/kitchen.jsx";
import { UIProvider, useUI } from "./state/ui.jsx";
import { TabBar, TimerBar, Toast } from "./components/Chrome.jsx";
import { isInMemory } from "./lib/store.js";
import Today from "./screens/Today.jsx";
import Kitchen from "./screens/Kitchen.jsx";
import Recipes from "./screens/Recipes.jsx";
import Receipt from "./screens/Receipt.jsx";
import Shopping from "./screens/Shopping.jsx";
import CookMode, { CookDock } from "./screens/CookMode.jsx";
import ChatScreen from "./screens/ChatScreen.jsx";
import Sheets from "./sheets/index.jsx";
import VoiceAgent from "./components/VoiceAgent.jsx";
import "./styles/waiting.css";

const SCREENS = { today: Today, kitchen: Kitchen, recipes: Recipes, receipt: Receipt, shopping: Shopping };

function Shell() {
  const { k } = useKitchen();
  const ui = useUI();
  useEffect(() => { if (k.ready && isInMemory()) ui.say("Storage is off here — nothing will be saved"); }, [k.ready]); // eslint-disable-line
  useEffect(() => { document.body.classList.toggle("anim-off", k.prefs?.bgAnim === false); }, [k.prefs?.bgAnim]);

  /* Reading the kitchen out of IndexedDB is usually instant, but on a cold
     start with a full store it isn't — and a lone spinner gives no clue
     whether the app is starting or stuck. */
  if (!k.ready) return (
    <div className="boot" aria-busy="true" role="status">
      <span className="boot-mark" aria-hidden="true"><span /><span /><span /></span>
      <p className="boot-name">Happy Bite</p>
      <p className="boot-note">Opening your kitchen…</p>
    </div>
  );

  const Screen = SCREENS[ui.tab] || Today;
  return (
    <>
      {/* Keyed on the tab: React remounts on every change, so the mount
          animation in waiting.css runs without any exit-transition bookkeeping. */}
      <main className="main">
        <div className="screen-swap" key={ui.tab}><Screen /></div>
      </main>
      <TimerBar />
      <TabBar />
      <Sheets />
      {ui.cooking && !ui.cookMin && <CookMode key={ui.cooking.recipe.id} />}
      {ui.cooking && ui.cookMin && <CookDock />}
      {ui.chat && <ChatScreen />}
      <VoiceAgent />
      <Toast />
    </>
  );
}

export default function App() {
  return <KitchenProvider><UIProvider><Shell /></UIProvider></KitchenProvider>;
}