/* Happy Bite — interface state: which tab, which sheet, the toast, the
   timer, cooking mode, the chat, and what the server can do right now. */

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
    tab, sheet, toast, timer, cooking, chat, server,
    setTab: (t) => { setTabRaw(t); window.scrollTo?.(0, 0); },
    openSheet: (type, props = {}) => setSheet({ type, props }),
    closeSheet: () => setSheet(null),
    say,
    startTimer: (minutes, label) => setTimer({ end: Date.now() + minutes * 60000, total: minutes * 60, label }),
    stopTimer: () => setTimer(null),
    startCooking: (recipe, serves) => { setSheet(null); setCooking({ recipe, serves }); },
    stopCooking: () => setCooking(null),
    openChat: (seed) => { setSheet(null); setChat({ seed }); },
    closeChat: () => setChat(null),
    refreshServer
  }), [tab, sheet, toast, timer, cooking, chat, server, say, refreshServer]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
