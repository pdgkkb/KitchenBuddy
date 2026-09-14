/* Happy Bite — on-device command parser (instant, no model).

   The local chef model (Qwen on Ollama) is smart but slow, and not always
   reliable at emitting a tool call. For the handful of things people say most
   — "go to the kitchen tab", "what's in the kitchen", "next step" — we don't
   need a model at all. This file recognises those on the spot and answers
   immediately: navigation happens with zero latency, and readouts are phrased
   like a person, not as a field dump.

   Anything it doesn't recognise returns null, and the caller falls back to the
   full chef. So this only ever makes things faster — it never blocks a real
   question.

   parse(text, ctx)    -> null | { action, say } | { say } | { resumeCooking, say }
       ctx = { stock, recipes: [{id,name}], openRecipeId }
   parseCook(text)     -> null | { cmd }        (cook-mode step/voice controls)
*/

import { ref } from "../core/engine.js";

const norm = (s) => String(s || "").toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();

/* Screens you can ask to be taken to, each with the words that mean it. */
const DESTS = [
  { view: "expiring",      words: ["expiring", "expire", "expires", "going off", "go off", "going bad", "use up", "use soon", "off soon"] },
  { view: "create_recipe", words: ["create a recipe", "new recipe", "make a recipe", "create recipe", "add a recipe", "write a recipe", "add recipe"] },
  { view: "shopping",      words: ["shopping", "shopping list", "groceries", "grocery", "grocery list", "to buy"] },
  { view: "recipes",       words: ["recipes", "recipe", "recipe book", "cookbook", "cook book"] },
  { view: "receipt",       words: ["receipt", "scan a receipt", "scan receipt"] },
  { view: "kitchen",       words: ["kitchen", "fridge", "pantry", "my stock", "the stock", "cupboard"] },
  { view: "today",         words: ["today", "tonight", "home", "home screen", "main screen", "start screen", "dinner tonight", "tonight tab"] },
];

const NAV_VERB = /\b(go to|open|show me|show|take me to|switch to|bring up|navigate to|jump to|head to|move to|let'?s go to)\b/;
const TAB_TAIL = /\b(tab|screen|page|section|view)\b/;

const READ_VERB = /\b(what|whats|how much|how many|list|read|tell me|say|check|do i have|have i got|is there|are there|any)\b/;
const INV_NOUN  = /\b(in (the |my )?(kitchen|fridge)|inventory|in stock|stock|left|do i have|have i got)\b/;
const EXP_NOUN  = /\b(going off|go off|going bad|expiring|expire|expires|use up|use soon|off soon)\b/;

/* Categories (by name fragment) that don't help you picture a meal — we leave
   these out of "what's in the kitchen" so it reads like a cook glancing at the
   shelf, not an audit. */
const BORING = /(spice|season|herb|condiment|sauce|oil|vinegar|baking|sweetener|sugar|salt|pepper|stock cube|other)/;
const HEARTY = /(meat|poultry|chicken|beef|pork|lamb|fish|seafood|protein|egg|veg|vegetable|fruit|dairy|cheese|grain|pasta|rice|noodle|bread|legume|pulse|bean|tofu|staple)/;

function findDest(t) {
  for (const d of DESTS) if (d.words.some(w => t.includes(w))) return d.view;
  return null;
}

const listWords = (arr) =>
  arr.length <= 1 ? (arr[0] || "") : arr.slice(0, -1).join(", ") + " and " + arr[arr.length - 1];

/* Curated, number-free: name the things you'd actually build a meal from, the
   meal-building ones first, and stop after a handful. No grams, no counts. */
function readInventory(stock) {
  const items = (stock || []).filter(s => s.qty > 0);
  if (!items.length) return "Your kitchen's empty at the moment — nothing to cook with.";
  const named = items.map(s => ({ ...s, name: (ref(s.id)?.name || s.id), cat: (ref(s.id)?.category || "") }));
  const hearty = named.filter(s => HEARTY.test((s.cat + " " + s.name).toLowerCase()));
  const rest = named.filter(s => !hearty.includes(s) && !BORING.test((s.cat + " " + s.name).toLowerCase()));
  const pick = [...hearty, ...rest].slice(0, 7).map(s => s.name.toLowerCase());
  if (!pick.length) return "Mostly odds and ends right now — nothing that adds up to a meal on its own.";
  return `You've got ${listWords(pick)} — enough to put a good meal together.`;
}

function readExpiring(stock) {
  const soon = (stock || [])
    .filter(s => s.qty > 0 && typeof s.daysLeft === "number" && s.daysLeft <= 3)
    .sort((a, b) => a.daysLeft - b.daysLeft);
  if (!soon.length) return "Nothing's going off in the next few days — you're good.";
  const say = soon.slice(0, 6).map(s => {
    const name = (ref(s.id)?.name || s.id).toLowerCase();
    const when = s.daysLeft <= 0 ? "today" : s.daysLeft === 1 ? "tomorrow" : `in ${s.daysLeft} days`;
    return `${name} ${when}`;
  });
  return `Use ${listWords(say)}.`;
}

/* "how much milk do I have", "do I have any eggs" -> one item, in human terms
   (a count if it's countable, otherwise a rough sense — never grams). */
function readOneItem(t, stock) {
  const items = (stock || []).filter(s => s.qty > 0);
  const hit = items.find(s => {
    const name = norm(ref(s.id)?.name || s.id);
    return name && (t.includes(name) || (name.endsWith("s") && t.includes(name.slice(0, -1))));
  });
  if (!hit) return null;                               // unknown item — let the model try
  const name = (ref(hit.id)?.name || hit.id).toLowerCase();
  const unit = hit.unit || ref(hit.id)?.unit || "g";
  if (unit === "u") return `You've got ${Math.round(hit.qty)} ${name}.`;
  const lots = unit === "l" ? hit.qty >= 1 : hit.qty >= 300;   // ~a litre / ~300 g
  return `You've got ${lots ? "plenty of" : "a little"} ${name}.`;
}

function matchRecipe(t, recipes) {
  if (!recipes || !recipes.length) return null;
  const sorted = [...recipes].sort((a, b) => (b.name || "").length - (a.name || "").length);
  for (const r of sorted) {
    const n = norm(r.name);
    if (n && (t.includes(n) || (n.split(" ").length > 1 && n.split(" ").every(w => w.length > 2 && t.includes(w)))))
      return r;
  }
  return null;
}

export function parse(text, ctx = {}) {
  const { stock, recipes, openRecipeId } = Array.isArray(ctx) ? { stock: ctx } : ctx;
  const t = norm(text);
  if (!t) return null;

  // Broad recipe requests are actions, not questions. The creator has the
  // live kitchen inventory and can decide the details without asking.
  if (/\b(create|make|cook|prepare|whip up|fix)\b/.test(t) &&
      /\b(recipe|something|dish|meal|dinner|lunch|breakfast)\b/.test(t) &&
      !/\b(open|show|see|view|go to|take me to)\b/.test(t)) {
    return { action: { kind: "generate_recipe", brief: text.trim() } };
  }

  // Stop a running timer.
  if (/\b(stop|cancel|clear|kill|dismiss)\b.*\btimer\b/.test(t) || /\bno timer\b/.test(t))
    return { action: { kind: "stop_timer" }, say: "Timer stopped." };

  // Turn the background animation on or off by voice.
  if (/\b(turn off|switch off|stop|disable|hide|kill|no more)\b.*\b(animation|animations|background|effects?|motion|moving)\b/.test(t))
    return { action: { kind: "set_anim", on: false }, say: "Animations off." };
  if (/\b(turn on|switch on|start|enable|show|bring back|put back)\b.*\b(animation|animations|background|effects?|motion|moving)\b/.test(t))
    return { action: { kind: "set_anim", on: true }, say: "Animations on." };

  // Come back to a minimised cooking session.
  if (/\b(back to|return to|resume|reopen|go back to)\b.*\b(cook|cooking)\b/.test(t) || /\bresume cooking\b/.test(t)) {
    return { resumeCooking: true, say: "Back to it." };
  }

  // "close / hide the chat", "go back"
  if (/\b(close|hide|dismiss|get rid of|remove)\b.*\bchat\b/.test(t) || /^\s*(go back|never mind|nevermind|that'?s all)\s*$/.test(t)) {
    return { action: { kind: "navigate", view: "close" }, say: "" };
  }

  // Cooking flow, the way it reads on screen:
  //  • Name a dish ("cook / make / open the tomato pasta") -> OPEN ITS SHEET, so
  //    they see it first.
  //  • A sheet is open and they say "let's cook this" / "start cooking" -> go
  //    FULL-SCREEN into cooking.
  //  • Bare "let's cook" / "cook this" with nothing open -> open tonight's dish.
  if (recipes && recipes.length) {
    const r = matchRecipe(t, recipes);
    const startNow = /\b(cook it|cook this|start cooking|let'?s cook|let'?s go|make it|make this|start it|start now|ok cook|okay cook|go ahead)\b/.test(t);
    const cookVerb = /\b(cook|make|prepare|whip up|fix)\b/.test(t);
    const openVerb = /\b(open|show|see|view|pull up|bring up|go to|take me to)\b/.test(t);

    if (r && (cookVerb || openVerb || /\brecipe\b/.test(t)))
      return { action: { kind: "open_recipe", id: r.id }, say: `Here's the ${r.name.toLowerCase()} — say "let's cook" when you're ready.` };
    if (openRecipeId && startNow)
      return { action: { kind: "cook_recipe", id: openRecipeId }, say: "Let's cook." };
    if (!r && (startNow || cookVerb))
      return { action: { kind: "open_featured" }, say: "Here's tonight's dish — say \"let's cook\" to start." };
  }

  // Reading the kitchen out loud — before navigation, so "what's in the kitchen"
  // reads it rather than opening the tab.
  if (READ_VERB.test(t) && !NAV_VERB.test(t)) {
    if (EXP_NOUN.test(t)) return { say: readExpiring(stock) };
    if (INV_NOUN.test(t)) {
      const one = /\bany\b|\bhow much\b|\bhow many\b|\bdo i have\b|\bhave i got\b/.test(t) ? readOneItem(t, stock) : null;
      return { say: one || readInventory(stock) };
    }
  }

  // Navigation — needs a verb ("go to…", "show me…") or a trailing "…tab".
  if (NAV_VERB.test(t) || TAB_TAIL.test(t)) {
    const view = findDest(t);
    if (view) {
      const label = {
        today: "tonight's suggestion", kitchen: "your kitchen", expiring: "what's going off soon",
        recipes: "your recipes", shopping: "the shopping list", receipt: "the receipt scanner",
        create_recipe: "a new recipe",
      }[view] || view;
      return { action: { kind: "navigate", view }, say: `Here's ${label}.` };
    }
  }

  return null;                                    // not a command — hand to the chef
}

/* Cook-mode voice controls.

   Commands are matched EXACTLY (the whole utterance is the command, give or
   take a filler word), never as words buried in a sentence — so a real question
   like "what do I do after adding the tomatoes?" is never mistaken for "next" or
   "back" and never moves the step. Anything that isn't one of these short
   commands returns null and goes to the chef as a question. */
const CMD = {
  next:    ["next", "next step", "next one", "continue", "go on", "carry on", "move on", "keep going", "and then", "what's next", "whats next", "move along", "onwards"],
  confirm: ["done", "yes", "yep", "yeah", "ok", "okay", "okey", "got it", "all set", "ready", "did it", "i did it", "that's done", "thats done", "finished that", "next please"],
  back:    ["back", "go back", "previous", "previous step", "last step", "one back", "back a step", "step back", "go back a step"],
  repeat:  ["repeat", "again", "say again", "say that again", "what was that", "come again", "read it again", "one more time", "what's the step", "whats the step"],
  finish:  ["finished", "all done", "we're done", "were done", "that's it", "thats it", "it's done", "its done", "done cooking"],
  ingredients: ["ingredients", "what do i need", "what's in it", "whats in it", "read the ingredients", "list ingredients"],
  timer:   ["timer", "set a timer", "start a timer", "start the timer", "time it", "set the timer"],
  stopTimer: ["stop timer", "stop the timer", "cancel timer", "cancel the timer", "clear timer", "clear the timer", "kill the timer", "no timer"],
  stopChef: ["stop", "stop chef", "stop talking", "quiet", "be quiet", "hush", "shush", "enough", "shut up", "ok stop", "okay stop", "that's enough", "thats enough", "stop it"],
  stopCooking: ["stop cooking", "end cooking", "finish cooking", "exit cooking", "quit cooking", "close cooking", "leave cooking", "i'm done cooking", "im done cooking"],
  minimize: ["minimize", "minimise", "hide this", "step out", "put it away", "make it smaller", "go smaller"],
};

/* True when the utterance IS the phrase, allowing a leading/trailing filler
   ("ok next", "next please", "the next step"). */
function isCmd(t, phrases) {
  for (const p of phrases) {
    if (t === p) return true;
    if (t === "ok " + p || t === "okay " + p || t === "please " + p || t === "the " + p) return true;
    if (t === p + " please" || t === p + " now" || t === p + " chef") return true;
  }
  return false;
}

export function parseCook(text) {
  const t = norm(text);
  if (!t) return null;
  if (t.split(" ").length > 6) return null;       // too long to be a command — it's a question

  // Order matters: "stop cooking"/"stop timer" must beat a bare "stop".
  if (isCmd(t, CMD.stopCooking)) return { cmd: "stopCooking" };
  if (isCmd(t, CMD.stopTimer))   return { cmd: "stopTimer" };
  if (isCmd(t, CMD.minimize))    return { cmd: "minimize" };
  if (isCmd(t, CMD.stopChef))    return { cmd: "stopChef" };
  if (isCmd(t, CMD.finish))      return { cmd: "finish" };
  if (isCmd(t, CMD.next) || isCmd(t, CMD.confirm)) return { cmd: "next" };
  if (isCmd(t, CMD.back))        return { cmd: "back" };
  if (isCmd(t, CMD.repeat))      return { cmd: "repeat" };
  if (isCmd(t, CMD.ingredients)) return { cmd: "ingredients" };
  if (isCmd(t, CMD.timer))       return { cmd: "timer" };

  return null;                                    // a real question — hand to the chef
}
