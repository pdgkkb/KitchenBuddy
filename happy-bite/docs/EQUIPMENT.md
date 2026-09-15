# Cooking it with what you've actually got

A recipe is written for a kitchen somebody imagined. Yours is the one you have.
This is the join between them.

## The three moving parts

**`frontend/src/core/equipment.js`** — the vocabulary (sixteen appliances) and a
plain-text detector: `wantedBy(recipe)` reads each step's `do`, `heat`, `cue`
and `why` and says what the recipe leans on. `heat` is the strongest signal in
the data, because "Oven 200 °C" lives there. This is in `core/` deliberately: it
works with the server off. Knowing tonight's dish wants an oven you haven't got
is a fact about the recipe, not a question for a model.

**`backend/app/adapt.py`** — the same vocabulary and the same patterns on the
server, plus `clean_adaptation`, which is to an adaptation what
`recipes.clean_recipe` is to a recipe. Model output is untrusted input: step
numbers outside the recipe are dropped, equipment ids the kitchen hasn't got are
dropped, minutes are clamped.

**`frontend/src/components/AdaptPanel.jsx`** — the panel inside the recipe.

## What the household says, and what "hasn't said" means

`k.equipment` is `null` until somebody opens the equipment sheet. Null is **not**
"owns nothing". Nothing is flagged, no panel is offered, and the chef is told
nothing about equipment. Telling a person their kitchen is missing an oven they
never mentioned is the kind of confident wrongness that makes people close an
app.

The sheet opens with the usual four already on (hob, frying pan, saucepan, oven)
and asks you to turn off what you haven't got — most people have one missing
thing, not fifteen present ones, so that is two taps rather than sixteen.

## "No" is an answer

`possible` is `yes`, `partly` or `no`, and `no` has its own colour and its own
copy. A model asked to do a slow-roast shoulder in a microwave will cheerfully
invent something; the result is a ruined dinner rather than a wasted minute.

The validator enforces the other half of that: a `yes` that rewrites no steps is
rejected outright, because it is the model agreeing with the question rather
than answering it. `watch` — the honest list of how the result will differ —
renders under its own heading and is not optional in the prompt.

## Where an answer is kept

Under `recipeId::<sorted equipment ids>`, in `k.adaptations`. Buying an air
fryer changes the key, so yesterday's "no, you can't" is not served forever
after the thing that made it true has changed.

## What the chef knows

`k.equipment` rides along in the chat context, so "I've got a pan and a
microwave but no oven — can I still do this?" is answered against the recipe on
screen and the kit on the list, in both the full chat and cooking mode. The
panel's own ask box goes to `/api/recipes/adapt` instead, because that returns
the structured thing the panel draws.

## Not done yet

Cooking mode still walks the **original** steps. Adapting them there means
deciding what the step list *is* while someone is standing over a pan, and it
wants its own pass rather than a flag on this one.
