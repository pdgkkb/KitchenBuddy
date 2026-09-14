/* Today — one dish, and the kitchen at a glance underneath.

   Still one proposal: a list of forty recipes is the problem this product
   exists to remove. The dashboard is below the fold on purpose; the dish
   is the answer, the charts are the reasons. */

import { useEffect, useMemo, useState } from "react";
import * as E from "../core/engine.js";
import { parse } from "../core/intent.js";
import * as api from "../lib/api.js";
import { MEAL_TYPES, COMPLEXITY } from "../data/index.js";
import { useKitchen } from "../state/kitchen.jsx";
import { useUI } from "../state/ui.jsx";
import { DishImage } from "../components/Chrome.jsx";
import { Icon } from "../components/Icon.jsx";
import { Ring, RingStack, SparkBars, WeekStrip } from "../components/Charts.jsx";

export const nameOf = (id) => E.ref(id).name || id;
export const listOf = (ids) => ids.map(nameOf).join(", ");
export const effortName = (n) => (COMPLEXITY.find(c => c.level === n) || {}).name || "";

export default function Today() {
  const { k, toggleDiner, setFilter } = useKitchen();
  const ui = useUI();
  const [ask, setAsk] = useState(null);
  const [askText, setAskText] = useState("");
  const [asking, setAsking] = useState(false);
  const [surprised, setSurprised] = useState(null);
  const [allowIncomplete, setAllowIncomplete] = useState(false);

  const ctx = useMemo(() => {
    const avoid = ask?.avoid || [];
    return {
      stock: k.stock, taste: k.taste, recipes: k.book, allowIncomplete,
      people: avoid.length ? [...k.people, { id: "__ask", avoids: avoid }] : k.people,
      diners: avoid.length ? [...k.diners, "__ask"] : k.diners,
      serves: k.diners.length || 1,
      filters: { ...k.filters, ...(ask?.filters || {}) }
    };
  }, [k, ask, allowIncomplete]);

  const proposal = useMemo(() => E.propose(ctx), [ctx]);
  const shopping = useMemo(() => E.shoppingList(k.planned, k.stock, k.diners.length || 1, k.wishlist, k.book), [k]);
  const summary = useMemo(() => E.kitchenSummary({ ...k, shopping }), [k, shopping]);

  const reset = () => { setSurprised(null); setAllowIncomplete(false); };

  async function applyAsk(text) {
    if (!text.trim()) return;
    setAsking(true);
    let read;
    if (ui.server.chat) {
      try { read = { ...(await api.understand(text)), source: "assistant" }; }
      catch { read = { ...parse(text), degraded: true }; }
      if (!Object.keys(read.filters || {}).length && !read.avoid?.length) read = parse(text);
    } else read = parse(text);
    setAsking(false);
    reset();
    const blank = !Object.keys(read.filters || {}).length && !read.avoid?.length;
    if (blank) { setAsk(null); ui.say(ui.server.chat ? "Couldn't make anything of that" : "Nothing recognised — without the server this is keyword matching only"); return; }
    setAsk(read);
  }

  const dropFilter = (key) => {
    reset();
    if (key === "__avoid") return setAsk(a => ({ ...a, avoid: [] }));
    if (ask?.filters?.[key] !== undefined) {
      const f = { ...ask.filters }; delete f[key]; return setAsk({ ...ask, filters: f });
    }
    setFilter(key, null);
  };

  const pick = surprised || proposal.main;
  const date = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });

  /* Tell the rest of the app what tonight's dish is, so "let's cook" by voice
     can open it even from another screen. */
  useEffect(() => { if (pick?.recipe?.id) ui.setFeatured?.(pick.recipe.id); }, [pick?.recipe?.id]); // eslint-disable-line

  return (
    <div className="screen">
      <header className="today-head">
        <div>
          <p className="today-date">{date}</p>
          <h1 className="screen-title">Tonight</h1>
        </div>
        <div className="head-actions">
          <button className="icon-btn" onClick={() => ui.openSheet("table")} aria-label="Who's eating"><Icon name="people" /></button>
          <button className="icon-btn" onClick={() => ui.openSheet("server")} aria-label="Assistant and voice"><Icon name="gear" /></button>
        </div>
      </header>

      <WeekStrip week={summary.week} />

      <div className="askbar">
        <input className="askbar-input" value={askText} placeholder="What do you feel like eating?"
               disabled={asking} enterKeyHint="search"
               onChange={(e) => setAskText(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && applyAsk(askText)} />
        <button className="icon-btn icon-btn-lg is-mint" onClick={() => applyAsk(askText)} aria-label="Find">
          {asking ? <span className="spin" /> : <Icon name="send" />}
        </button>
      </div>
      {ask && (
        <div className="ask-pill">
          <span className="ask-text">{ask.understood.join(", ")}</span>
          <span className="ask-src">{ask.source === "assistant" ? "read by the assistant" : ask.degraded ? "assistant unreachable, matched locally" : "matched locally"}</span>
          <button className="icon-btn icon-btn-sm" aria-label="Clear" onClick={() => { setAsk(null); setAskText(""); reset(); }}>
            <Icon name="close" size={18} />
          </button>
        </div>
      )}

      {!k.diners.length ? (
        <section className="empty-state">
          <h2 className="empty-head">Who's eating?</h2>
          <p className="empty-note">Portions follow the table, so nothing gets proposed until somebody's sitting at it.</p>
          <div className="chips">
            {k.people.map(p => <button key={p.id} className="chip chip-lg" onClick={() => toggleDiner(p.id)}>{p.name}</button>)}
            <button className="chip chip-lg chip-ghost" onClick={() => ui.openSheet("household")}>Manage household</button>
          </div>
        </section>
      ) : pick ? (
        <>
          <article className="dish-card">
            <DishImage recipe={pick.recipe} className="dish-photo dish-hero">
              <div className="dish-overlay">
                <h2 className="dish-name">{pick.recipe.name}</h2>
                <DishTags recipe={pick.recipe} />
              </div>
            </DishImage>
            <div className="match">
              <Ring value={pick.have / Math.max(1, pick.total)} size={74} stroke={8}
                    color={pick.cookable ? "var(--mint)" : "var(--sky)"}
                    label={`${pick.have} of ${pick.total} ingredients in the kitchen`}>
                <span className="match-frac">{pick.have}<small>/{pick.total}</small></span>
              </Ring>
              <div className="match-text">
                <Reason pick={pick} surprised={!!surprised} />
                <button className="link" onClick={() => ui.openSheet("score", { pick })}>How was this picked?</button>
              </div>
            </div>
          </article>

          <button className="table-strip" onClick={() => ui.openSheet("table")}>
            <span className="avatars">{k.diners.map(id => {
              const p = k.people.find(x => x.id === id);
              return <span key={id} className="avatar">{(p?.name || "?")[0]}</span>;
            })}</span>
            <span className="table-text">For {k.diners.length}: {k.diners.map(id => k.people.find(p => p.id === id)?.name).filter(Boolean).join(", ")}</span>
            <span className="table-edit">Change</span>
          </button>

          <FilterPills filters={ctx.filters} avoid={ask?.avoid} onDrop={dropFilter} />

          <div className="row-actions">
            <button className="btn btn-primary" onClick={() => ui.openSheet("recipe", { id: pick.recipe.id })}>Cook this</button>
            <button className="btn btn-ghost" onClick={() => ui.openSheet("alternates", { list: proposal.alternates })}>Something else</button>
          </div>
          <div className="row-links">
            <button className="link" onClick={() => ui.openSheet("adjust")}><Icon name="sliders" size={20} /> Adjust</button>
            <button className="link" onClick={() => {
              const s = E.surprise(ctx, surprised?.recipe.id);
              s ? setSurprised(s) : ui.say("Nothing fits this table");
            }}><Icon name="dice" size={20} /> Surprise me</button>
          </div>
        </>
      ) : (
        <NothingFits proposal={proposal} filters={ctx.filters} avoid={ask?.avoid} onDrop={dropFilter}
                     onNear={() => setAllowIncomplete(true)} />
      )}

      <Dashboard summary={summary} />
    </div>
  );
}

export function DishTags({ recipe }) {
  return (
    <p className="tags">
      <span className="tag"><Icon name="clock" size={18} />{recipe.minutes} min</span>
      <span className="tag"><Icon name="flame" size={18} />{effortName(recipe.complexity)}</span>
      <span className="tag">{recipe.cuisine}</span>
      {recipe.origin && <span className="tag tag-mint">{recipe.origin === "link" ? "From a link" : "Yours"}</span>}
    </p>
  );
}

function Reason({ pick, surprised }) {
  const [head, detail] =
    surprised ? ["Drawn at random", pick.cookable ? "Everything's already here" : "You'd need " + listOf(pick.missing.map(m => m.id))]
    : pick.urgent.length ? ["Uses what goes off first", listOf(pick.urgent)]
    : pick.missing.length ? ["You'd need", listOf(pick.missing.map(m => m.id))]
    : ["Everything's in the kitchen", "Nothing to buy"];
  return (
    <div className={"reason" + (pick.urgent.length && !surprised ? " is-warm" : "")}>
      <span className="reason-head">{head}</span>
      <span className="reason-detail">{detail}</span>
    </div>
  );
}

/* Every active constraint is its own pill, each removable on its own. */
function FilterPills({ filters: f, avoid, onDrop }) {
  const out = [];
  if (f.mealType) out.push(["mealType", MEAL_TYPES.find(m => m.id === f.mealType)?.name]);
  if (f.maxMinutes) out.push(["maxMinutes", "under " + f.maxMinutes + " min"]);
  if (f.maxComplexity && f.maxComplexity < 3) out.push(["maxComplexity", effortName(f.maxComplexity)]);
  if (f.cuisine) out.push(["cuisine", f.cuisine]);
  if (f.mustUse) out.push(["mustUse", "with " + nameOf(f.mustUse).toLowerCase()]);
  if (avoid?.length) out.push(["__avoid", "without " + listOf(avoid).toLowerCase()]);
  if (!out.length) return null;
  return (
    <div className="pills">
      {out.map(([key, label]) => (
        <button key={key} className="pill" onClick={() => onDrop(key)} aria-label={`Remove ${label}`}>
          {label}<Icon name="close" size={16} />
        </button>
      ))}
    </div>
  );
}

function NothingFits({ proposal: p, filters, avoid, onDrop, onNear }) {
  const ui = useUI();
  if (p.considered === 0) return (
    <section className="empty-state">
      <h2 className="empty-head">The filters rule out everything</h2>
      <p className="empty-note">Nothing in the book matches all of them at once.</p>
      <FilterPills filters={filters} avoid={avoid} onDrop={onDrop} />
      <button className="btn btn-ghost" onClick={() => ui.openSheet("adjust")}>Loosen the filters</button>
    </section>
  );
  if (p.nearMisses > 0) return (
    <section className="empty-state">
      <h2 className="empty-head">Nothing you can cook right now</h2>
      <p className="empty-note">{p.nearMisses} {p.nearMisses === 1 ? "dish is" : "dishes are"} one or two
        ingredients short. An empty screen is worse than a near miss.</p>
      <FilterPills filters={filters} avoid={avoid} onDrop={onDrop} />
      <div className="row-actions">
        <button className="btn btn-primary" onClick={onNear}>Show what's close</button>
        <button className="btn btn-ghost" onClick={() => ui.openSheet("create")}>Invent something</button>
      </div>
    </section>
  );
  return (
    <section className="empty-state">
      <h2 className="empty-head">Nothing works for this table</h2>
      <p className="empty-note">Every dish is ruled out by what somebody here won't eat.</p>
      <button className="btn btn-ghost" onClick={() => ui.openSheet("table")}>Change the table</button>
    </section>
  );
}

/* The kitchen at a glance: three rings, three tiles. */
function Dashboard({ summary: s }) {
  const ui = useUI();
  const pct = (a, b) => b ? Math.round((a / b) * 100) : 0;
  const rings = [
    { key: "fresh", value: s.items ? s.fresh / s.items : 0, color: "var(--mint)", label: "Fresh", text: `${pct(s.fresh, s.items)}%` },
    { key: "ready", value: s.bookSize ? s.ready / s.bookSize : 0, color: "var(--sky)", label: "Ready to cook", text: `${s.ready} of ${s.bookSize}` },
    { key: "shop", value: s.shoppingTotal ? 1 - s.shoppingLeft / s.shoppingTotal : 0, color: "var(--peach)", label: "Shopping done",
      text: s.shoppingTotal ? `${s.shoppingTotal - s.shoppingLeft} of ${s.shoppingTotal}` : "nothing planned" }
  ];
  return (
    <section className="dash" aria-label="Your kitchen">
      <h2 className="section-title">Your kitchen</h2>
      <div className="dash-rings">
        <RingStack rings={rings} size={156} stroke={14} gap={5}
                   label={rings.map(r => `${r.label}: ${r.text}`).join(". ")} />
        <ul className="legend">
          {rings.map(r => (
            <li key={r.key}><span className="legend-dot" style={{ background: r.color }} />
              <span className="legend-label">{r.label}</span><span className="legend-value">{r.text}</span></li>
          ))}
        </ul>
      </div>
      <div className="tiles">
        <button className="tile" onClick={() => ui.setTab("kitchen")}>
          <span className="tile-label">Use soon</span>
          <span className={"tile-value" + (s.useSoon + s.gone ? " is-coral" : "")}>{s.useSoon + s.gone}</span>
          <SparkBars values={s.soon} hot={(i) => i < 4} />
          <span className="tile-sub">next 7 days</span>
        </button>
        <button className="tile" onClick={() => ui.setTab("recipes")}>
          <span className="tile-label">Ready to cook</span>
          <span className="tile-value">{s.ready}</span>
          <span className="tile-sub">of {s.bookSize} recipes, with what's here</span>
        </button>
        <div className="tile">
          <span className="tile-label">Cooked this week</span>
          <span className="tile-value">{s.cookedThisWeek}</span>
          <SparkBars values={s.week.map(d => d.cooked)} color="var(--sky)" />
          <span className="tile-sub">meals, last 7 days</span>
        </div>
      </div>
    </section>
  );
}
