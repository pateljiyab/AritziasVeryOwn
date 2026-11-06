// What the stylist says. The API sends notes and outfits; the wording around them, the compact
// activity lines for button actions, and the choices offered in the chat all live here.

import { formatLimit, formatPrice, type Action, type Budget, type Outfit, type Slot } from "./api";

export const OPTIONAL_SLOTS: Slot[] = ["outerwear", "accessory"];

/** How to name what a slot holds: "the coat", "different shoes". */
export const SLOT_NOUN: Record<Slot, string> = {
  top: "top", bottom: "bottoms", dress: "dress", outerwear: "coat", shoes: "shoes", accessory: "accessory",
};
const SLOT_PLURAL: Record<Slot, string> = {
  top: "tops", bottom: "bottoms", dress: "dresses", outerwear: "coats", shoes: "shoes", accessory: "accessories",
};
export const leftOutLabel = (slot: Slot) => `No ${SLOT_PLURAL[slot]}`;

/** Colors offered when the user wants a piece in another color (tags from tagging/colors.py). */
export const COLORS = ["black", "white", "cream", "beige", "camel", "brown", "grey", "navy", "denim", "red", "pink", "green"];

const dollars = (amount: number) => formatLimit(amount, "USD");

/** "the Vortex Coat", but "The Super Puff" for a name that already starts with "The". */
export const the = (name: string) => (/^the\s/i.test(name) ? name : `the ${name}`);

/** "under $200 for the whole outfit", "nothing over $80 a piece", or both; "" for no budget. */
export function budgetWords(budget: { max_total?: number | null; max_item?: number | null }): string {
  const parts = [];
  if (budget.max_total != null) parts.push(`under ${dollars(budget.max_total)} for the whole outfit`);
  if (budget.max_item != null) parts.push(`nothing over ${dollars(budget.max_item)} a piece`);
  return parts.join(", and ");
}

/** What a turn did: a button's Action, or the intent a chat message was parsed into. */
export type Intent = {
  action: Action["action"];
  slot?: Slot | null;
  item_id?: string | null;
  formality?: number | null;
  vibe?: string | null;
  max_total?: number | null;
  max_item?: number | null;
  color?: string | null;
};

/** Actions that replace pieces, after which the chat offers to keep the rest. */
export const REPLACING = new Set<Intent["action"]>(["swap", "reject", "cheaper", "remove"]);

/** The stylist's reply to a typed message. */
export function replyText(notes: string[], hasOutfit: boolean, accepted: boolean, action?: Intent, name?: string): string {
  const lines = [...notes];
  if (accepted) lines.push("Great choice! Your final outfit is ready, with a link to every piece.");
  else if (hasOutfit) {
    const it = name ? the(name) : "that piece";
    switch (action?.action) {
      case undefined: lines.push("Here's a look for that. Lock the pieces you love, and swap or reject the rest."); break;
      case "lock": lines.push(`Locked ${it}. It stays through the next outfits.`); break;
      case "unlock": lines.push(`Unlocked ${it}.`); break;
      case "swap":
        lines.push(action.color === "any" ? "Any color goes again. Here's another option."
          : action.color ? `Here's one in ${action.color}. I'll stick to ${action.color} for that piece.`
          : "Here's a different option for that slot.");
        break;
      case "reject": lines.push(`Got it, I won't show ${it} again. Here's a replacement.`); break;
      case "remove":
        lines.push(name ? `Left out ${it}. I won't add one back unless you ask.`
          : `No ${action.slot ? SLOT_NOUN[action.slot] : "piece"} then. I'll leave it out unless you ask for one.`);
        break;
      case "nudge":
        lines.push(action.vibe ? `Leaning more ${action.vibe} now.`
          : action.formality === 1 ? "Dressed it up a notch." : "Made it a bit more relaxed.");
        break;
      case "cheaper":
        lines.push(action.slot || action.item_id ? `Here's something cheaper than ${it}.`
          : "Here's a cheaper version, and later outfits won't cost more than this.");
        break;
      case "budget": {
        if (notes.length) break; // the notes already say why the budget can't be met
        const words = budgetWords(action);
        lines.push(words ? `Got it: ${words}. Every outfit from here on keeps to that.`
          : "No budget now, so I'm choosing from everything again.");
        break;
      }
    }
  }
  if (lines.length === 0) lines.push("I couldn't build an outfit for that. Try describing the occasion differently.");
  return lines.join("\n\n");
}

/** Enough of an outfit to describe a change: a reloaded chat only knows the names on screen now. */
export interface Look {
  pieces: { slot: Slot; item_id: string; name?: string; price?: number }[];
  total_price?: number;
  currency?: string;
}

/** One line for a button action, said after it happened: "Swapped the X for the Y". */
export function activityText(action: Action, before: Look | null, after: Look | null): string {
  const old = before?.pieces.find((p) => ("item_id" in action && p.item_id === action.item_id)
    || ("slot" in action && p.slot === action.slot));
  const slot = old?.slot ?? ("slot" in action ? action.slot : undefined);
  const fresh = slot ? after?.pieces.find((p) => p.slot === slot) : undefined;
  const named = (p?: { name?: string }) => p?.name ? the(p.name) : `the ${slot ? SLOT_NOUN[slot] : "piece"}`;
  const money = (amount?: number) => amount == null ? "" : formatPrice(amount, after?.currency ?? "USD");
  let line: string;
  switch (action.action) {
    case "lock": line = `Locked ${named(old)}`; break;
    case "unlock": line = `Unlocked ${named(old)}`; break;
    case "remove": line = `Left out ${named(old)}`; break;
    case "reject":
      line = `Won't show ${named(old)} again. Here's ${named(fresh)}`
        + (action.color ? ` in ${action.color}` : "")
        + (action.cheaper && fresh?.price != null ? `, ${money(fresh.price)}` + (old?.price != null ? ` (was ${money(old.price)})` : "") : "");
      break;
    case "swap":
      if (!old) line = `Added ${named(fresh)}`;
      else if ("color" in action && action.color) line = `Swapped in ${action.color}: ${named(fresh)}`;
      else line = `Swapped ${named(old)} for ${named(fresh)}`;
      break;
    case "cheaper":
      line = "item_id" in action && action.item_id
        ? `Found a cheaper ${slot ? SLOT_NOUN[slot] : "piece"}: ${named(fresh)}${fresh?.price != null ? `, ${money(fresh.price)}` : ""}`
          + (old?.price != null ? ` (was ${money(old.price)})` : "")
        : `Made it cheaper${before?.total_price != null && after?.total_price != null
          ? `: ${money(before.total_price)} → ${money(after.total_price)}` : ""}`;
      break;
    case "nudge":
      line = action.vibe ? `Leaning more ${action.vibe}` : action.formality === 1 ? "Dressed it up a notch" : "Made it more relaxed";
      break;
    case "budget": {
      const words = budgetWords(action);
      line = words ? `Budget set: ${words}` : "Budget cleared";
      break;
    }
    case "accept": line = "Accepted the outfit"; break;
  }
  // Keeping to the budget can change more than the piece asked about; say so, so nothing changes silently.
  const others = changedSlots(before, after).filter((s) => s !== slot);
  if (others.length && action.action !== "nudge" && !(action.action === "cheaper" && !slot)) {
    line += ` (and changed the ${others.map((s) => SLOT_NOUN[s]).join(", ")} to fit)`;
  }
  return line;
}

/** Slots whose piece differs between two outfits (FR-UI-4). Nothing is "changed" on the first outfit. */
export function changedSlots(before: Look | null, after: Look | null): Slot[] {
  if (!before || !after) return [];
  const old = new Map(before.pieces.map((p) => [p.slot, p.item_id]));
  return after.pieces.filter((p) => old.get(p.slot) !== p.item_id).map((p) => p.slot);
}

/** A labelled action offered in the chat. */
export interface Choice {
  label: string;
  action: Action;
}

/** The answers to "what's not working about it?". Each rejects the piece for good, and the
 *  replacement fixes what was wrong. */
export function reasonsFor(piece: { item_id: string; slot: Slot }): { label: string; action?: Action }[] {
  return [
    { label: "Too expensive", action: { action: "reject", item_id: piece.item_id, cheaper: true } },
    { label: "Not my style", action: { action: "reject", item_id: piece.item_id } },
    { label: "Another colour" }, // opens the color choices
    ...(OPTIONAL_SLOTS.includes(piece.slot)
      ? [{ label: `I don't need ${piece.slot === "accessory" ? "one" : "a coat"}`, action: { action: "remove", item_id: piece.item_id } as Action }]
      : []),
  ];
}

/** "Your budget is $300 and this outfit is $321.50, so we need to save $21.50." */
export function overBudgetText(outfit: Outfit): string | null {
  const { budget, total_price: total, currency } = outfit;
  if (!outfit.over_budget) return null;
  const money = (amount: number) => formatPrice(amount, currency);
  if (budget?.max_total != null && total > budget.max_total) {
    return `Your budget is ${formatLimit(budget.max_total, currency)} and this outfit is ${money(total)}, `
      + `so we need to save another ${money(total - budget.max_total)}. Nothing in the catalog closes that gap `
      + "with the pieces as they are. Here's what we can do:";
  }
  return "One of these pieces is over your per-piece limit, and nothing cheaper fits. Here's what we can do:";
}

/** Ways out of a budget nothing fits, from the cheapest change to the biggest. */
export function recoveryFor(outfit: Outfit, budget: Budget | null): Choice[] {
  if (!outfit.over_budget) return [];
  const choices: Choice[] = [];
  const optional = outfit.pieces.filter((p) => OPTIONAL_SLOTS.includes(p.slot)).sort((a, b) => b.price - a.price);
  for (const p of optional) {
    choices.push({ label: `Leave out the ${SLOT_NOUN[p.slot]} (save ${formatPrice(p.price, p.currency)})`,
                   action: { action: "remove", item_id: p.item_id } });
  }
  // A cheaper piece in place of the priciest locked one (that also unlocks it).
  const locked = outfit.pieces.filter((p) => p.locked).sort((a, b) => b.price - a.price)[0];
  if (locked) choices.push({ label: `Find a cheaper ${SLOT_NOUN[locked.slot]} than the locked one`,
                             action: { action: "cheaper", item_id: locked.item_id } });
  if (budget?.max_total != null && outfit.total_price > budget.max_total) {
    const raised = Math.ceil(outfit.total_price / 10) * 10;
    choices.push({ label: `Raise my budget to ${formatLimit(raised, outfit.currency)}`,
                   action: { action: "budget", max_total: raised, max_item: budget.max_item } });
  }
  if (budget) choices.push({ label: "Forget the budget", action: { action: "budget" } });
  return choices;
}
