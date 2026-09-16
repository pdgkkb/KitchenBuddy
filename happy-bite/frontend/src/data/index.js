/* Happy Bite — data. One copy, in /shared, read by the browser and the
   server alike. PROVISIONAL: shape matters, contents don't.

   INGREDIENTS is deliberately mutable: products the household names
   from a receipt are merged into it at load, so every screen knows them.

   In a Vite build the catalogue arrives in two parts (see vite.perf.js): the
   hand-written core inside the bundle, and the ~3,000 corpus ingredients as a
   separate, preloaded JSON file. `catalogReady` resolves once they are merged;
   the kitchen waits for it before opening. Under plain Node (npm test) the
   import is the whole file and there is nothing to fetch. */

import catalog from "../../../shared/catalog.json" with { type: "json" };
import book from "../../../shared/recipes.json" with { type: "json" };
import receipt from "../../../shared/receipt-sample.json" with { type: "json" };

export const CATEGORIES = catalog.categories;
export const INGREDIENTS = catalog.ingredients;
export const STARTING_STOCK = catalog.startingStock;
// The recipe book starts empty. Recipes are created by the assistant and
// saved locally after the household chooses one.
export const RECIPES = [];
export const MEAL_TYPES = book.mealTypes;
export const COMPLEXITY = book.complexity;
export const SAMPLE_RECEIPT = receipt;
export const CUISINES = [...new Set(RECIPES.map(r => r.cuisine))].sort();

export const catalogReady = !catalog.corpusUrl ? Promise.resolve() :
  fetch(catalog.corpusUrl)
    .then(res => { if (!res.ok) throw new Error(`HTTP ${res.status}`); return res.json(); })
    .then(rows => {
      for (const [id, name, category, unit, shelfLife] of rows) {
        if (!(id in INGREDIENTS)) INGREDIENTS[id] = { name, category, unit, shelfLife };
      }
    })
    .catch(e => console.warn("Happy Bite: corpus ingredients unavailable, using the core catalogue.", e));
