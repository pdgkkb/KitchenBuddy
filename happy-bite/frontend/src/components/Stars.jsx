/* Difficulty, as half a star to five (see starsOf in core/engine.js).

   Five star shapes, each full, half or empty, and one label for a screen
   reader — "Difficulty 3.5 out of 5" — rather than five separate images. */

import { useId } from "react";
import { starsOf } from "../core/engine.js";
import "../styles/stars.css";

const PATH = "M12 2.8l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 16.8l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z";

export function starLabel(value) {
  const n = String(value).replace(".5", "½").replace(/^0½$/, "½");
  return `${n} ${value <= 1 ? "star" : "stars"}`;
}

export default function Stars({ recipe, value, size = 16, className = "" }) {
  const v = value ?? starsOf(recipe);
  const clip = useId();
  return (
    <span className={"stars " + className} role="img" aria-label={`Difficulty ${v} out of 5`}
          title={`Difficulty: ${starLabel(v)} out of 5`}>
      {[1, 2, 3, 4, 5].map(i => {
        const fill = v >= i ? "full" : v >= i - 0.5 ? "half" : "empty";
        return (
          <svg key={i} className={"star is-" + fill} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
            {fill === "half" && (
              <defs><clipPath id={`${clip}-${i}`}><rect x="0" y="0" width="12" height="24" /></clipPath></defs>
            )}
            <path d={PATH} className="star-back" />
            {fill !== "empty" && <path d={PATH} className="star-fill" clipPath={fill === "half" ? `url(#${CSS.escape(clip)}-${i})` : undefined} />}
          </svg>
        );
      })}
    </span>
  );
}
