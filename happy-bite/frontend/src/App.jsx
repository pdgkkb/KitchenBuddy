/* Happy Bite — the shell. Tabs, the sheet on top, cooking mode and the
   chat over everything when they're open, plus the global voice agent.

   Two small additions: the screen is wrapped in a keyed element so a tab
   change animates on mount (no transition library — React already remounts it,
   CSS does the rest; see styles/motion.css), and while the kitchen is being
   read the loading screen in index.html stays up. It is told to leave through
   window.hbReady() once there is something real to show.

   Cooking mode, the chat, the sheets and the voice agent are split into their
   own chunks: none of them is on screen when the app opens. They are fetched
   when the browser is idle after the first render, so opening one is still
   instant. */

import { Suspense, lazy, useEffect } from "react";
import { KitchenProvider, useKitchen } from "./state/kitchen.jsx";
import { UIProvider, useUI } from "./state/ui.jsx";
import { TabBar, TimerBar, Toast } from "./components/Chrome.jsx";
import { isInMemory } from "./lib/store.js";
import Today from "./screens/Today.jsx";
import Kitchen from "./screens/Kitchen.jsx";
import Recipes from "./screens/Recipes.jsx";
import Receipt from "./screens/Receipt.jsx";
import Shopping from "./screens/Shopping.jsx";
import "./styles/waiting.css";

const loadCook = () => import("./screens/CookMode.jsx");
const loadChat = () => import("./screens/ChatScreen.jsx");
const loadSheets = () => import("./sheets/index.jsx");
const loadAgent = () => import("./components/VoiceAgent.jsx");

const CookMode = lazy(loadCook);
const CookDock = lazy(() => loadCook().then(m => ({ default: m.CookDock })));
const ChatScreen = lazy(loadChat);
const Sheets = lazy(loadSheets);
const VoiceAgent = lazy(loadAgent);

const whenIdle = (fn) => (window.requestIdleCallback
  ? requestIdleCallback(fn, { timeout: 2500 })
  : setTimeout(fn, 1200));

const SCREENS = { today: Today, kitchen: Kitchen, recipes: Recipes, receipt: Receipt, shopping: Shopping };

function Shell() {
  const { k } = useKitchen();
  const ui = useUI();
  useEffect(() => { if (k.ready && isInMemory()) ui.say("Storage is off here — nothing will be saved"); }, [k.ready]); // eslint-disable-line
  useEffect(() => { document.body.classList.toggle("anim-off", k.prefs?.bgAnim === false); }, [k.prefs?.bgAnim]);
  // Dismiss the loading screen (index.html) once the real interface exists.
  useEffect(() => { if (k.ready) window.hbReady?.(); }, [k.ready]);
  // Warm the lazy chunks once the kitchen is on screen.
  useEffect(() => {
    if (k.ready) whenIdle(() => { loadSheets(); loadAgent(); loadChat(); loadCook(); });
  }, [k.ready]);

  /* Reading the kitchen out of IndexedDB is usually instant, but not always.
     Until it's done the loading screen in index.html covers the page, so there
     is nothing to render here. */
  if (!k.ready) return null;

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
      {/* One boundary each: a chunk still loading never hides the others. */}
      <Suspense fallback={null}><Sheets /></Suspense>
      {ui.cooking && !ui.cookMin && <Suspense fallback={null}><CookMode key={ui.cooking.recipe.id} /></Suspense>}
      {ui.cooking && ui.cookMin && <Suspense fallback={null}><CookDock /></Suspense>}
      {ui.chat && <Suspense fallback={null}><ChatScreen /></Suspense>}
      <Suspense fallback={null}><VoiceAgent /></Suspense>
      <Toast />
    </>
  );
}

export default function App() {
  return <KitchenProvider><UIProvider><Shell /></UIProvider></KitchenProvider>;
}