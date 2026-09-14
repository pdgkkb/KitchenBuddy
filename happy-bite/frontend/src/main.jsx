import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/figtree";
import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/redesign.css";
import App from "./App.jsx";

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);

/* Offline after the first visit. Skipped in development, where a cached
   bundle would hide every change you make. */
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
