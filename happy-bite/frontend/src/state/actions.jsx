/* Happy Bite — carrying out what KitchenBuddy asks.

   The server validates every tool call and streams it here as an `action`
   event (see backend/app/actions.py). This hook turns one into the matching
   kitchen mutation and returns a one-line receipt to show in the chat. It
   only ever calls methods a person could trigger by tapping — the assistant
   gets no privileged path to the data. */

import { useCallback } from "react";
import { useKitchen } from "./kitchen.jsx";
import { useUI } from "./ui.jsx";
import { ref, formatQty } from "../core/engine.js";

const nameOf = (id) => ref(id).name || id;

export function useApplyAction() {
  const kitchen = useKitchen();
  const ui = useUI();

  return useCallback((action) => {
    if (!action || !action.kind) return null;
    const K = kitchen;
    switch (action.kind) {
      case "adjust_stock": {
        K.adjustStock(action.id, action.delta);
        const r = ref(action.id);
        return { text: `${action.delta < 0 ? "Used" : "Added"} ${formatQty(Math.abs(action.delta), r.unit)} ${nameOf(action.id).toLowerCase()}` };
      }
      case "set_stock": {
        const r = ref(action.id);
        K.setStock(action.id, action.qty, r.unit);
        return { text: `${nameOf(action.id)} set to ${formatQty(action.qty, r.unit)}` };
      }
      case "add_stock": {
        if (action.create) K.addCustom(action.create);
        K.addStockItem(action.id, action.qty, action.unit, action.daysLeft);
        return { text: `Added ${formatQty(action.qty, action.unit || ref(action.id).unit)} ${nameOf(action.id).toLowerCase()}` };
      }
      case "set_expiry": {
        K.setExpiry(action.id, action.days);
        return { text: `${nameOf(action.id)}: good ${action.days} more day${action.days === 1 ? "" : "s"}` };
      }
      case "add_custom": {
        K.addCustom(action);
        return { text: `Added ${action.name} to the catalogue` };
      }
      case "save_recipe": {
        K.saveRecipe(action.recipe);
        return { text: `Saved “${action.recipe.name}”`, recipe: action.recipe };
      }
      case "add_to_shopping": {
        K.addToShopping(action.ids);
        return { text: `Added to shopping: ${action.ids.map(nameOf).join(", ")}` };
      }
      case "set_timer": {
        ui.startTimer(action.minutes, action.label || "Timer");
        return { text: `Timer set — ${action.minutes} min${action.label ? " · " + action.label : ""}` };
      }
      case "navigate": {
        const v = action.view;
        const labels = {
          today: "tonight's suggestion", kitchen: "the kitchen", expiring: "what's going off soon",
          recipes: "the recipes", shopping: "the shopping list", receipt: "the receipt scanner",
          create_recipe: "the recipe creator", chat: "the chef"
        };
        if (v === "chat") { ui.openChat(); return { text: "Opened the chef" }; }
        if (v === "close") { ui.closeChat(); return { text: "Closed the chat" }; }
        ui.closeChat();                         // step out of the full-screen chat so the tab shows
        if (v === "create_recipe") { ui.setTab("recipes"); ui.openSheet("create"); }
        else if (v === "expiring") ui.setTab("kitchen");
        else ui.setTab(v);                      // today | kitchen | recipes | shopping | receipt
        return { text: `Opened ${labels[v] || v}` };
      }
      case "generate_recipe":
        ui.setTab("recipes");
        ui.openSheet("create", { autoGenerate: true, initialBrief: action.brief || "Something good with what is in the kitchen",
                                 maxMinutes: action.maxMinutes || null });
        return { text: "Checking the kitchen and creating recipe options" };
      case "open_recipe": {
        const r = kitchen.k.book.find(x => x.id === action.id);
        if (!r) return null;
        ui.closeChat();
        ui.setTab("recipes");
        ui.openSheet("recipe", { id: action.id });
        return { text: `Opened ${r.name}` };
      }
      case "cook_recipe": {
        const r = kitchen.k.book.find(x => x.id === action.id);
        if (!r) return null;
        const serves = (kitchen.k.diners && kitchen.k.diners.length) || r.serves;
        ui.closeChat();
        ui.startCooking(r, serves);
        return { text: `Cooking ${r.name}` };
      }
      case "open_featured": {
        const id = ui.featured || (kitchen.k.book[0] && kitchen.k.book[0].id);
        const r = id && kitchen.k.book.find(x => x.id === id);
        if (!r) return null;
        ui.closeChat();
        ui.openSheet("recipe", { id: r.id });
        return { text: `Opened ${r.name}` };
      }
      case "set_anim": {
        kitchen.setPref("bgAnim", action.on);
        return { text: action.on ? "Animations on" : "Animations off" };
      }
      case "stop_timer": {
        ui.stopTimer();
        return { text: "Timer stopped" };
      }
      default:
        return null;
    }
  }, [kitchen, ui]);
}
