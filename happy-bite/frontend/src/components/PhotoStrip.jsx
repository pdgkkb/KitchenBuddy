/* Happy Bite — a recipe's photographs.

   A dish gets a few pictures, not one. A single generated photo is a coin flip:
   SD-Turbo will give you a beautiful gratin or a bowl of orange mud, and with
   one take you're stuck with whichever you got. Three takes, framed and lit
   differently, and you pick.

   The work happens on the server and the pictures are kept there, so this stays
   thin: ask once, show what exists, poll while more are coming, stop. Tapping
   one makes it the dish's picture everywhere.

   With picture generation off it renders nothing at all — the recipe sheet
   looks exactly as it did before. */

import { useEffect, useRef, useState } from "react";
import * as api from "../lib/api.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { Icon } from "./Icon.jsx";
import "../styles/happy-extra.css";

const POLL_MS = 2500;
const GIVE_UP_AFTER = 90;          // ~4 minutes: SD-Turbo on CPU is slow, not infinite

export default function PhotoStrip({ recipe, auto = true, onPrimary }) {
  const ui = useUI();
  const { setPhoto } = useKitchen();
  const [urls, setUrls] = useState([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);       // bumping this re-runs the whole effect
  const timer = useRef(null);

  const id = recipe && recipe.id;
  const on = ui.server.images;
  const start = auto || nonce > 0;

  useEffect(() => {
    if (!on || !id) return undefined;
    let alive = true;
    let tries = 0;

    const take = (res) => {
      if (!alive || !res) return;
      const list = res.urls || [];
      setUrls(list);
      setPending(!!res.pending);
      /* The first picture becomes the dish's photo, so the hero image and the
         cards in the book fill in on their own. Later ones are offered, not
         imposed — swapping it under someone as they read would be rude. */
      if (list.length && !recipe.photo) {
        setPhoto(id, list[0]);
        onPrimary?.(list[0]);
      }
      if (res.pending && tries < GIVE_UP_AFTER) {
        tries += 1;
        timer.current = setTimeout(poll, POLL_MS);
      }
    };

    const poll = () => { api.recipeImages(id).then(take).catch(() => {}); };

    setError("");
    (start ? api.makeRecipeImages(recipe) : api.recipeImages(id))
      .then(take)
      .catch((e) => alive && setError(e.message || "Pictures aren't available."));

    return () => { alive = false; clearTimeout(timer.current); };
  }, [id, on, nonce]);  // eslint-disable-line react-hooks/exhaustive-deps

  /* Throw the set away and paint another. The effect above does the work — this
     only clears the old files and bumps the nonce, so there is exactly one
     polling loop in this component rather than two that can race. */
  const again = async () => {
    clearTimeout(timer.current);
    setUrls([]);
    setPending(true);
    try { await api.clearRecipeImages(id); } catch { /* nothing to clear */ }
    setNonce(n => n + 1);
  };

  if (!on || !id) return null;
  if (error) return <p className="sub-note photo-strip-note">{error}</p>;
  if (!urls.length && !pending) return null;

  return (
    <div className="photo-strip">
      <div className="photo-strip-row">
        {urls.map((u) => (
          <button key={u} type="button"
                  className={"photo-thumb" + (recipe.photo === u ? " is-on" : "")}
                  onClick={() => { setPhoto(id, u); ui.say("Picture changed"); }}
                  aria-label="Use this picture">
            <img src={u} alt="" loading="lazy" />
          </button>
        ))}
        {pending && (
          <span className="photo-thumb is-waiting" aria-label="Another picture on the way">
            <Icon name="image" size={20} />
          </span>
        )}
      </div>
      <div className="photo-strip-foot">
        <span className="sub-note">
          {pending ? "Painting the rest…"
                   : urls.length > 1 ? "Tap one to use it" : "One picture so far"}
        </span>
        {!pending && <button type="button" className="link" onClick={again}>New set</button>}
      </div>
    </div>
  );
}
