/* Happy Bite — the shell. Tabs, the sheet on top, cooking mode and the
   chat over everything when they're open. */

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
import CookMode from "./screens/CookMode.jsx";
import ChatScreen from "./screens/ChatScreen.jsx";
import Sheets from "./sheets/index.jsx";

const SCREENS = { today: Today, kitchen: Kitchen, recipes: Recipes, receipt: Receipt, shopping: Shopping };

function Shell() {
  const { k } = useKitchen();
  const ui = useUI();
  useEffect(() => { if (k.ready && isInMemory()) ui.say("Storage is off here — nothing will be saved"); }, [k.ready]); // eslint-disable-line
  if (!k.ready) return <div className="boot" aria-busy="true"><span className="spin" /></div>;
  const Screen = SCREENS[ui.tab] || Today;
  return (
    <>
      <main className="main"><Screen key={ui.tab} /></main>
      <TimerBar />
      <TabBar />
      <Sheets />
      {ui.cooking && <CookMode key={ui.cooking.recipe.id} />}
      {ui.chat && <ChatScreen />}
      <Toast />
    </>
  );
}

export default function App() {
  return <KitchenProvider><UIProvider><Shell /></UIProvider></KitchenProvider>;
}
