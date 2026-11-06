import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Action, type Budget, type Item, type Outfit, type Piece, type Session, type Slot, type Turn } from "./api";
import {
  activityText, changedSlots, overBudgetText, recoveryFor, replyText, REPLACING, the, type Intent, type Look,
} from "./chat";
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

/** Notes the chat words itself: the over-budget explanation (with numbers and ways out) and the
 *  confirmation of a budget the activity line already shows. */
function ownNotes(notes: string[], action?: Intent): string[] {
  return notes.filter((n) => !n.startsWith("Nothing fits")
    && !(action?.action === "budget" && (n.startsWith("Got it:") || n === "Budget cleared.")));
}

/** Rebuild the chat from a saved session, so a reload keeps the conversation. Only the names of
 *  the pieces on screen now are known, so older activity lines name slots instead. */
function historyOf(session: Session): ChatEntry[] {
  const names = new Map(session.outfit?.pieces.map((p) => [p.item_id, p.name]) ?? []);
  const look = (shown: Record<string, string> | null): Look | null => shown && {
    pieces: Object.entries(shown).map(([slot, item_id]) => ({ slot: slot as Slot, item_id, name: names.get(item_id) })),
  };
  let before: Look | null = null;
  return session.turns.flatMap((turn, i): ChatEntry[] => {
    const after = look(turn.outfit);
    const entries: ChatEntry[] = [];
    if (turn.kind === "action") {
      const action = turn.input as unknown as Action;
      if (action.action === "accept") {
        entries.push({ id: `${i}s`, role: "stylist", text: replyText(turn.notes, true, true) });
      } else if (!turn.error) {
        entries.push({ id: `${i}a`, role: "activity", text: activityText(action, before, after ?? before) });
        const notes = turn.notes.filter((n) => !(action.action === "budget" && (n.startsWith("Got it:") || n === "Budget cleared.")));
        if (notes.length) entries.push({ id: `${i}s`, role: "stylist", text: notes.join("\n\n") });
      }
    } else {
      // A typed message read as feedback ("keep it under $200") gets the same reply wording as the button.
      const intent = turn.parsed && "action" in turn.parsed ? (turn.parsed as Intent) : undefined;
      const name = intent?.slot && before ? before.pieces.find((p) => p.slot === intent.slot)?.name : undefined;
      entries.push(
        { id: `${i}u`, role: "user", text: String(turn.input.text ?? "") },
        { id: `${i}s`, role: "stylist",
          text: replyText(turn.notes, turn.outfit !== null && !turn.error, intent?.action === "accept", intent, name) },
      );
    }
    if (after) before = after;
    return entries;
  });
}

/** The piece an action is about, in the outfit it was taken on. */
function targetOf(action: Action | Intent, outfit: Outfit | null): Piece | undefined {
  if ("item_id" in action && action.item_id) return outfit?.pieces.find((p) => p.item_id === action.item_id);
  if ("slot" in action && action.slot) return outfit?.pieces.find((p) => p.slot === action.slot);
  return undefined;
}

/** After a piece is replaced, offer to keep the rest: "I like everything except the jacket". */
function keepAfter(action: Action | Intent, before: Outfit | null, after: Outfit | null): Slot[] | undefined {
  // Only when one piece was replaced: not a cheaper outfit overall, or leaving out a piece that wasn't there.
  if (!after || !REPLACING.has(action.action) || (action.action !== "swap" && !targetOf(action, before))) return undefined;
  const changed = new Set(changedSlots(before, after));
  const target = targetOf(action, before)?.slot;
  const rest = after.pieces.filter((p) => p.slot !== target && !changed.has(p.slot)).map((p) => p.slot);
  return rest.length ? rest : undefined;
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

// Shown on both screens: the site uses the Aritzia name and product photos, so say plainly it isn't Aritzia's.
const DISCLAIMER = "Independent student project, not affiliated with or endorsed by Aritzia.";

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
        <p className={styles.disclaimer}>{DISCLAIMER}</p>
      </div>
    </header>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  // One outfit state. The cards, the chat's state bar and the chat's own controls all change it
  // through the same API actions, so they can't disagree.
  const [outfit, setOutfit] = useState<Outfit | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [omitted, setOmitted] = useState<Slot[]>([]);
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
          setBudget(session.budget);
          setOmitted(session.omitted);
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

  const entriesNow = useRef(entries);
  entriesNow.current = entries;

  function add(...added: Omit<ChatEntry, "id">[]) {
    setEntries((all) => [...all, ...added.map((e) => ({ ...e, id: entryId() }))]);
  }

  /** Notes the stylist's last message already said, word for word, aren't said again. */
  function unsaid(notes: string[]): string[] {
    const last = [...entriesNow.current].reverse().find((e) => e.role === "stylist" && !e.error)?.text ?? "";
    return notes.filter((note) => !last.includes(note));
  }

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
      // A 409 is the stylist explaining why it can't do that (over budget, nothing cheaper): a reply, not a failure.
      const explained = e instanceof ApiError && e.status === 409;
      add({ role: "stylist", text: e instanceof Error ? e.message : String(e), error: !explained });
      return null;
    } finally {
      setBusy(false);
    }
  }

  function show(turn: Turn) {
    if (turn.outfit) {
      setChanged(new Set(changedSlots(outfit, turn.outfit)));
      setOutfit(turn.outfit);
    }
    setBudget(turn.budget);
    setOmitted(turn.omitted);
    setAccepted(turn.accepted);
  }

  /** The stylist's follow-up: what the notes say, and when the budget can't be met, by how much
   *  and the ways out, once, instead of the same note every turn. */
  function followUp(turn: Turn, lines: string[], keep?: Slot[]): Omit<ChatEntry, "id"> {
    const over = turn.outfit && !turn.accepted ? overBudgetText(turn.outfit) : null;
    return {
      role: "stylist",
      text: [...lines, ...unsaid(over ? [over] : [])].join("\n\n"),
      offers: turn.outfit && !turn.accepted ? recoveryFor(turn.outfit, turn.budget) : undefined,
      keep,
    };
  }

  async function sendMessage(text: string) {
    add({ role: "user", text });
    const before = outfit;
    const turn = await runTurn((id) => api.sendMessage(id, text));
    if (!turn) return;
    // A message like "swap the shoes" is parsed into the same intent a button sends; word the reply to match.
    const intent = turn.parsed && "action" in turn.parsed ? (turn.parsed as Intent) : undefined;
    show(turn);
    const hasChoices = Object.keys(turn.choices).length > 0;
    // A change that couldn't be applied: the note explains, and nothing changed to describe.
    const reply = replyText(unsaid(ownNotes(turn.notes, intent)), turn.outfit !== null && !turn.error, turn.accepted, intent,
                            intent && targetOf(intent, before)?.name);
    const next = followUp(turn, [reply], intent && !turn.error ? keepAfter(intent, before, turn.outfit) : undefined);
    add({ ...next, ...(hasChoices ? { choices: turn.choices, requestText: text } : {}) });
  }

  /** A button or chat control. Routine actions show as one activity line; `quiet` shows nothing,
   *  for controls that show the new state themselves (a lock checkbox, the state bar's tags). */
  async function sendAction(action: Action, quiet = false) {
    const before = outfit;
    const turn = await runTurn((id) => api.sendAction(id, action));
    if (!turn) return;
    show(turn);
    if (action.action === "accept") {
      add({ role: "stylist", text: replyText(turn.notes, true, true) });
      return;
    }
    const notes = unsaid(ownNotes(turn.notes, action));
    const keep = keepAfter(action, before, turn.outfit);
    const added: Omit<ChatEntry, "id">[] = [];
    if (!quiet) added.push({ role: "activity", text: activityText(action, before, turn.outfit ?? before) });
    const next = followUp(turn, notes);
    if (next.text || next.offers?.length) added.push(next);
    if (keep && added.length) added[added.length - 1].keep = keep; // the newest entry holds the live controls
    add(...added);
  }

  /** Reject asks why first, so the replacement can fix what was wrong (too pricey, wrong colour, ...). */
  function askWhy(piece: Piece) {
    add({ role: "stylist", text: `What's not working about ${the(piece.name)}?`,
          ask: { item_id: piece.item_id, slot: piece.slot, colors: piece.colors } });
    if (window.matchMedia("(max-width: 900px)").matches) {
      document.getElementById("stylist-chat")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function answer(entryId: string, label: string, action: Action) {
    setEntries((all) => all.map((e) => e.id === entryId && e.ask ? { ...e, ask: { ...e.ask, answer: label } } : e));
    void sendAction(action);
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
      setBudget(null);
      setOmitted([]);
      setChanged(new Set());
      setAccepted(false);
    } catch (e) {
      add({ role: "stylist", text: e instanceof Error ? e.message : String(e), error: true });
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
              outfit={outfit}
              budget={budget}
              omitted={omitted}
              busy={busy}
              ready={sessionId !== null}
              accepted={accepted}
              onSend={sendMessage}
              onChoose={choose}
              onStartOver={startOver}
              onAction={sendAction}
              onAnswer={answer}
            />
            <OutfitPanel
              outfit={outfit}
              changed={changed}
              accepted={accepted}
              busy={busy}
              onAction={sendAction}
              onReject={askWhy}
            />
          </div>
        )}
        <footer className={styles.footer}>
          {DISCLAIMER} Product data from the Channel3 product API; product links go to the retailer.
        </footer>
      </main>
    )
  );
}
