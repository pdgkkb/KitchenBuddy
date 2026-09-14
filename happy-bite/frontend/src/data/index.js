/* Happy Bite — data. One copy, in /shared, read by the browser and the
   server alike. PROVISIONAL: shape matters, contents don't.

   INGREDIENTS is deliberately mutable: products the household names
   from a receipt are merged into it at load, so every screen knows them. */

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
