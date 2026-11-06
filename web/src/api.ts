// Typed client for /api/v1. The types mirror backend/src/stylist/api/schemas.py;
// http://localhost:8000/docs is the source of truth if they drift.

export const API_BASE = (import.meta.env.VITE_API_BASE ?? "http://localhost:8000").replace(/\/$/, "");

export type Slot = "top" | "bottom" | "dress" | "outerwear" | "shoes" | "accessory";
export const VIBES = [
  "cozy", "polished", "going-out", "cute", "off-duty", "chic", "athleisure", "minimal", "romantic", "edgy",
] as const;
export type Vibe = (typeof VIBES)[number];

export interface Item {
  item_id: string;
  slot: Slot;
  name: string;
  brand_line: string;
  price: number;
  currency: string;
  image_url: string;
  product_url: string;
  vibes: string[];
  formality: number | null;
  colors: string[];
}

export interface Piece extends Item {
  locked: boolean;
}

export interface Outfit {
  pieces: Piece[]; // in slot order: top, bottom, dress, outerwear, shoes, accessory
  score: number;
  breakdown: Record<string, number>; // score term -> value in [0, 1]
  total_price: number;
  currency: string;
  budget: Budget | null; // the session's budget, if it has one
  over_budget: boolean; // nothing fit the budget, so this is the closest outfit
}

export interface Budget {
  max_total: number | null; // the whole outfit
  max_item: number | null; // each piece
}

export interface Summary {
  pieces: { slot: Slot; name: string; brand_line: string; price: number; product_url: string }[];
  total_price: number;
  currency: string;
}

export interface Turn {
  session_id: string;
  outfit: Outfit | null;
  notes: string[];
  choices: Record<string, Item[]>; // typed name -> close matches
  accepted: boolean;
  summary: Summary | null;
  parsed: Record<string, unknown> | null;
  error: string | null; // a message's change couldn't be applied; the outfit is unchanged
  budget: Budget | null; // the session's budget, also before there's an outfit
  omitted: Slot[]; // optional pieces the user asked to leave out
}

export interface TurnSummary {
  kind: "message" | "action";
  input: Record<string, unknown>;
  parsed: Record<string, unknown> | null; // what a message was understood as
  error: string | null;
  outfit: Record<string, string> | null; // slot -> item_id
  notes: string[];
  latency_ms: number;
}

export interface Session {
  session_id: string;
  outfit: Outfit | null;
  accepted: boolean;
  budget: Budget | null;
  omitted: Slot[];
  turns: TurnSummary[];
}

/** A button click; mirrors FeedbackIntent. */
export type Action =
  | { action: "lock" | "unlock"; item_id: string }
  | { action: "reject"; item_id: string; cheaper?: boolean; color?: string } // never again; what to fix in the replacement
  | { action: "swap"; item_id: string; color?: string } // a color keeps that piece to it
  | { action: "swap"; slot: Slot } // a piece the outfit doesn't have yet ("add a bag")
  | { action: "remove"; item_id: string } // leave an optional piece out from now on
  | { action: "nudge"; formality?: -1 | 1; vibe?: Vibe }
  | { action: "accept" }
  | { action: "cheaper"; item_id?: string } // one piece, or the whole outfit
  | { action: "budget"; max_total?: number | null; max_item?: number | null }; // neither clears it

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/v1${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, `Can't reach the stylist API at ${API_BASE}. Is it running?`);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? `Request failed (${res.status}).`);
  return body as T;
}

export const api = {
  createSession: () => request<{ session_id: string }>("/sessions", { method: "POST" }),
  getSession: (id: string) => request<Session>(`/sessions/${id}`),
  sendMessage: (id: string, text: string) =>
    request<Turn>(`/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  sendAction: (id: string, action: Action) =>
    request<Turn>(`/sessions/${id}/actions`, { method: "POST", body: JSON.stringify(action) }),
};

export function formatPrice(amount: number, currency: string): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
}

/** A budget limit: "$200", or "$199.50" when it has cents. */
export function formatLimit(amount: number, currency: string): string {
  const cents = Number.isInteger(amount) ? 0 : 2;
  return new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: cents }).format(amount);
}
