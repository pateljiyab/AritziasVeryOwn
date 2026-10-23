# Gold set: labeling guide

`gold_set.csv` holds 30 catalog items (5 per slot) labeled by hand. It is the answer key for the tagging accuracy report (FR-TAG-3, FR-TAG-4). The tag definitions are in [PRD Appendix A](../docs/PRD.md#appendix-a-tag-taxonomy).

## Ground rules

1. **Label before any model tags exist.** Do not run the tagging job, look at LLM or CLIP output, or ask an AI assistant for help until all 30 rows are done.
2. **Freeze the labels once model output exists.** If you later find a real mistake (for example, you misread the photo), fix it and say why in `notes`. Never change a label because the model disagreed.
3. **Label from the product photo and the description.** Open `image_url`, and `product_url` if you need more. The catalog has no retailer color names, so judge color from the photo.
4. **Go with your first honest read.** Spend about a minute per item. If you're unsure, pick a label and write the doubt in `notes`.

## How to fill in each column

Use lowercase tags exactly as written in Appendix A. Separate multiple tags with `;` and no spaces, for example `fall;winter`. **Put the most defining tag first**: the report scores top-1 accuracy on the first tag.

| Column | What to enter | Rules of thumb |
|---|---|---|
| `season` | 1–4 of `spring`, `summer`, `fall`, `winter` | Tag every season you'd realistically wear it in, with the best one first. A basic tee might be `summer;spring;fall`. Use all four only for true year-round staples. |
| `vibe` | 1–3 of `cozy`, `polished`, `going-out`, `off-duty`, `athleisure`, `minimal`, `romantic`, `edgy` | Ask "what would a stylist call this?" Stop at 1–2 tags unless a third is clearly true. Use `minimal` for plain neutral pieces, not as a fallback. |
| `color` | 1–3 color tags | The main color comes first. Map shades to the closest tag (sage → `green`, oat → `cream`, wash → `denim`). Prints, stripes and plaids are `multi`. Add a second or third color only if it's clearly visible, like contrast trim or soles. |
| `formality` | One integer, 1–5 | 1 lounge/athletic, 2 casual, 3 smart casual, 4 business/dressy, 5 formal/event. Rate the item itself, not how you'd style it. |
| `notes` | Optional | Doubts, near-ties ("between 3 and 4"), or a photo that doesn't match the description. |

If an item has more than one color, label the color shown in the photo, because the photo is what the catalog row shows.

## How the 30 items were picked

Within each slot, products with the same name were collapsed to one, then 5 were drawn at random with `random.Random(42)`. One shoe, a second colorway of the CVO Sneaker that was already drawn, was swapped for another random shoe (same seed) so no product appears twice. The gold set follows the catalog's real mix, so it's heavy on knitwear and scarves, as the catalog is.
