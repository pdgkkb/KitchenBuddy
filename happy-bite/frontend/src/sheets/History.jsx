/* What you've cooked — every dish finished in cooking mode, newest first.

   A few numbers at the top (this week, this month, days in a row, the dish
   you come back to), then the meals themselves, grouped the way you remember
   them: this week, then by month. Each one says when, for how many, how hard,
   and what you thought of it if you said — and offers to cook it again while
   it's still in the book. */

import { useMemo } from "react";
import * as E from "../core/engine.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import Stars from "../components/Stars.jsx";
import "../styles/history.css";

const VERDICT = { love: "Loved it", fine: "It was fine", no: "Not again" };

function when(date, today) {
  const days = Math.round((new Date(today) - new Date(date)) / E.DAY);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return new Date(date).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function groupOf(date, today) {
  const days = Math.round((new Date(today) - new Date(date)) / E.DAY);
  if (days < 7) return "This week";
  return new Date(date).toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

export default function HistorySheet() {
  const { k } = useKitchen();
  const ui = useUI();
  const today = E.isoDay();
  const list = useMemo(() => E.cookedHistory(k.history, k.book), [k.history, k.book]);
  const stats = useMemo(() => E.historyStats(k.history), [k.history]);

  const groups = [];
  for (const h of list) {
    const label = groupOf(h.date, today);
    if (!groups.length || groups[groups.length - 1].label !== label) groups.push({ label, items: [] });
    groups[groups.length - 1].items.push(h);
  }

  if (!list.length) return (
    <div className="history-empty">
      <Icon name="chef" size={40} />
      <p className="empty-head">Nothing cooked yet</p>
      <p className="empty-note">Finish a recipe in cooking mode and it shows up here, with the date and what you thought of it.</p>
      <button className="btn btn-primary" onClick={() => ui.openSheet("pick")}>Pick something to cook</button>
    </div>
  );

  return (
    <div className="history">
      <div className="history-stats">
        <div className="history-stat"><b>{stats.thisWeek}</b><span>this week</span></div>
        <div className="history-stat"><b>{stats.thisMonth}</b><span>this month</span></div>
        <div className="history-stat"><b>{stats.streak}</b><span>{stats.streak === 1 ? "day in a row" : "days in a row"}</span></div>
        <div className="history-stat"><b>{stats.total}</b><span>all time</span></div>
      </div>
      {stats.favourite && (
        <p className="history-fav"><Icon name="spark" size={18} />
          <span>Your favourite: <b>{stats.favourite.name}</b>, cooked {stats.favourite.count} times</span></p>
      )}

      {groups.map(g => (
        <section key={g.label}>
          <h3 className="sub-head">{g.label}</h3>
          <ul className="history-list">
            {g.items.map((h, i) => (
              <li key={`${h.date}-${h.recipe}-${h.order}-${i}`} className="history-row">
                <DishImage recipe={h.recipeNow || { name: h.name }} className="history-img" />
                <div className="history-text">
                  <p className="history-name">{h.name}</p>
                  <p className="history-meta">
                    <span>{when(h.date, today)}</span>
                    {h.serves && <span>for {h.serves}</span>}
                    {h.stars && <Stars value={h.stars} size={14} />}
                  </p>
                  {h.verdict && <span className={"history-verdict is-" + h.verdict}>{VERDICT[h.verdict]}</span>}
                </div>
                {h.recipeNow && (
                  <button className="btn btn-small btn-ghost" onClick={() => ui.openSheet("recipe", { id: h.recipe })}>
                    Cook again
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
