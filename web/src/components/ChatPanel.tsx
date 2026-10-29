import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Item } from "../api";
import styles from "./ChatPanel.module.css";

export interface ChatEntry {
  id: string;
  role: "user" | "stylist";
  text: string;
  error?: boolean;
  choices?: Record<string, Item[]>; // typed name -> close matches (FR-NAME-3)
  requestText?: string; // the message the choices came from
}

interface Props {
  entries: ChatEntry[];
  busy: boolean;
  ready: boolean;
  onSend: (text: string) => void;
  onChoose: (requestText: string, typed: string, item: Item) => void;
}

const EXAMPLES = [
  "rooftop birthday dinner, it might get chilly",
  "cozy fall Sunday, running errands",
  "something polished for a client meeting",
];

/** The conversation, oldest first; earlier turns stay visible (FR-UI-2). */
export function ChatPanel({ entries, busy, ready, onSend, onChoose }: Props) {
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const canSend = ready && !busy && draft.trim().length > 0;

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
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
    <section className={styles.panel}>
      <div className={styles.log} aria-live="polite">
        {entries.length === 0 && (
          <div className={styles.intro}>
            <p>Hi! What are you dressing for? Try one of these:</p>
            {EXAMPLES.map((ex) => (
              <button key={ex} className={styles.example} disabled={!ready || busy} onClick={() => onSend(ex)}>
                {ex}
              </button>
            ))}
          </div>
        )}
        {entries.map((entry) => (
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
          </div>
        ))}
        {busy && <div className={`${styles.bubble} ${styles.stylist} ${styles.typing}`}>Styling…</div>}
        <div ref={end} />
      </div>
      <form className={styles.composer} onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={'Describe the occasion, or say "dressier" or "swap the shoes"'}
          maxLength={500}
          rows={2}
          aria-label="Message"
        />
        <button type="submit" disabled={!canSend}>Send</button>
      </form>
    </section>
  );
}
