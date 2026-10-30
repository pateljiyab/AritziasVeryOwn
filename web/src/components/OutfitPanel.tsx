import { useState, type FormEvent } from "react";
import { formatLimit, formatPrice, VIBES, type Action, type Outfit, type Slot, type Vibe } from "../api";
import { OutfitCard } from "./OutfitCard";
import styles from "./OutfitPanel.module.css";

interface Props {
  outfit: Outfit | null;
  changed: Set<Slot>;
  accepted: boolean;
  busy: boolean;
  onAction: (action: Action) => void;
}

/** "Under $200", "Under $200 · over by $40", "$80 max a piece". */
function budgetText(outfit: Outfit): string {
  const { budget, total_price: total, currency, over_budget: over } = outfit;
  const money = (amount: number) => formatPrice(amount, currency);
  const limit = (amount: number) => formatLimit(amount, currency);
  const parts: string[] = [];
  if (budget?.max_total != null) {
    parts.push(`Under ${limit(budget.max_total)}`);
    if (total > budget.max_total) parts.push(`over by ${money(total - budget.max_total)}`);
  }
  if (budget?.max_item != null) parts.push(`${limit(budget.max_item)} max a piece`);
  if (over && budget?.max_total == null) parts.push("a piece is over");
  return parts.join(" · ");
}

/** The outfit on screen: score, cards, outfit-wide nudges, and the summary once accepted. */
export function OutfitPanel({ outfit, changed, accepted, busy, onAction }: Props) {
  const [vibe, setVibe] = useState<Vibe>(VIBES[0]);
  const [budgetDraft, setBudgetDraft] = useState("");
  const draftAmount = Number(budgetDraft);

  function setBudget(e: FormEvent) {
    e.preventDefault();
    if (!(draftAmount > 0)) return;
    onAction({ action: "budget", max_total: draftAmount, max_item: outfit?.budget?.max_item ?? null });
    setBudgetDraft("");
  }

  if (!outfit) {
    return (
      <section className={`${styles.panel} ${styles.empty}`}>
        <p className={styles.emptyTitle}>No outfit yet</p>
        <p className={styles.muted}>Tell the stylist where you're going, and your outfit shows up here.</p>
      </section>
    );
  }

  return (
    <section className={styles.panel} aria-busy={busy}>
      <div className={styles.summaryBar}>
        <div>
          <p className={styles.label}>Total</p>
          <p className={styles.total}>{formatPrice(outfit.total_price, outfit.currency)}</p>
          {outfit.budget && (
            <p className={`${styles.budget} ${outfit.over_budget ? styles.over : ""}`}>{budgetText(outfit)}</p>
          )}
        </div>
        <div className={styles.score} title="How well the pieces fit the request and each other">
          <p className={styles.label}>Match {Math.round(outfit.score * 100)}%</p>
          <dl className={styles.breakdown}>
            {Object.entries(outfit.breakdown).map(([term, value]) => (
              <div key={term} className={styles.term}>
                <dt>{term}</dt>
                <dd>
                  <span className={styles.bar}>
                    <span className={styles.fill} style={{ width: `${Math.round(value * 100)}%` }} />
                  </span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </div>

      {accepted && (
        <div className={styles.accepted}>
          <h2>Your outfit</h2>
          <ul>
            {outfit.pieces.map((p) => (
              <li key={p.item_id}>
                <a href={p.product_url} target="_blank" rel="noreferrer">{p.name}</a>
                <span className={styles.muted}> · {p.brand_line} · {formatPrice(p.price, p.currency)}</span>
              </li>
            ))}
          </ul>
          <p>Total: <strong>{formatPrice(outfit.total_price, outfit.currency)}</strong></p>
        </div>
      )}

      <div className={styles.grid}>
        {outfit.pieces.map((piece) => (
          <OutfitCard
            key={piece.slot}
            piece={piece}
            changed={changed.has(piece.slot)}
            overLimit={!piece.locked && outfit.budget?.max_item != null && piece.price > outfit.budget.max_item}
            disabled={busy || accepted}
            onAction={onAction}
          />
        ))}
      </div>

      {!accepted && (
        <div className={styles.controls}>
          <button disabled={busy} onClick={() => onAction({ action: "nudge", formality: 1 })}>Dressier</button>
          <button disabled={busy} onClick={() => onAction({ action: "nudge", formality: -1 })}>More relaxed</button>
          <span className={styles.vibe}>
            <select aria-label="Vibe" value={vibe} disabled={busy} onChange={(e) => setVibe(e.target.value as Vibe)}>
              {VIBES.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            <button disabled={busy} onClick={() => onAction({ action: "nudge", vibe })}>More {vibe}</button>
          </span>
          <form className={styles.budgetForm} onSubmit={setBudget}>
            <input
              type="number"
              min="1"
              step="any"
              inputMode="decimal"
              aria-label="Budget for the whole outfit, in dollars"
              placeholder={outfit.budget?.max_total != null ? `$${outfit.budget.max_total}` : "Budget $"}
              value={budgetDraft}
              disabled={busy}
              onChange={(e) => setBudgetDraft(e.target.value)}
            />
            <button type="submit" disabled={busy || !(draftAmount > 0)}>Set budget</button>
            {outfit.budget && (
              <button type="button" disabled={busy} onClick={() => onAction({ action: "budget" })}>Clear</button>
            )}
          </form>
          <button className={styles.accept} disabled={busy} onClick={() => onAction({ action: "accept" })}>
            Accept outfit
          </button>
        </div>
      )}
    </section>
  );
}
