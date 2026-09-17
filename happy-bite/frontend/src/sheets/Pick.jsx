/* Three dishes from the book, dealt like upgrade cards in a survivors game.

   The moment of choosing from recipes you already have used to be a list in a
   bottom sheet. Choosing between three is quicker than scanning a list, and a
   hand you can reroll makes "none of these" a tap rather than a scroll. Each
   card is the four things the decision rests on: the photo, the name, the time
   and the difficulty.

   The first hand is the best three for this kitchen (engine.drawChoices).
   Reroll deals three others at random. 1, 2 and 3 on a keyboard pick a card,
   R rerolls, Escape closes. With fewer than three recipes in the book, the
   empty places offer to write one. */

import { useEffect, useMemo, useState } from "react";
import * as E from "../core/engine.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import Stars, { starLabel } from "../components/Stars.jsx";
import { listOf } from "../screens/Today.jsx";
import "../styles/pick.css";

export default function PickSheet({ people, diners, filters }) {
  const { k } = useKitchen();
  const ui = useUI();
  const ctx = useMemo(() => ({
    stock: k.stock, taste: k.taste, recipes: k.book,
    people: people || k.people, diners: diners || k.diners,
    serves: k.diners.length || 1, filters: filters || k.filters,
  }), [k, people, diners, filters]);

  const [hand, setHand] = useState(() => E.drawChoices(ctx));
  const [deal, setDeal] = useState(0);                 // re-keys the cards so they pop in again

  const reroll = () => {
    setHand(E.drawChoices(ctx, { reroll: true, avoid: hand.map(n => n.recipe.id) }));
    setDeal(d => d + 1);
  };
  const choose = (n) => ui.openSheet("recipe", { id: n.recipe.id });
  const canReroll = k.book.length > hand.length;

  useEffect(() => {
    const key = (e) => {
      if (e.target.closest?.("input, textarea")) return;
      if (e.key === "Escape") ui.closeSheet();
      else if (/^[123]$/.test(e.key) && hand[Number(e.key) - 1]) choose(hand[Number(e.key) - 1]);
      else if (e.key.toLowerCase() === "r" && canReroll) reroll();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  });

  const slots = [0, 1, 2].map(i => hand[i] || null);

  return (
    <div className="pick" role="dialog" aria-modal="true" aria-label="Pick a dish">
      <div className="pick-scrim" onClick={ui.closeSheet} />
      <section className="pick-panel">
        <header className="pick-head">
          <div>
            <p className="pick-eyebrow">From your recipes</p>
            <h2 className="pick-title">Pick one</h2>
          </div>
          <button className="icon-btn" onClick={ui.closeSheet} aria-label="Close"><Icon name="close" /></button>
        </header>

        <ol className="pick-cards" key={deal}>
          {slots.map((n, i) => (
            <li key={n ? n.recipe.id : `empty-${i}`} style={{ "--i": i }}>
              {n ? (
                <button className={"pick-card" + (n.cookable ? "" : " is-short")} onClick={() => choose(n)}
                        aria-label={`${n.recipe.name}, ${E.minutesOf(n.recipe)} minutes, difficulty ${starLabel(E.starsOf(n.recipe))} out of 5`}>
                  <span className="pick-key" aria-hidden="true">{i + 1}</span>
                  <DishImage recipe={n.recipe} className="pick-photo" />
                  <span className="pick-body">
                    <span className="pick-name">{n.recipe.name}</span>
                    <span className="pick-meta">
                      <span className="pick-time"><Icon name="clock" size={16} /> {E.minutesOf(n.recipe)} min</span>
                      <Stars recipe={n.recipe} size={17} />
                    </span>
                    <span className={"pick-need" + (n.cookable ? " is-ready" : "")}>
                      {n.cookable ? "Ready to cook" : "Need " + listOf(n.missing.map(m => m.id))}
                    </span>
                  </span>
                </button>
              ) : (
                <button className="pick-card is-empty" onClick={() => ui.openSheet("create")}>
                  <Icon name="spark" size={34} />
                  <span className="pick-name">Write a new one</span>
                  <span className="pick-need">from what's in the kitchen</span>
                </button>
              )}
            </li>
          ))}
        </ol>

        <div className="pick-foot">
          {canReroll && (
            <button className="btn btn-ghost pick-reroll" onClick={reroll}>
              <Icon name="dice" size={22} /> Reroll
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
