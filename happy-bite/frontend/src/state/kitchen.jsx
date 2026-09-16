/* Happy Bite — the household's data and every change made to it.

   The only place that mutates. Screens read `k` and call actions; nothing
   else writes to storage. All of it lives on this device.

   KitchenBuddy reaches this file through `useApplyAction` (state/actions.jsx),
   which is why the assistant can only do things a person could already do by
   tapping — it calls the very same methods. */

import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { INGREDIENTS, RECIPES, STARTING_STOCK, catalogReady } from "../data/index.js";
import * as E from "../core/engine.js";
import * as Store from "../lib/store.js";

const Ctx = createContext(null);
export const useKitchen = () => useContext(Ctx);

const DEFAULT_PEOPLE = [
  { id: "p1", name: "Camille", avoids: [] },
  { id: "p2", name: "Julien", avoids: ["cod"] },
  { id: "p3", name: "Léa", avoids: ["pork", "beef"] }
];

const PERSISTED = {
  people: DEFAULT_PEOPLE, diners: null, stock: null, planned: [], bought: [], wishlist: [],
  corrections: {}, customs: {}, taste: E.EMPTY_TASTE, filters: {}, myRecipes: [], history: [],
  prefs: { speakReplies: false, readSteps: false, bgAnim: true },
  /* What they can cook ON. null means NOBODY HAS SAID — which is not the same
     as owning nothing, and the difference matters: on null the app says nothing
     about equipment at all rather than telling someone their kitchen is missing
     an oven they never mentioned. */
  equipment: null,
  adaptations: {}
};

async function load() {
  // Every key in parallel, alongside the corpus catalogue: one wait, not eighteen.
  const keys = Object.keys(PERSISTED);
  const [values, recipeReset] = await Promise.all([
    Promise.all(keys.map(key => Store.read(key, PERSISTED[key]))),
    Store.read("recipeBookReset", false),
    catalogReady
  ]);
  const s = Object.fromEntries(keys.map((key, i) => [key, values[i]]));
  // One-time migration: remove the old built-in/template-created local book,
  // then preserve recipes created by the assistant from this point onward.
  if (!recipeReset) {
    s.myRecipes = [];
    await Store.write("myRecipes", s.myRecipes);
    await Store.write("recipeBookReset", true);
  }
  if (!s.stock) {
    const t0 = E.today().getTime();
    s.stock = STARTING_STOCK.map(a => ({
      id: a.id, qty: a.qty, unit: E.ref(a.id).unit,
      bought: E.isoDay(new Date(t0 + a.bought * E.DAY))
    }));
    await Store.write("stock", s.stock);
  }
  if (!s.diners) s.diners = s.people.map(p => p.id);
  Object.assign(INGREDIENTS, s.customs);
  return { ...s, receipt: null, ready: true };
}

const round2 = (n) => Math.round(n * 100) / 100;
const slug = (name) => "u_" + String(name).toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 24);

export function KitchenProvider({ children }) {
  const [k, setK] = useState({ ready: false });
  const now = useRef(k);
  now.current = k;

  useEffect(() => { load().then(setK); }, []);

  const actions = useMemo(() => {
    /* Write-through: state first so the screen answers at once, storage after. */
    const set = (changes) => {
      setK(prev => ({ ...prev, ...changes }));
      now.current = { ...now.current, ...changes };
      for (const key of Object.keys(changes)) if (key in PERSISTED) Store.write(key, changes[key]);
    };
    const get = () => now.current;
    const toggle = (list, id) => list.includes(id) ? list.filter(x => x !== id) : [...list, id];

    return {
      set,
      toggleDiner: (id) => set({ diners: toggle(get().diners, id) }),

      saveProfile(p) {
        const { people, diners } = get();
        const exists = people.some(x => x.id === p.id);
        set({
          people: exists ? people.map(x => x.id === p.id ? p : x) : [...people, p],
          diners: exists ? diners : [...diners, p.id]
        });
      },
      removeProfile: (id) => set({
        people: get().people.filter(x => x.id !== id),
        diners: get().diners.filter(x => x !== id)
      }),

      setFilter(key, value) {
        const filters = { ...get().filters };
        if (value === null || value === undefined) delete filters[key]; else filters[key] = value;
        set({ filters });
      },
      clearFilters: () => set({ filters: {} }),

      addWish: (id) => !get().wishlist.includes(id) && set({ wishlist: [...get().wishlist, id] }),
      togglePlanned: (id) => set({ planned: toggle(get().planned, id) }),
      toggleBought: (id) => set({ bought: toggle(get().bought, id) }),
      clearShopping: () => set({ bought: [], wishlist: [], planned: [] }),

      finishCooking(recipe, serves) {
        const stock = get().stock.map(a => ({ ...a }));
        for (const n of recipe.needs) {
          const item = stock.find(a => a.id === n.id);
          if (!item) continue;
          const r = E.ref(n.id);
          const want = E.scale(n.qty, serves, recipe.serves, r.unit);
          item.qty = Math.max(0, item.qty - E.convert(want, r.unit, item.unit || r.unit, r));
        }
        set({
          stock: stock.filter(a => a.qty > 0),
          planned: get().planned.filter(x => x !== recipe.id),
          history: [...get().history, { date: E.isoDay(), recipe: recipe.id }].slice(-400)
        });
      },

      saveReview: (review) => set({ taste: E.applyReview(get().taste, review) }),

      setStock(id, qty, unit) {
        const stock = qty <= 0
          ? get().stock.filter(x => x.id !== id)
          : get().stock.map(x => x.id === id ? { ...x, qty, unit } : x);
        set({ stock });
      },

      /* ---- Live changes KitchenBuddy makes on your say-so. Same rules as a tap. ---- */

      /* "I used 100 g of milk" -> delta negative; "found more" -> positive. */
      adjustStock(id, delta) {
        const stock = get().stock.map(a => ({ ...a }));
        const item = stock.find(a => a.id === id);
        if (item) {
          item.qty = round2(Math.max(0, item.qty + delta));
          set({ stock: stock.filter(a => a.qty > 0) });
        } else if (delta > 0) {
          stock.push({ id, qty: round2(delta), unit: E.ref(id).unit, bought: E.isoDay() });
          set({ stock });
        }
      },

      /* Something bought or found, with a quantity. `daysLeft` sets an explicit
         use-by; otherwise the shelf-life estimate applies. */
      addStockItem(id, qty, unit, daysLeft) {
        const stock = get().stock.map(a => ({ ...a }));
        const patch = { bought: E.isoDay() };
        if (typeof daysLeft === "number") {
          patch.expires = E.isoDay(new Date(E.today().getTime() + daysLeft * E.DAY));
        }
        const item = stock.find(a => a.id === id);
        if (item) { item.qty = round2(item.qty + qty); Object.assign(item, patch); }
        else stock.push({ id, qty: round2(qty), unit: unit || E.ref(id).unit, ...patch });
        set({ stock });
      },

      setExpiry(id, days) {
        const expires = E.isoDay(new Date(E.today().getTime() + days * E.DAY));
        set({ stock: get().stock.map(a => a.id === id ? { ...a, expires } : a) });
      },

      /* Teach the kitchen a new ingredient. Mirrors createProduct but takes an
         explicit id/unit/shelfLife (the ones KitchenBuddy or a receipt supply). */
      addCustom({ id, name, category, unit, shelfLife }) {
        const iid = id || slug(name);
        const product = { name, category: category || "other", unit: unit || "g", shelfLife: shelfLife || 14 };
        INGREDIENTS[iid] = product;
        set({ customs: { ...get().customs, [iid]: product } });
        return iid;
      },

      addToShopping(ids) {
        const wishlist = [...get().wishlist];
        for (const id of ids) if (!wishlist.includes(id)) wishlist.push(id);
        set({ wishlist });
      },

      setReceipt: (receipt) => set({ receipt }),

      resolveLine(index, id, rejected = false) {
        const receipt = structuredClone(get().receipt);
        const line = receipt.lines[index];
        Object.assign(line, rejected ? { id: null, confidence: 0, rejected: true }
                                     : { id, confidence: 1, rejected: false });
        set({ receipt, corrections: { ...get().corrections, [line.raw]: rejected ? null : id } });
      },

      createProduct(name, category) {
        const id = slug(name);
        const product = { name, category, unit: "g", shelfLife: 14 };
        INGREDIENTS[id] = product;
        set({ customs: { ...get().customs, [id]: product } });
        return id;
      },

      putAway() {
        const { receipt } = get();
        const date = E.isoDay();
        const stock = get().stock.map(a => ({ ...a }));
        let count = 0;
        for (const l of receipt.lines) {
          if (!l.id || l.rejected || l.confidence < E.THRESHOLD) continue;
          const r = E.ref(l.id);
          const held = stock.find(a => a.id === l.id);
          if (held) { held.qty += E.convert(l.qty, r.unit, held.unit || r.unit, r); held.bought = date; }
          else stock.push({ id: l.id, qty: l.qty, unit: r.unit, bought: date });
          count++;
        }
        set({ stock, receipt: null });
        return count;
      },

      saveRecipe: (recipe) => set({ myRecipes: [...get().myRecipes.filter(r => r.id !== recipe.id), recipe] }),
      updateRecipe: (id, patch) => set({ myRecipes: get().myRecipes.map(r => r.id === id ? { ...r, ...patch } : r) }),
      deleteRecipe: (id) => set({ myRecipes: get().myRecipes.filter(r => r.id !== id) }),

      /* Photos for the built-in dishes are kept apart from the book itself. */
      setPhoto(id, url) {
        if (get().myRecipes.some(r => r.id === id)) {
          return set({ myRecipes: get().myRecipes.map(r => r.id === id ? { ...r, photo: url } : r) });
        }
        set({ prefs: { ...get().prefs, photos: { ...(get().prefs.photos || {}), [id]: url } } });
      },

      setPref: (key, value) => set({ prefs: { ...get().prefs, [key]: value } }),

      /* ---- What you cook with, and the ways round it ---- */

      setEquipment: (ids) => set({ equipment: [...new Set(ids)] }),

      /* Keyed by recipe AND the exact equipment it was written for (see
         core/equipment.js adaptKey), so buying an air fryer retires yesterday's
         "no, you can't" instead of serving it forever after it stopped being
         true. Capped, because these are whole answers and storage is a phone's. */
      saveAdaptation(key, adaptation) {
        const all = { ...get().adaptations, [key]: { ...adaptation, at: Date.now() } };
        const keys = Object.keys(all).sort((a, b) => (all[b].at || 0) - (all[a].at || 0));
        set({ adaptations: Object.fromEntries(keys.slice(0, 40).map(x => [x, all[x]])) });
      },

      forgetAdaptation(key) {
        const all = { ...get().adaptations };
        delete all[key];
        set({ adaptations: all });
      }
    };
  }, []);

  const book = useMemo(() => {
    if (!k.ready) return RECIPES;
    const photos = k.prefs.photos || {};
    return RECIPES.map(r => photos[r.id] ? { ...r, photo: photos[r.id] } : r).concat(k.myRecipes);
  }, [k.ready, k.myRecipes, k.prefs]);

  const value = useMemo(() => ({ k: { ...k, book }, ...actions }), [k, book, actions]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/* What a chat or recipe request is allowed to know about the kitchen:
   names, quantities, days left. Never who lives here. */
export function stockForServer(stock) {
  return stock.map(a => ({
    id: a.id, name: E.ref(a.id).name, qty: a.qty, unit: a.unit || E.ref(a.id).unit, daysLeft: E.daysLeft(a)
  }));
}
