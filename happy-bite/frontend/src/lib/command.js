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
import { parseStock } from "./stockcmd.js";

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

  // Stock: "add 200 g of chicken", "what meat do we have", "how much milk is
  // left". See stockcmd.js for what each one says back.
  const stockAnswer = parseStock(text, stock);
  if (stockAnswer) return stockAnswer;

  // "I'm drained. Give me 15 minutes." — the whole request, with no dish named.
  // No questions back: one recipe, from the kitchen, in the time they gave.
  const tired = /\b(drained|exhausted|knackered|shattered|wiped out|destroyed|no energy|so tired|too tired|i m tired|im tired)\b/.test(t);
  const mins = t.match(/\b(\d{1,3}) ?(?:min|mins|minutes)\b/);
  if (!/\b(timer|remind|alarm)\b/.test(t) &&
      (tired || (mins && /\b(give me|i have|i ve got|ive got|i got|only have|dinner in|something in|meal in|ready in)\b/.test(t)))) {
    return { action: { kind: "generate_recipe", brief: text.trim(), maxMinutes: mins ? Number(mins[1]) : 15 } };
  }

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
      return { action: { kind: "open_featured" }, say: "Here are three from your recipes — pick one." };
  }

  // Reading the kitchen out loud — before navigation, so "what's in the kitchen"
  // reads it rather than opening the tab.
  if (READ_VERB.test(t) && !NAV_VERB.test(t)) {
    if (EXP_NOUN.test(t)) return { say: readExpiring(stock) };
    if (INV_NOUN.test(t)) return { say: readInventory(stock) };
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

/* Words that carry no instruction, at either end of what you said.
   ------------------------------------------------------------------------
   The old version compared the whole utterance against the phrase, allowing
   exactly ONE filler from a list of five. "next step" worked. "go to the next
   step" — which is what people actually say — did not, so it fell through to
   the chef as a question, and the answer arrived half a minute later while the
   step sat where it was.

   These are stripped repeatedly from both ends until nothing is left to strip,
   and only THEN is the remainder compared to the phrase. That keeps the rule
   the strict one it was meant to be: the whole utterance must BE the command.
   "what do I do after adding the tomatoes" strips nothing and still goes to
   the chef, which is right — it is a question. */
// NOTE the apostrophe-free spellings. norm() above turns every non-letter into
// a space, so "let's" reaches here as "let s" and "i'd" as "i d". Listing only
// the written forms is how "let's go to the next step" stayed a question.
const LEAD = ["ok", "okay", "okey", "alright", "all right", "right", "so", "and", "then",
              "um", "uh", "er", "well", "hey", "hey chef", "chef", "please", "the", "a",
              "can you", "could you", "would you", "i want to", "i would like to",
              "i d like to", "id like to", "lets", "let s", "let us",
              "go to", "go", "move to", "take me to", "take me", "jump to", "switch to",
              "now", "just", "yeah", "yes", "we", "you"];
const TAIL = ["please", "now", "chef", "thanks", "thank you", "for me", "then", "ok", "okay",
              "already", "step", "one"];

function strip(t, words) {
  const lead = words === LEAD;
  let out = t;
  for (let pass = 0; pass < 4; pass++) {
    let cut = false;
    for (const w of words) {
      const edge = lead ? w + " " : " " + w;
      if (lead ? out.startsWith(edge) : out.endsWith(edge)) {
        out = (lead ? out.slice(edge.length) : out.slice(0, -edge.length)).trim();
        cut = true;
        break;
      }
    }
    if (!cut) break;
  }
  return out;
}

function isCmd(t, phrases) {
  // Whole-utterance match first, so a phrase that IS a filler word — "next",
  // "ok", "done" — is never stripped into nothing before it can match.
  for (const p of phrases) if (t === p) return true;
  const core = strip(strip(t, LEAD), TAIL);
  if (!core) return false;
  for (const p of phrases) {
    if (core === p) return true;
    if (core === strip(strip(p, LEAD), TAIL)) return true;
  }
  return false;
}

/* "set a timer for 6 minutes", "timer for ten minutes", "time this for half an hour".
   A small local model narrates these ("I'll set a timer") without ever calling
   the tool, so the one thing someone at the hob asks for most has to work here,
   instantly, without the model. Returns minutes, or null. */
const NUMBER_WORDS = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50,
  sixty: 60, ninety: 90, couple: 2, few: 3 };
export function timerMinutes(text) {
  // Not norm(): it turns "1.5" into "1 5". Lower-case and keep decimals.
  const t = String(text || "").toLowerCase().replace(/(\d),(\d)/g, "$1.$2").replace(/[^a-z0-9.\s]/g, " ").replace(/\s+/g, " ").trim();
  if (!/\b(timer|time (it|this|that)|countdown)\b/.test(t)) return null;
  if (/\b(an|one) hour and a half\b/.test(t)) return 90;
  if (/\bhalf (an )?hour\b/.test(t)) return 30;
  if (/\bquarter (of )?(an )?hour\b/.test(t)) return 15;
  const m = t.match(/\b(\d+(?:\.\d+)?|[a-z]+(?: [a-z]+)?)(?: of)? (?:and a half )?(minutes?|mins?|hours?|hrs?|seconds?|secs?)\b/);
  if (!m) return null;
  const words = m[1].split(" ");
  let n;
  if (/\d/.test(m[1])) n = parseFloat(m[1]);
  else if (words.length === 2 && NUMBER_WORDS[words[0]] >= 20 && NUMBER_WORDS[words[1]] < 10) n = NUMBER_WORDS[words[0]] + NUMBER_WORDS[words[1]];
  else n = NUMBER_WORDS[words[words.length - 1]];
  if (!n) return null;
  if (/and a half/.test(t)) n += 0.5;
  const minutes = /^h/.test(m[2]) ? n * 60 : /^s/.test(m[2]) ? n / 60 : n;
  return minutes > 0 && minutes <= 600 ? Math.round(minutes * 60) / 60 : null;
}

/* "go to step 2", "back to step two", "step 3", "the first step".
   Before this existed, "go to step 2" went to the model as a question: it read
   step 2 out loud and the screen stayed where it was. Speech-to-text writes
   "two" as "to" or "too" often enough to count them, but only straight after
   "step". A question about a step ("how long is step 2") is not a move. */
const STEP_NUMBERS = {
  one: 1, won: 1, two: 2, to: 2, too: 2, three: 3, four: 4, for: 4, five: 5, six: 6,
  seven: 7, eight: 8, ate: 8, nine: 9, ten: 10, eleven: 11, twelve: 12,
};
const ORDINALS = {
  first: 1, second: 2, third: 3, fourth: 4, fifth: 5, sixth: 6,
  seventh: 7, eighth: 8, ninth: 9, tenth: 10, eleventh: 11, twelfth: 12,
};
const GOTO_LEAD = String.raw`^(?:(?:ok|okay|so|and|now|please|can you|could you|let s|lets)\s+)*` +
  String.raw`(?:(?:go back|go|jump|skip|move|get|take me|switch|show me|read me|read|back)\s+)?(?:on\s+)?(?:to\s+)?(?:the\s+)?`;
const GOTO_NUMBER = new RegExp(GOTO_LEAD + String.raw`step\s+(?:number\s+)?(\d{1,2}|[a-z]+)(?:\s+(?:please|now))?$`);
const GOTO_ORDINAL = new RegExp(GOTO_LEAD + String.raw`([a-z]+)\s+step(?:\s+(?:please|now))?$`);

export function stepNumber(t) {
  let m = t.match(GOTO_NUMBER);
  if (m) return /^\d+$/.test(m[1]) ? Number(m[1]) : STEP_NUMBERS[m[1]] || null;
  m = t.match(GOTO_ORDINAL);
  return m ? ORDINALS[m[1]] || null : null;
}

export function parseCook(text) {
  const t = norm(text);
  if (!t) return null;
  const goto = stepNumber(t);
  if (goto) return { cmd: "goto", step: goto };
  const minutes = timerMinutes(text);
  if (minutes && !/\b(stop|cancel|clear|kill)\b/.test(t)) return { cmd: "timer", minutes };
  // Eight rather than six: "can you go back to the previous step please" is
  // nine words of which three are instruction. The filler-stripping below is
  // what actually decides; this is only a cheap early exit.
  if (t.split(" ").length > 8) return null;       // too long to be a command — it's a question

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
