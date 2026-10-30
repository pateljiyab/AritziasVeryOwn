import { useEffect, useRef, useState } from "react";
import { api, ApiError, formatLimit, type Action, type Item, type Outfit, type Session, type Slot, type Turn } from "./api";
import { ChatPanel, type ChatEntry } from "./components/ChatPanel";
import { OutfitPanel } from "./components/OutfitPanel";
import styles from "./App.module.css";

const SESSION_KEY = "stylist.session";

function readStoredSession(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY);
  } catch {
    return null;
  }
}

function storeSession(id: string) {
  try {
    localStorage.setItem(SESSION_KEY, id);
  } catch {
    // Private windows can block storage; the session just won't survive a reload.
  }
}

/** Slots whose piece differs from the previous outfit (FR-UI-4). Nothing is "changed" on the first outfit. */
function changedSlots(before: Outfit | null, after: Outfit): Set<Slot> {
  if (!before) return new Set();
  const old = new Map(before.pieces.map((p) => [p.slot, p.item_id]));
  return new Set(after.pieces.filter((p) => old.get(p.slot) !== p.item_id).map((p) => p.slot));
}

const dollars = (amount: number) => formatLimit(amount, "USD");

/** "under $200 for the whole outfit", "nothing over $80 a piece", or both; "" for no budget. */
function budgetWords(intent: { max_total?: number | null; max_item?: number | null }): string {
  const parts = [];
  if (intent.max_total != null) parts.push(`under ${dollars(intent.max_total)} for the whole outfit`);
  if (intent.max_item != null) parts.push(`nothing over ${dollars(intent.max_item)} a piece`);
  return parts.join(", and ");
}

/** What the user did, as a chat line. */
function describeAction(action: Action, name?: string): string {
  const it = name ?? "this piece";
  switch (action.action) {
    case "lock": return `Keep the ${it}`;
    case "unlock": return `Unlock the ${it}`;
    case "swap": return `Show me a different ${it}`;
    case "reject": return `Not the ${it}`;
    case "accept": return "I'll take this outfit";
    case "cheaper": return action.item_id ? `Find something cheaper than the ${it}` : "Make it cheaper";
    case "budget": {
      const words = budgetWords(action);
      return words ? `Keep it ${words}` : "Forget the budget";
    }
    case "nudge":
      if (action.vibe) return `Make it more ${action.vibe}`;
      return action.formality === 1 ? "Dress it up" : "Make it more relaxed";
  }
}

/** What a turn did: a button's Action, or the intent a chat message was parsed into. */
type Intent = {
  action: Action["action"];
  slot?: Slot | null;
  item_id?: string | null;
  formality?: number | null;
  vibe?: string | null;
  max_total?: number | null;
  max_item?: number | null;
  color?: string | null;
};

/** The stylist's reply line. The API sends notes and an outfit; the wording around them lives here. */
function replyText(notes: string[], hasOutfit: boolean, accepted: boolean, action?: Intent, name?: string): string {
  const lines = [...notes];
  if (accepted) lines.push("Great choice! Your final outfit is ready, with a link to every piece.");
  else if (hasOutfit) {
    const it = name ?? "that piece";
    switch (action?.action) {
      case undefined: lines.push("Here's a look for that. Lock the pieces you love, and swap or reject the rest."); break;
      case "lock": lines.push(`Locked the ${it}. It stays through the next outfits.`); break;
      case "unlock": lines.push(`Unlocked the ${it}.`); break;
      case "swap":
        lines.push(action.color === "any" ? "Any color goes again. Here's another option."
          : action.color ? `Here's one in ${action.color}. I'll stick to ${action.color} for that piece.`
          : "Here's a different option for that slot.");
        break;
      case "reject": lines.push(`Got it, I won't show the ${it} again. Here's a replacement.`); break;
      case "nudge":
        lines.push(action.vibe ? `Leaning more ${action.vibe} now.`
          : action.formality === 1 ? "Dressed it up a notch." : "Made it a bit more relaxed.");
        break;
      case "cheaper":
        lines.push(action.slot || action.item_id ? `Here's something cheaper than the ${it}.`
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

/** Rebuild the chat from a saved session, so a reload keeps the conversation. */
function historyOf(session: Session): ChatEntry[] {
  const names = new Map(session.outfit?.pieces.map((p) => [p.item_id, p.name]) ?? []);
  return session.turns.flatMap((turn, i): ChatEntry[] => {
    const action = turn.kind === "action" ? (turn.input as unknown as Action) : undefined;
    const name = action && "item_id" in action && action.item_id ? names.get(action.item_id) : undefined;
    const said = action ? describeAction(action, name) : String(turn.input.text ?? "");
    // A typed message read as feedback ("keep it under $200") gets the same reply wording as the button.
    const intent = action ?? (turn.parsed && "action" in turn.parsed ? (turn.parsed as Intent) : undefined);
    const accepted = intent?.action === "accept";
    return [
      { id: `${i}u`, role: "user", text: said },
      { id: `${i}s`, role: "stylist", text: replyText(turn.notes, turn.outfit !== null && !turn.error, accepted, intent, name) },
    ];
  });
}

let nextId = 0;
const entryId = () => `e${nextId++}`;

/** The twelve-spoke mark beside the tagline. */
function Asterisk({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 60 60" aria-hidden="true">
      <g transform="translate(30 30)">
        {[0, 30, 60, 90, 120, 150].map((deg) => (
          <rect key={deg} x={-5} y={-29} width={10} height={58} rx={3} transform={`rotate(${deg})`} />
        ))}
      </g>
    </svg>
  );
}

/** Matches the hero's fade-out transition in App.module.css. */
const HERO_FADE_MS = 600;

/** The first screen. "Create Your Vision" fades it out and swaps in the studio. */
function Hero({ leaving, onStart }: { leaving: boolean; onStart: () => void }) {
  return (
    <header className={`${styles.hero} ${leaving ? styles.leaving : ""}`}>
      <div className={styles.heroInner}>
        <h1 className={styles.title} aria-label="Aritzia's Very Own">
          <span className={styles.line1} aria-hidden="true">
            <span className={styles.script}>A</span>RITZIA’S
          </span>
          <span className={styles.line2} aria-hidden="true">
            VERY<span className={styles.script}>O</span>WN
          </span>
        </h1>
        <hr className={styles.rule} />
        <div className={styles.pitch}>
          <p className={styles.tagline}>
            <Asterisk className={styles.asterisk} />
            <span><strong>Describe a vibe,</strong><br />Discover a look, fit &amp; empower.</span>
          </p>
          <button className={styles.cta} disabled={leaving} onClick={onStart}>
            Create Your Vision
            <svg viewBox="0 0 40 12" aria-hidden="true">
              <path d="M0 6h38M32 1l6 5-6 5" fill="none" stroke="currentColor" strokeWidth="1.3" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [outfit, setOutfit] = useState<Outfit | null>(null);
  const [changed, setChanged] = useState<Set<Slot>>(new Set());
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [startupError, setStartupError] = useState<string | null>(null);
  const started = useRef(false);
  const [stage, setStage] = useState<"hero" | "leaving" | "studio">("hero");
  const studio = useRef<HTMLElement>(null);

  function enterStudio() {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setStage("leaving");
    setTimeout(() => setStage("studio"), reduced ? 0 : HERO_FADE_MS);
  }

  // The studio replaces the hero, so start at its top and put focus in it rather than on the removed button.
  useEffect(() => {
    if (stage !== "studio") return;
    window.scrollTo(0, 0);
    studio.current?.focus();
  }, [stage]);

  async function newSession(): Promise<string> {
    const { session_id } = await api.createSession();
    storeSession(session_id);
    setSessionId(session_id);
    return session_id;
  }

  async function startUp() {
    setStartupError(null);
    try {
      const stored = readStoredSession();
      if (stored) {
        try {
          const session = await api.getSession(stored);
          setSessionId(stored);
          setEntries(historyOf(session));
          setOutfit(session.outfit);
          setAccepted(session.accepted);
          return;
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 404)) throw e;
        }
      }
      await newSession();
    } catch (e) {
      setStartupError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice in dev; don't create two sessions
    started.current = true;
    void startUp();
  }, []);

  const say = (role: ChatEntry["role"], text: string, extra?: Partial<ChatEntry>) =>
    setEntries((all) => [...all, { id: entryId(), role, text, ...extra }]);

  /** Run one turn. If the session vanished (sessions.db was reset), start a new one and retry once. */
  async function runTurn(call: (id: string) => Promise<Turn>): Promise<Turn | null> {
    if (!sessionId) return null;
    setBusy(true);
    try {
      try {
        return await call(sessionId);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404 && e.message.startsWith("No session")) {
          return await call(await newSession());
        }
        throw e;
      }
    } catch (e) {
      say("stylist", e instanceof Error ? e.message : String(e), { error: true });
      return null;
    } finally {
      setBusy(false);
    }
  }

  function show(turn: Turn) {
    if (turn.outfit) {
      setChanged(changedSlots(outfit, turn.outfit));
      setOutfit(turn.outfit);
    }
    setAccepted(turn.accepted);
  }

  async function sendMessage(text: string) {
    say("user", text);
    const turn = await runTurn((id) => api.sendMessage(id, text));
    if (!turn) return;
    // A message like "swap the shoes" is parsed into the same intent a button sends; word the reply to match.
    const intent = turn.parsed && "action" in turn.parsed ? (turn.parsed as Intent) : undefined;
    const name = intent?.slot ? outfit?.pieces.find((p) => p.slot === intent.slot)?.name : undefined;
    show(turn);
    const hasChoices = Object.keys(turn.choices).length > 0;
    // A change that couldn't be applied: the note explains, and nothing changed to describe.
    say("stylist", replyText(turn.notes, turn.outfit !== null && !turn.error, turn.accepted, intent, name),
      hasChoices ? { choices: turn.choices, requestText: text } : undefined);
  }

  async function sendAction(action: Action) {
    const name = "item_id" in action ? outfit?.pieces.find((p) => p.item_id === action.item_id)?.name : undefined;
    say("user", describeAction(action, name));
    const turn = await runTurn((id) => api.sendAction(id, action));
    if (!turn) return;
    show(turn);
    say("stylist", replyText(turn.notes, turn.outfit !== null, turn.accepted, action, name));
  }

  /** Picking one of several close name matches (FR-NAME-3) re-sends the request with the full name. */
  function choose(requestText: string, typed: string, item: Item) {
    const at = requestText.toLowerCase().indexOf(typed.toLowerCase());
    const text = at >= 0
      ? requestText.slice(0, at) + item.name + requestText.slice(at + typed.length)
      : `${requestText} with the ${item.name}`;
    void sendMessage(text);
  }

  async function startOver() {
    setBusy(true);
    try {
      await newSession();
      setEntries([]);
      setOutfit(null);
      setChanged(new Set());
      setAccepted(false);
    } catch (e) {
      say("stylist", e instanceof Error ? e.message : String(e), { error: true });
    } finally {
      setBusy(false);
    }
  }

  return (
    stage !== "studio" ? (
      <Hero leaving={stage === "leaving"} onStart={enterStudio} />
    ) : (
      <main ref={studio} className={styles.studio} tabIndex={-1}>
        {startupError ? (
          <div className={styles.startupError} role="alert">
            <p>{startupError}</p>
            <button onClick={startUp}>Try again</button>
          </div>
        ) : (
          <div className={styles.panels}>
            <ChatPanel
              entries={entries}
              busy={busy}
              ready={sessionId !== null}
              onSend={sendMessage}
              onChoose={choose}
              onStartOver={startOver}
            />
            <OutfitPanel
              outfit={outfit}
              changed={changed}
              accepted={accepted}
              busy={busy}
              onAction={sendAction}
            />
          </div>
        )}
      </main>
    )
  );
}
