import { formatPrice, type Action, type Piece } from "../api";
import styles from "./OutfitCard.module.css";

interface Props {
  piece: Piece;
  changed: boolean; // differs from the previous outfit (FR-UI-4)
  disabled: boolean;
  onAction: (action: Action) => void;
}

/** One piece of the outfit: image, name, brand line, price (FR-UI-1), and its buttons (FR-UI-3). */
export function OutfitCard({ piece, changed, disabled, onAction }: Props) {
  const classes = [styles.card, piece.locked && styles.locked, changed && styles.changed].filter(Boolean).join(" ");
  return (
    <article className={classes}>
      <a className={styles.imageLink} href={piece.product_url} target="_blank" rel="noreferrer">
        <img className={styles.image} src={piece.image_url} alt={piece.name} loading="lazy" referrerPolicy="no-referrer" />
        <span className={styles.slot}>{piece.slot}</span>
        {piece.locked && <span className={styles.lockBadge}>Locked</span>}
      </a>
      <div className={styles.body}>
        <p className={styles.brand}>{piece.brand_line}</p>
        <h3 className={styles.name}>
          <a href={piece.product_url} target="_blank" rel="noreferrer">{piece.name}</a>
        </h3>
        <p className={styles.price}>{formatPrice(piece.price, piece.currency)}</p>
      </div>
      <div className={styles.actions}>
        <button
          className={piece.locked ? styles.active : undefined}
          aria-pressed={piece.locked}
          disabled={disabled}
          onClick={() => onAction({ action: piece.locked ? "unlock" : "lock", item_id: piece.item_id })}
        >
          {piece.locked ? "Unlock" : "Lock"}
        </button>
        <button disabled={disabled} onClick={() => onAction({ action: "swap", item_id: piece.item_id })}>
          Swap
        </button>
        <button
          className={styles.reject}
          disabled={disabled}
          title="Replace it and never show it again"
          onClick={() => onAction({ action: "reject", item_id: piece.item_id })}
        >
          Reject
        </button>
      </div>
    </article>
  );
}
