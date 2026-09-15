/* Happy Bite — what the app shows while a model is thinking.

   A local 7B takes ten to thirty seconds to write a recipe. The old interface
   met that by disabling a button and changing its label to "Writing it…", which
   is indistinguishable from a hang. People tap again, then reload, then decide
   the app is broken.

   So this narrates instead. It names the phase, ticks off the ones that are
   done, and shows the clock once the wait is long enough to be worth
   acknowledging. Three rules it sticks to:

     * It never claims to be finished. The last stage stays lit until the real
       work returns — no progress bar sliding to 100% and then sitting there,
       which is the single most dishonest pattern in loading UI.
     * The stage timings are estimates and are labelled as estimates. When the
       real thing runs long, the copy says so rather than pretending.
     * A failure keeps the stage list on screen, so the error has a place: you
       can see it got as far as "Writing the method" before it gave up.

   It's driven by a plain array, so a new slow operation is a new list of
   strings, not a new component. */

import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon.jsx";
import "../styles/waiting.css";

/* Rough seconds per stage on a local Qwen. Used only to pace the narration —
   nothing waits on them, and the last one is open-ended by design. */
/* These are now keyed to the real phases the server reports (`key`), not to a
   stopwatch. Two lists because writing a recipe is two separate requests and
   pretending otherwise is what produced the old bug: the second half of the
   work showed "Reading your kitchen" (already done) and then sat on "Checking
   the ingredients" — a step that takes a millisecond and happens LAST — for the
   twenty seconds the model spent writing the method.

   `secs` survives only as the fallback pace for when the server can't narrate
   (an old backend, or the stream refused). It is never the truth. */
export const IDEA_STAGES = [
  { key: "kitchen", label: "Reading your kitchen", secs: 2 },
  { key: "corpus", label: "Looking through the cookbook", secs: 4 },
  { key: "ideas", label: "Thinking up two dishes", secs: 14 },
];

export const METHOD_STAGES = [
  { key: "kitchen", label: "Reading your kitchen", secs: 2 },
  { key: "corpus", label: "Looking through the cookbook", secs: 4 },
  { key: "method", label: "Writing the method", secs: 20 },
  { key: "check", label: "Checking it against your kitchen", secs: 2 },
];

/* Kept for anything still importing the old name. */
export const RECIPE_STAGES = METHOD_STAGES;

export const LINK_STAGES = [
  { label: "Fetching the page", secs: 3 },
  { label: "Finding the recipe on it", secs: 4 },
  { label: "Matching it to your kitchen", secs: 8 },
  { label: "Adding heat and cues", secs: 6 },
];

export const RECEIPT_STAGES = [
  { label: "Straightening the photo", secs: 2 },
  { label: "Reading the print", secs: 6 },
  { label: "Matching lines to ingredients", secs: 8 },
];

export const PICTURE_STAGES = [
  { label: "Warming the picture model", secs: 6 },
  { label: "Painting the dish", secs: 10 },
  { label: "Taking the other shots", secs: 12 },
];

export default function Waiting({
  stages = RECIPE_STAGES,
  title = "Working on it",
  note,                          // one line under the stages — what's doing the work
  error = null,                  // when set, the stages freeze and this is shown
  stage = null,                  // a stage KEY from the server; overrides the guess
  onRetry,
  onCancel,
  slowAfter = 30,                // seconds before we admit it's taking a while
}) {
  /* Two sources, one of them honest. When the server tells us where it is, that
     wins outright. The timer below is the fallback for a backend that can't
     narrate, and it is only ever a guess — which is why it can never reach the
     last stage on its own. */
  const [guess, setGuess] = useState(0);
  const told = stage == null ? -1 : stages.findIndex(s => s.key === stage);
  const at = told >= 0 ? told : guess;
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(Date.now());

  useEffect(() => {
    if (error) return undefined;
    const tick = setInterval(
      () => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 1000);
    return () => clearInterval(tick);
  }, [error]);

  useEffect(() => {
    if (error || stage != null) return undefined;             // the server is talking
    if (guess >= stages.length - 1) return undefined;         // hold on the last one
    const wait = setTimeout(() => setGuess(n => n + 1), (stages[guess]?.secs || 5) * 1000);
    return () => clearTimeout(wait);
  }, [guess, stages, error, stage]);

  const slow = elapsed >= slowAfter;
  /* Two different waits, and one message for both was a lie. Thirty seconds is
     a model loading off the SSD and is fine. Two minutes is not a slow start —
     it is a model that doesn't fit in this machine's memory, and saying "after
     that it's quick" at that point sends someone off to wait for a thing that
     is never going to get quicker. */
  const tooLong = elapsed >= 120;

  return (
    <div className={"waiting" + (error ? " is-failed" : "")}
         role="status" aria-live="polite" aria-busy={!error}>

      <div className="waiting-mark" aria-hidden="true">
        {error
          ? <Icon name="chef" size={34} />
          : <span className="waiting-pan"><i /><i /><i /></span>}
      </div>

      <p className="waiting-title">{error ? "That didn't work" : (stages[at]?.label || title)}</p>

      <ol className="waiting-stages">
        {stages.map((s, i) => {
          const state = error ? (i < at ? "done" : i === at ? "stuck" : "todo")
                              : (i < at ? "done" : i === at ? "now" : "todo");
          return (
            <li key={s.label} className={"waiting-stage is-" + state}>
              <span className="waiting-dot" aria-hidden="true" />
              <span className="waiting-label">{s.label}</span>
            </li>
          );
        })}
      </ol>

      {error
        ? <p className="waiting-error">{error}</p>
        : (
          <p className="waiting-meta">
            {note}
            {note && elapsed >= 4 ? " · " : ""}
            {elapsed >= 4 && <span className="waiting-clock">{elapsed}s</span>}
          </p>
        )}

      {!error && slow && !tooLong && (
        <p className="waiting-slow">
          Longer than usual. The first run of the day loads the model into memory —
          after that it's quick.
        </p>
      )}

      {!error && tooLong && (
        <p className="waiting-slow">
          Over two minutes is not a slow start — it usually means the model
          doesn't fit in this Mac's memory alongside everything else, so it's
          working from the disk. Closing other apps helps; a smaller model helps
          more. Activity Monitor → Memory: if Swap Used is above a gigabyte,
          that's it.
        </p>
      )}

      {(error || onCancel) && (
        <div className="waiting-actions">
          {error && onRetry && (
            <button className="btn btn-primary btn-small" onClick={onRetry}>Try again</button>
          )}
          {onCancel && (
            <button className="btn btn-ghost btn-small" onClick={onCancel}>
              {error ? "Back" : "Cancel"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/* The small inline version, for a wait inside a row rather than a whole panel:
   three dots and a word. Used where a full narration would be pompous. */
export function Working({ label = "Working", className = "" }) {
  return (
    <span className={"working " + className} role="status" aria-live="polite">
      <span className="working-dots" aria-hidden="true"><i /><i /><i /></span>
      <span className="working-label">{label}</span>
    </span>
  );
}