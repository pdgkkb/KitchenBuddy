/* Which sheet is open. Every sheet but the recipe uses the same frame. */

import { useUI } from "../state/ui.jsx";
import { Sheet } from "../components/Chrome.jsx";
import RecipeSheet from "./RecipeSheet.jsx";
import ScoreSheet from "./ScoreSheet.jsx";
import { TableSheet, HouseholdSheet, ProfileSheet } from "./People.jsx";
import { AdjustSheet, QuantitySheet, VerifySheet } from "./Kitchen.jsx";
import { CreateSheet, LinkSheet, DraftSheet } from "./Create.jsx";
import { ReviewSheet, ServerSheet } from "./Misc.jsx";
import EquipmentSheet from "./Equipment.jsx";
import PickSheet from "./Pick.jsx";
import HistorySheet from "./History.jsx";
import { nameOf } from "../screens/Today.jsx";

const SHEETS = {
  score:      [ScoreSheet, () => "How this was picked"],
  table:      [TableSheet, () => "Who's eating"],
  household:  [HouseholdSheet, () => "Household"],
  profile:    [ProfileSheet, (p) => p.id ? "Edit" : "Add someone"],
  adjust:     [AdjustSheet, () => "Adjust"],
  quantity:   [QuantitySheet, (p) => nameOf(p.id)],
  verify:     [VerifySheet, () => "What is this?"],
  create:     [CreateSheet, () => "Create a recipe"],
  link:       [LinkSheet, () => "Add from a link"],
  draft:      [DraftSheet, () => "New recipe", true],
  review:     [ReviewSheet, () => "How was it?"],
  server:     [ServerSheet, () => "Assistant and voice"],
  equipment:  [EquipmentSheet, () => "What I cook with"],
  history:    [HistorySheet, () => "What you've cooked"]
};

export default function Sheets() {
  const { sheet, closeSheet } = useUI();
  if (!sheet) return null;
  if (sheet.type === "recipe") return <RecipeSheet key={sheet.props.id} {...sheet.props} />;
  // The chooser is its own full-screen frame, not a bottom sheet.
  if (sheet.type === "pick") return <PickSheet {...sheet.props} />;
  const entry = SHEETS[sheet.type];
  if (!entry) return null;
  const [Body, title, wide] = entry;
  return (
    <Sheet title={title(sheet.props)} onClose={closeSheet} wide={wide}>
      <Body key={JSON.stringify(sheet.props).slice(0, 80)} {...sheet.props} />
    </Sheet>
  );
}
