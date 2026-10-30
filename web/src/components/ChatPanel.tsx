import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Action, Budget, Item, Outfit, Slot } from "../api";
import { COLORS, leftOutLabel, reasonsFor, the, type Choice } from "../chat";
import styles from "./ChatPanel.module.css";

export interface ChatEntry {
  id: string;
  role: "user" | "stylist" | "activity"; // activity: one compact line for a button action
  text: string;
  error?: boolean;
  choices?: Record<string, Item[]>; // typed name -> close matches (FR-NAME-3)
  requestText?: string; // the message the choices came from
  ask?: { item_id: string; slot: Slot; colors: string[]; answer?: string }; // "what's not working about it?"
  keep?: Slot[]; // pieces to offer to keep (lock), after the others changed
  offers?: Choice[]; // ways forward, e.g. out of a budget nothing fits
}

interface Props {
  entries: ChatEntry[];
  outfit: Outfit | null;
  budget: Budget | null;
  omitted: Slot[];
  busy: boolean;
  ready: boolean;
  accepted: boolean;
  onSend: (text: string) => void;
  onChoose: (requestText: string, typed: string, item: Item) => void;
  onStartOver: () => void;
  onAction: (action: Action, quiet?: boolean) => void; // quiet: the control itself shows the result
  onAnswer: (entryId: string, answer: string, action: Action) => void;
}

const EXAMPLES = [
  "rooftop birthday dinner, it might get chilly",
  "cozy fall Sunday, running errands",
  "something polished for a client meeting",
];

function LockIcon() {
  return (
    <svg className={styles.icon} viewBox="0 0 16 16" aria-hidden="true">
      <rect x="3" y="7" width="10" height="7" rx="1.5" fill="currentColor" />
      <path d="M5 7V5a3 3 0 0 1 6 0v2" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

/** What the stylist is keeping to, editable here: the budget, the locked pieces, the pieces left out.
 *  The same state the cards change, so the chat and the outfit never disagree. */
function StateBar({ outfit, budget, omitted, disabled, onAction }: {
  outfit: Outfit | null; budget: Budget | null; omitted: Slot[]; disabled: boolean;
  onAction: Props["onAction"];
}) {
  const current = budget?.max_total != null ? String(budget.max_total) : "";
  const [draft, setDraft] = useState(current);
  useEffect(() => setDraft(current), [current]);
  const amount = Number(draft);
  const locked = outfit?.pieces.filter((p) => p.locked) ?? [];

  function submit(e: FormEvent) {
    e.preventDefault();
    if (amount > 0 && draft !== current) onAction({ action: "budget", max_total: amount, max_item: budget?.max_item ?? null });
  }

  return (
    <div className={styles.state}>
      <form className={styles.budget} onSubmit={submit}>
        <label htmlFor="max-budget">Maximum budget <span className={styles.muted}>(USD)</span></label>
        <div className={styles.budgetField}>
          <span aria-hidden="true">$</span>
          <input
            id="max-budget"
            type="number"
            min="1"
            step="any"
            inputMode="decimal"
            placeholder="No limit"
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
          />
          {draft !== current && amount > 0 ? (
            <button type="submit" disabled={disabled}>Set</button>
          ) : budget ? (
            <button type="button" disabled={disabled} onClick={() => onAction({ action: "budget" })}>Clear</button>
          ) : null}
        </div>
      </form>
      {budget?.max_item != null && <p className={styles.small}>Nothing over ${budget.max_item} a piece</p>}
      {(locked.length > 0 || omitted.length > 0) && (
        <ul className={styles.tags} aria-label="Kept through every outfit">
          {locked.map((p) => (
            <li key={p.item_id}>
              <button className={styles.tag} disabled={disabled} title={`Unlock the ${p.name}`}
                      onClick={() => onAction({ action: "unlock", item_id: p.item_id }, true)}>
                <LockIcon /> <span className={styles.tagName}>{p.name}</span> <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
          {omitted.map((slot) => (
            <li key={slot}>
              <button className={styles.tag} disabled={disabled} title="Add one back"
                      onClick={() => onAction({ action: "swap", slot })}>
                {leftOutLabel(slot)} <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** "Keep the black dress locked" checkboxes, bound to the live lock state. */
function KeepToggles({ slots, outfit, disabled, onAction }: {
  slots: Slot[]; outfit: Outfit | null; disabled: boolean; onAction: Props["onAction"];
}) {
  const pieces = outfit?.pieces.filter((p) => slots.includes(p.slot)) ?? [];
  if (pieces.length === 0) return null;
  return (
    <div className={styles.keep}>
      {pieces.map((p) => (
        <label key={p.item_id} className={styles.check}>
          <input type="checkbox" checked={p.locked} disabled={disabled}
                 onChange={() => onAction({ action: p.locked ? "unlock" : "lock", item_id: p.item_id }, true)} />
          Keep {the(p.name)} locked
        </label>
      ))}
    </div>
  );
}

/** The answers to "what's not working about it?". */
function Reasons({ entry, live, disabled, onAnswer }: {
  entry: ChatEntry; live: boolean; disabled: boolean; onAnswer: Props["onAnswer"];
}) {
  const [picking, setPicking] = useState(false);
  const ask = entry.ask!;
  if (ask.answer) return <p className={styles.answer}>{ask.answer}</p>;
  if (!live) return null;
  if (picking) {
    return (
      <div className={styles.chips} role="group" aria-label="Pick a colour">
        {COLORS.filter((c) => !ask.colors.includes(c)).map((color) => (
          <button key={color} className={styles.chip} disabled={disabled}
                  onClick={() => onAnswer(entry.id, `In ${color}, please`, { action: "reject", item_id: ask.item_id, color })}>
            {color}
          </button>
        ))}
        <button className={styles.chip} onClick={() => setPicking(false)}>Back</button>
      </div>
    );
  }
  return (
    <div className={styles.chips}>
      {reasonsFor(ask).map(({ label, action }) => (
        <button key={label} className={styles.chip} disabled={disabled}
                onClick={() => (action ? onAnswer(entry.id, label, action) : setPicking(true))}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** The conversation, oldest first; earlier turns stay visible (FR-UI-2). Only the newest entry's
 *  controls are live, so an old message can't act on an outfit that has moved on. */
export function ChatPanel(props: Props) {
  const { entries, outfit, budget, omitted, busy, ready, accepted, onSend, onChoose, onStartOver, onAction, onAnswer } = props;
  const [draft, setDraft] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const canSend = ready && !busy && draft.trim().length > 0;

  // Scroll the log itself, not the page: scrollIntoView would move the whole studio.
  useEffect(() => {
    const el = log.current;
    el?.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [entries.length, busy]);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSend) return;
    onSend(draft.trim());
    setDraft("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) submit(e);
  }

  return (
    <section id="stylist-chat" className={styles.panel}>
      <header className={styles.header}>
        <h2 className={styles.heading}>Your stylist</h2>
        <button className={styles.startOver} onClick={onStartOver} disabled={busy || !ready}>
          Start over
        </button>
      </header>
      <StateBar outfit={outfit} budget={budget} omitted={omitted} disabled={busy || !ready || accepted}
                onAction={onAction} />
      <div ref={log} className={styles.log} aria-live="polite">
        {entries.length === 0 && (
          <div className={styles.intro}>
            <p>Hi! What are you dressing for? Set a budget above if you have one, or try:</p>
            {EXAMPLES.map((ex) => (
              <button key={ex} className={styles.example} disabled={!ready || busy} onClick={() => onSend(ex)}>
                {ex}
              </button>
            ))}
          </div>
        )}
        {entries.map((entry, i) => {
          // Controls stay on screen while a turn runs (disabled), so they don't flicker.
          const live = i === entries.length - 1 && !accepted;
          const controls = (
            <>
              {entry.ask && <Reasons entry={entry} live={live} disabled={busy} onAnswer={onAnswer} />}
              {live && entry.offers && entry.offers.length > 0 && (
                <div className={styles.chips}>
                  {entry.offers.map((o) => (
                    <button key={o.label} className={styles.chip} disabled={busy} onClick={() => onAction(o.action)}>{o.label}</button>
                  ))}
                </div>
              )}
              {live && entry.keep && <KeepToggles slots={entry.keep} outfit={outfit} disabled={busy} onAction={onAction} />}
            </>
          );
          if (entry.role === "activity") {
            return (
              <div key={entry.id} className={styles.activityRow}>
                <p className={styles.activity}>{entry.text}</p>
                {controls}
              </div>
            );
          }
          return (
            <div key={entry.id} className={`${styles.bubble} ${styles[entry.role]} ${entry.error ? styles.error : ""}`}>
              {entry.text}
              {entry.choices && entry.requestText && (
                <div className={styles.choices}>
                  {Object.entries(entry.choices).map(([typed, items]) => (
                    <div key={typed}>
                      <p className={styles.choiceLabel}>Did you mean, for "{typed}":</p>
                      {items.map((item) => (
                        <button
                          key={item.item_id}
                          className={styles.choice}
                          disabled={busy}
                          onClick={() => onChoose(entry.requestText!, typed, item)}
                        >
                          <img src={item.image_url} alt="" referrerPolicy="no-referrer" />
                          <span>{item.name}</span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
              {controls}
            </div>
          );
        })}
        {busy && <div className={`${styles.bubble} ${styles.stylist} ${styles.typing}`}>Styling…</div>}
      </div>
      <form className={styles.composer} onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={'Describe the occasion, or say "cheaper shoes" or "no bag"'}
          maxLength={500}
          rows={3}
          aria-label="Message"
        />
        <button type="submit" disabled={!canSend}>Send</button>
      </form>
    </section>
  );
}
