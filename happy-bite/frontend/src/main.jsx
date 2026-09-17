import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/figtree";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/redesign.css";
import "./styles/sheet-hairlines.css";
import "./styles/receipt-capture.css";
import "./styles/motion.css";                 // last: interaction and motion states win
import App from "./App.jsx";

/* In production the stylesheet is loaded without blocking the first paint
   (see vite.perf.js); the boot screen in index.html is styled inline. Mount
   once it has applied, so the real interface never flashes unstyled. */
function stylesheetReady() {
  const link = document.querySelector("link[data-app-css]");
  if (!link || link.sheet) return Promise.resolve();
  // The first load event is the preload; the stylesheet applies on a later one.
  return new Promise((done) => {
    const check = () => link.sheet ? done() : link.addEventListener("load", check, { once: true });
    link.addEventListener("error", done, { once: true });
    check();
  });
}

stylesheetReady().then(() => {
  createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
});

/* Offline after the first visit. Skipped in development, where a cached
   bundle would hide every change you make. Registered after load so it
   never competes with the first render. */
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}), { once: true });
}
