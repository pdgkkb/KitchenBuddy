/* Happy Bite — the offline fallback for "Create a recipe".

   Not a model and not pretending to be one. It assembles a plain dish from
   what's actually in the kitchen with one of a few technique templates,
   and the interface labels it as such: a template dressed up as
   intelligence is a lie the user finds out about at the hob. */

import { ref, daysLeft, uid } from "./engine.js";

const TEMPLATES = [
  {
    name: (m) => `Fried ${m} with rice`, base: "rice", minutes: 25, types: ["lunch", "dinner"],
    steps: (m) => [
      { do: "Cook the rice.", heat: "High", minutes: 14 },
      { do: "Get the pan hot, then add oil.", heat: "High", cue: "A drop of water skitters", minutes: 2 },
      { do: `Fry the ${m} without crowding the pan.`, heat: "High", cue: "Catching colour at the edges",
        why: "Too much at once cools the pan and it stews instead of frying.", minutes: 6 },
      { do: "Season, then fold through the rice.", heat: "Medium", minutes: 3 }
    ]
  },
  {
    name: (m) => `${m[0].toUpperCase() + m.slice(1)} traybake`, base: null, minutes: 35, types: ["dinner"],
    steps: (m) => [
      { do: "Heat the oven.", heat: "Oven 200 °C", minutes: 8 },
      { do: `Toss the ${m} with oil and salt on a tray.`, minutes: 4 },
      { do: "Spread it out in one layer.", why: "Piled up it steams. One layer is the whole difference.", minutes: 2 },
      { do: "Roast until the edges brown.", heat: "Oven 200 °C", cue: "Browned edges, a knife slides in", minutes: 25 }
    ]
  },
  {
    name: (m) => `Simple ${m} soup`, base: null, minutes: 30, types: ["lunch", "dinner"],
    steps: (m) => [
      { do: "Soften an onion in oil.", heat: "Medium-low", cue: "Soft and glossy, not brown", minutes: 8 },
      { do: `Add the ${m} and stir it around.`, heat: "Medium", minutes: 3 },
      { do: "Cover with water. Simmer.", heat: "Low", cue: "Everything soft enough to squash", minutes: 18 },
      { do: "Blend or leave it chunky. Season well.", minutes: 3 }
    ]
  }
];

export function draftRecipe(brief, stock) {
  const usable = stock
    .filter(a => ["produce", "meat", "seafood"].includes(ref(a.id).category))
    .sort((a, b) => daysLeft(a) - daysLeft(b));
  if (!usable.length) return null;

  const main = usable[0];
  const mainName = ref(main.id).name.toLowerCase();
  let t = TEMPLATES[Math.floor(Math.random() * TEMPLATES.length)];
  if (t.base && !stock.some(a => a.id === t.base)) t = TEMPLATES[1];

  const needs = [{ id: main.id, qty: ref(main.id).unit === "u" ? 2 : 400 }];
  if (t.base) needs.push({ id: t.base, qty: 300 });
  if (stock.some(a => a.id === "onion")) needs.push({ id: "onion", qty: 1 });
  if (stock.some(a => a.id === "oliveoil")) needs.push({ id: "oliveoil", qty: 2, flexible: true });

  return {
    id: "tpl_" + uid(), name: brief ? brief.slice(0, 60) : t.name(mainName),
    minutes: t.minutes, complexity: 1, types: t.types, cuisine: "Everyday", serves: 4,
    needs,
    seasoning: stock.some(a => a.id === "salt") ? [{ id: "salt", qty: 4, essential: true }] : [],
    steps: t.steps(mainName), origin: "template"
  };
}
