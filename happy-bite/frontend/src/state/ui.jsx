/* Happy Bite — interface state: which tab, which sheet, the toast, the
   timer, cooking mode, the chat, and what the server can do right now.

   Cooking mode can be MINIMISED: the session stays alive (same recipe, same
   step, the chef still listening through the dock) but the full screen steps
   out of the way, leaving a pulsating dock you tap — or say "go back to the
   cooking recipe" — to return. Only "stop cooking" ends it. */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import * as api from "../lib/api.js";

const Ctx = createContext(null);
export const useUI = () => useContext(Ctx);

export function UIProvider({ children }) {
  const [tab, setTabRaw] = useState("today");
  const [sheet, setSheet] = useState(null);          // { type, props }
  const [toast, setToast] = useState(null);
  const [timer, setTimer] = useState(null);          // { end, total, label }
  const [cooking, setCooking] = useState(null);      // { recipe, serves }
  const [cookMin, setCookMin] = useState(false);     // cooking minimised to the dock
  const [featured, setFeatured] = useState(null);    // id of tonight's suggested dish
  const [chat, setChat] = useState(null);            // { seed }
  const [server, setServer] = useState(api.OFF);
  const toastTimer = useRef();

  const refreshServer = useCallback(() => api.status().then(setServer), []);
  useEffect(() => {
    refreshServer();
    const h = setInterval(refreshServer, 30000);
    window.addEventListener("online", refreshServer);
    return () => { clearInterval(h); window.removeEventListener("online", refreshServer); };
  }, [refreshServer]);

  const say = useCallback((text) => {
    setToast({ text });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3400);
  }, []);

  const value = useMemo(() => ({
    tab, sheet, toast, timer, cooking, cookMin, chat, server, featured,
    setFeatured,
    setTab: (t) => { setTabRaw(t); window.scrollTo?.(0, 0); },
    openSheet: (type, props = {}) => setSheet({ type, props }),
    closeSheet: () => setSheet(null),
    say,
    startTimer: (minutes, label) => setTimer({ end: Date.now() + minutes * 60000, total: minutes * 60, label }),
    stopTimer: () => setTimer(null),
    startCooking: (recipe, serves) => { setSheet(null); setCookMin(false); setCooking({ recipe, serves }); },
    stopCooking: () => { setCooking(null); setCookMin(false); },
    minimizeCooking: () => setCookMin(true),           // step out, keep the session
    resumeCooking: () => setCookMin(false),            // come back to it
    openChat: (seed) => { setSheet(null); setChat({ seed }); },
    closeChat: () => setChat(null),
    refreshServer
  }), [tab, sheet, toast, timer, cooking, cookMin, chat, server, featured, say, refreshServer]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
