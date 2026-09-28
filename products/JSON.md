# How to author a product `.json`

This is the spec for creating a product config file. The reader is expected to be an
LLM (or a careful human) that, given a **plain-English description of a product** plus
its **brands/makes**, **price range**, and **age window**, produces a complete
`products/<name>/<name>.json` that the pipeline can run.

The two example configs in this folder are the reference. `bois.json` is the full case
(two kept classes + an auxiliary "trap" class); `stiga_blade.json` is the minimal case
(only a primary class, no trap). Read both before generating a new file.

---

## 0. Where the file goes

```
products/<name>/<name>.json
```

- One folder per product, named `<name>` (lowercase, no spaces — the token used on the
  command line: `.\run.ps1 -Product <name>`).
- The config file inside MUST be named exactly `<name>.json` — the runner discovers a
  product by the presence of `products/<name>/<name>.json`.
- `<name>` also names the result pages: `products/<name>/vinted/<name>_DD.MM.YY.html`.
- The parent folder can be relocated via `products_dir` in the repo-root `config.json`
  (default `products`); that does not change the layout *inside* the folder.

Choose `<name>` to describe the *search*, not the category: `stiga_blade` (a specific
blade) not `blade`; `rtx3080` not `gpu`.

---

## 1. The class model — free-form names, three slots + OTHER

A product's classification is a **small set of classes with names you choose**, drawn
from the product description. There is no universal vocabulary: a table-tennis product
might have classes `bois` / `raquette` / `gomme`; a GPU product might have
`rtx 3080` / `other nvidia card`. The only fixed word is **`OTHER`** — the universal
reject.

### The three slots

The `classes` object has up to three slots:

| Slot        | Kept? | Purpose |
|-------------|-------|---------|
| `primary`   | **yes** | THE product — what you actually want to buy. Always present. |
| `secondary` | **yes** | An optional *second* acceptable form of the product. Only when there are genuinely two variants you both want. |
| `auxiliary` | **no**  | The optional **trap class** — the look-alike you expect sellers to list but that you want the *photo* stage to name and drop. Never kept. |

`auxiliary` is a separate top-level key (not inside `classes`) so it is structurally
"not kept". **Most products are just a `primary`** (like `stiga_blade`) — do not
invent a secondary or trap that the product doesn't have.

Each class entry is `{ "name": ..., "def": ... }`:

- **`name`** — the class name the model must reply with. Free-form: lowercase words,
  short, unambiguous. Multi-word names are fine (`"other nvidia card"`). It must be a
  word or phrase that **cannot appear** in the reply text as part of a different
  meaning (the parser is a substring match, longest name first).
- **`def`** — the plain-English definition, including the local-language synonyms
  sellers actually use (FR/DE/PT/ES). This is where recall comes from.

### How the model reply is parsed

`work/common.py::parse_reply(cfg, text)` uppercases the reply and does a **substring
match** against the product's class names (primary, secondary, auxiliary) plus
`OTHER`, **longest name first** (so `"other nvidia card"` wins over a shorter name it
contains). The first match is the class; if none match, the result is `OTHER`.

Consequences for naming:
- Names are matched case-insensitively, so the model can reply in any case.
- Because matching is "first occurrence of the word", keep names distinct enough that
  the model's reply names exactly one class. If two names could both appear in one
  sentence, the longer one wins — usually what you want, but design names so the
  model doesn't need to.
- You **never** put `OTHER` in `classes`; it is implicit and always means "reject".

### Mapping a description onto the slots

Given a product description, decide:

1. **What exactly is the target?** → that is the `primary` class name + definition.
   - Table tennis (bare blade): primary = `bois` = "a bare blade, no rubber".
   - GPU (RTX 3080): primary = `rtx 3080` = "an RTX 3080 / 3080 Ti card".
2. **Is there a second acceptable form?** → `secondary`. E.g. table tennis: you want
   both bare blades *and* finished rackets → secondary = `raquette`. A single-target
   product leaves `secondary` out.
3. **Is there one recurring look-alike the photo must reject?** → `auxiliary`.
   - Table tennis: a rubber sheet mislabeled as a blade → `gomme`.
   - GPU: *other* NVIDIA cards (3090, 4080…) under the same brand search →
     `other nvidia card`.
   If there is no dominant look-alike, omit `auxiliary` (like `stiga_blade`).
4. **Everything else** → `OTHER` (its definition goes in `vars.other_note`).

> A class you don't define, the model won't offer. Don't add classes for forms you
> don't care about — they just add noise the model has to weigh.

---

## 2. Field reference

Every field of `products/<name>/<name>.json`. Required fields are mandatory; others
fall back to the defaults shown.

### Page identity

| Field      | Type / req. | Meaning |
|------------|-------------|---------|
| `title`    | string, **required** | Page `<title>` and `<h1>`. e.g. `"Bois & raquettes de tennis de table"` or `"NVIDIA GeForce RTX 3080"`. |
| `subtitle` | string, optional | One line under the heading, **escaped as plain text** (no HTML). Describes the filtering, e.g. `"annonces vérifiées sur photo (raquettes et revêtements écartés)"`. |
| `legend`   | string, optional | Badge legend. **Inserted raw** — the only config field that may contain HTML. e.g. `"<b>bois</b> = lame seule · <b>raquette</b> = raquette complète"`. |
| `description` | string, optional | Plain-English "what to find" for this product (what the product editor shows in its Description field). The pipeline ignores it; it exists so the editor can re-load and re-generate the config. |

### Brands (search + grouping)

| Field           | Type / req. | Meaning |
|-----------------|-------------|---------|
| `brands`        | string[], **required** | The bare words searched on Vinted — **one catalog search per entry**. Lowercase, exactly as sellers write the brand/make. These are also the only brands recognized for grouping. |
| `brand_aliases` | object, optional (default `{}`) | Lowercase brand word → display name, for non-default capitalization: `{"dhs": "DHS"}`. Default display = word title-cased. |
| `brand_order`   | string[], optional | Display order of the brand sections (display names, i.e. *after* aliases). Unlisted brands follow alphabetically; the catch-all is last. Default: alphabetical. |

**How brand search works (critical).** Vinted is searched with each bare brand word; a
listing is returned only if the seller tagged that brand in "Marque:" **or** it is in
the title. The union of the `brands` searches is your candidate pool — keep the words
**broad enough** to capture the target, then let the LLM + price filter narrow. A
multi-word model number is usually **not** the right search term: for "RTX 3080" search
the *make* (`"nvidia"`), and let the LLM distinguish 3080 from 3090/4090 by title and
photo. Only put a specific model word in `brands` if sellers reliably tag it.

### Filters

| Field        | Type / req. | Meaning |
|--------------|-------------|---------|
| `price_min`  | number, **required** | Inclusive low bound, EUR (asking price; the "incl. buyer protection" second price is ignored). Dropped before any LLM call. |
| `price_max`  | number, **required** | Inclusive high bound, EUR. |
| `age_months` | number, default `4` | Keep only listings published within this many months. Vinted exposes no publish date; at runtime this becomes a minimum listing-ID via the calibrated ID→date fit (`work/id_date_fit.json`), precision ±1–2 months. |
| `max_items`  | number, default `200` | Page cap. If more items pass all filters, the most recent (highest ID) are kept. |
| `keep_days`  | number, default `2` | Days of dated result pages to keep in the product's `vinted/` folder; `build_html.py` prunes older files. |

### Classes

| Field       | Type / req. | Meaning |
|-------------|-------------|---------|
| `classes`   | object, **required** | The class set. Always has `primary`; optional `secondary`. Each value is `{ "name": ..., "def": ... }`. The names in `primary` (+ `secondary` when present) are exactly what counts as "your product" (the keep set). |
| `auxiliary` | object, optional | The trap class, `{ "name": ..., "def": ... }`. Present only if you have a dominant look-alike. **Never kept.** |
| `badges`    | object, optional | Card badge label per kept **class name**: `{"bois": "bois", "raquette": "raquette"}`. Only for classes that appear on cards (the keep set). Default: the class name. |
| `badge_colors` | object, optional | Per-badge **label** colors: `"<label>": [background, foreground, border]` (CSS colors). Default: neutral grey. Keep 2–3 colors distinguishable. |

### The `vars` section (prompt variables)

Holds every free-form product string substituted into the prompt templates.

| Key          | Req. | Feeds placeholder | Meaning |
|--------------|------|-------------------|---------|
| `product`    | **required** | `{product}` | Short product label, e.g. `"table-tennis blade"`, `"NVIDIA RTX 3080 graphics card"`. |
| `other_note` | **required** | `{other_note}` | Definition of `OTHER` — enumerate the common non-target items so the model rejects them confidently. e.g. `"anything else: rackets, rubbers, balls, apparel, unrelated items"`. |
| `disambig`   | optional | `{disambig}` | One-line **photo** disambiguation used by the vision prompt *only when there are ≥2 kept classes*. e.g. `"rubber visible = raquette, bare wood = bois"`. Omit for a single-kept-class product (the vision template doesn't reference it in that case). |

Placeholders derived by the code from `classes`/`auxiliary` (never put in `vars`):

| Placeholder    | Value |
|----------------|-------|
| `{all_names}`   | primary + secondary + auxiliary names, comma-joined. e.g. `"bois, raquette, gomme"` — or just `"bois"` for a single class, `"rtx 3080, other nvidia card"` for a GPU. |
| `{class_defs}`  | `"BOIS = <def>. RAQUETTE = <def>"` — the keep classes (primary, secondary) uppercased, with their defs. |
| `{aux_name}`    | auxiliary class name (only when `auxiliary` is set). |
| `{aux_def}`     | auxiliary class definition (only when `auxiliary` is set). |

### The `prompts` section

Six strings: three stages × (system, user). See §4.

### LLM sizing

The local model is a **reasoning model**: its hidden reasoning is billed
against `max_tokens` and the answer comes last. Measured on bois, a typical
title needs ~150–1500 reasoning tokens (p50 ~560), and a few titles trigger
runaway reasoning that never stops. So the pipeline runs each item at a
**small starting budget**, and if the budget is exhausted
(`finish_reason: length`, i.e. an empty answer) it escalates:

- **title stage**: `300 → 1500 → 4000` (covers ~95% of titles at the middle
  step), then the item is treated as unclassifiable (`OTHER`);
- **vision stage**: `400 → 4000`, then `OTHER`.

Runaway reasoners (> 4000 tokens of thinking) are rejected at the cap instead
of being retried forever. The cap is `common.ESCALATE_BUDGET` (4000).

| Field               | Type / req. | Meaning |
|---------------------|-------------|---------|
| `title_max_tokens`  | number, default `300` | Starting budget, title stage (auto-escalates up to 4000). |
| `vision_max_tokens` | number, default `400` | Starting budget, vision stage (auto-escalates up to 4000). |
| `workers`           | number, optional, default `4` | Parallel LLM calls, title stage. |
| `vision_workers`    | number, optional, default `3` | Parallel LLM calls, vision stage. |

Keep the starting budgets small — that's what keeps runs fast. Only raise them
persistently if *many* (not a few) items of a product need the escalation.

---

## 3. Prompt templating — how `vars` become prompts

`work/common.py::load_product` runs `_fill_prompts(cfg)` at load time. It takes each
string in `prompts` and calls `.format_map(...)` over a dict containing:

- every key you put in `vars` (`product`, `other_note`, `disambig`, …), and
- the derived keys: `all_names`, `class_defs`, `aux_name`, `aux_def`.

**Two-stage formatting.** After the load-time fill, each pipeline step does a second
`.format(title=..., brand=...)` per item. So:

- **You** use `{product}`, `{all_names}`, `{class_defs}`, `{aux_name}`, `{aux_def}`,
  `{other_note}`, `{disambig}` — filled from `vars` + `classes` at load time.
- The **code** uses `{title}` (cleaned listing title) and `{brand}` (seller's brand
  field) — these must appear **literally** in your `title_user`, `vision_user`, and
  `audit_user` strings; they survive the load-time fill and are filled per item.

> ⚠️ **Undefined placeholders are NOT removed.** `format_map` uses a mapping whose
> `__missing__` returns the literal `{key}`. If a prompt references a placeholder with
> no value, the finished prompt contains the literal `{placeholder}` — a silent bug.
>
> **Rule: only reference a placeholder if its value exists.** The two reference configs
> show the two valid shapes:
>
> - **Full (`bois.json`):** two kept classes + trap. Prompts reference
>   `{all_names}` (→ `bois, raquette, gomme`), `{class_defs}`, `{aux_name}`, `{aux_def}`,
>   `{other_note}`, and the vision prompt uses `{disambig}` (2 kept classes → defined).
> - **Minimal (`stiga_blade.json`):** one kept class, no trap. Prompts reference
>   `{all_names}` (→ just `bois`), `{class_defs}`, `{other_note}`. No `{aux_name}`,
>   `{aux_def}`, or `{disambig}` anywhere (nothing to disambiguate, no trap).

A safe general approach: write the prompts against the **actual** class set. Two kept
classes + trap → mirror `bois.json`. One kept class → mirror `stiga_blade.json`.

---

## 4. The six prompts

| key             | used by         | role |
|-----------------|-----------------|------|
| `title_system`  | step 2 (text)   | Define the classes and what each means. The model sees **only title + brand field** here. This is the bulk filter — get recall *and* precision here. |
| `title_user`    | step 2 (text)   | Per-item template. **Must** contain `{title}` and `{brand}`. e.g. `"Listing title: \"{title}\"\nSeller brand field: \"{brand}\"\nClassify:"` |
| `vision_system` | step 3 (photo)  | Re-check the **kept** items against the actual photo. Stricter than title: this is where the trap and boundary cases get dropped. |
| `vision_user`   | step 3 (photo)  | Per-item template, **must** contain `{title}` and `{brand}`. The photo is attached by the code. |
| `audit_system`  | `audit_dropped.py` | Optional second look at *dropped* items: a 1–2 sentence photo description + a final `VERDICT: X` line. Used for manual re-admission. |
| `audit_user`    | `audit_dropped.py` | Per-item template, must contain `{title}` and `{brand}`. |

### Writing guidance that makes or breaks quality

1. **`title_system` is the filter.** State each class's definition concretely and
   enumerate the look-alikes to reject. The title is the only signal here, so lean on
   the words sellers actually type — that's why `def` lists local-language synonyms.
2. **`vision_system` is the adjudicator.** It sees the photo, so describe the *visual*
   difference: for a blade, "rubber face = raquette, bare wood = bois"; for a GPU,
   "the model number printed on the cooler is the deciding text." Name the trap
   explicitly.
3. **Keep the one-word contract.** Every system prompt must say *"Reply with EXACTLY
   one of: …"* and list the class names (via `{all_names}`), with `OTHER` included.
   Remove or reword this and output degrades to `OTHER`.
4. **Reasoning models think first.** The answer is the *last* thing emitted, so the
   definitions in the system prompt are what get weighed — make them unambiguous.
5. **`other_note` should be a concrete enumeration**, not "anything else" alone.
   Listing the real distractors (for a GPU: other makes, other RTX models, cases,
   power cables) sharpens the reject.

### Reference prompt templates

**Single kept class (mirror `stiga_blade.json`)** — no `{aux_*}`, no `{disambig}`:

```
title_system:  You classify Vinted {product} listings. Reply with EXACTLY one of: {all_names}. {class_defs}. OTHER = {other_note}
vision_system: You are verifying a Vinted {product} listing. You are given the PHOTO of the item and its title. Look at the photo carefully. Decide what the item ACTUALLY is. Reply with EXACTLY one of: {all_names}. {class_defs}. OTHER = {other_note}
```

**Two kept classes + trap (mirror `bois.json`)** — add the trap line and `{disambig}`:

```
title_system:  You classify Vinted {product} listings. Reply with EXACTLY one of: {all_names}. {class_defs}. {aux_name} = {aux_def}. OTHER = {other_note}
vision_system: You are verifying a Vinted {product} listing. You are given the PHOTO of the item and its title. Look at the photo carefully. Decide what the item ACTUALLY is. Reply with EXACTLY one of: {all_names}. {class_defs}. {aux_name} = {aux_def}. OTHER = {other_note} When in doubt between the kept classes, judge from the photo: {disambig}
```

`title_user` / `vision_user` / `audit_user` are identical in both shapes (see the
example configs).

---

## 5. Step-by-step: authoring a product from a description

Given: *"description, brands/makes, price range, age."*

1. **Name it.** Pick `<name>` (lowercase token). Create `products/<name>/`.
2. **Decide the classes.** One sentence for the exact target → that's `primary`
   (name + def with local synonyms). Second acceptable form → `secondary`. One
   dominant look-alike → `auxiliary`. Most products stop at `primary`.
3. **Write `vars`.** `product`, `other_note` (enumerate real distractors). Add
   `disambig` only if there are ≥2 kept classes.
4. **Write the six prompts.** Mirror `bois.json` (full) or `stiga_blade.json`
   (minimal) to match your class set. Only reference placeholders you defined. Keep
   `{title}`/`{brand}` in the three `*_user` prompts. Preserve the "EXACTLY one of"
   contract.
5. **Fill the rest.** `title`, `subtitle`, `legend`, `brands` (+ aliases/order if
   needed), `price_min`/`price_max`, `age_months`, `badges` (one per kept class),
   `badge_colors`. Defaults for `max_items`, `keep_days`, token budgets are fine.
6. **Self-check** against the checklist in §6.

---

## 6. Checklist (verify before saving)

- [ ] File is `products/<name>/<name>.json`; `<name>` is a valid command-line token.
- [ ] `title`, `brands`, `price_min`, `price_max`, `classes.primary`, `vars.product`,
      `vars.other_note`, and all six `prompts` are present.
- [ ] `classes.primary` has a `name` and a `def`; `classes.secondary` (if present) does
      too. Each `def` lists the local-language synonyms sellers use.
- [ ] If `auxiliary` is present: it has `name` + `def`, and its name is **not** in
      `classes`. If the product has no dominant look-alike: `auxiliary` is absent.
- [ ] Class names are distinct and won't both appear in one reply; none of them is
      `OTHER`.
- [ ] Prompts only reference placeholders that exist: for a single kept class with no
      trap, no `{aux_name}`/`{aux_def}`/`{disambig}`; for a trap, those are present and
      `auxiliary` is set.
- [ ] All three `*_user` prompts contain `{title}` and `{brand}`.
- [ ] Every system prompt says "Reply with EXACTLY one of:" and lists the classes
      (via `{all_names}`) with `OTHER` included.
- [ ] `badges` has an entry for each kept class name.
- [ ] Token budgets ≥ 300 (title) / ≥ 300 (vision) for a reasoning model.
- [ ] `brands` words are lowercase as sellers type them; broad enough to capture the
      target, with narrowing done by the LLM + price filter.

---

## 7. Worked example — "NVIDIA GeForce RTX 3080, 200–500 €, last 3 months"

Reasoning:
- **Target** = an RTX 3080 (or 3080 Ti) card. → `primary` = `rtx 3080`.
- **Second form?** A 3080 Ti is just a variant of the target, not a second class —
  fold it into the primary's `def`. No `secondary`.
- **Dominant look-alike?** Yes — *other* NVIDIA GPUs (3090, 4080/4090, 3070, 2080…) are
  everywhere under the "nvidia" search and look identical in a title. → `auxiliary` =
  `other nvidia card`.
- **Search term** = the make `nvidia` (searching "3080" would miss cards tagged only by
  make); the LLM narrows to 3080 by title + the model number on the cooler.
- `OTHER` = non-GPU items (cases, PSU, cables, other brands).

Shape: one kept class + a trap. So the prompts follow the **single-kept-class** title
prompt, but because a trap exists, `title_system`/`vision_system` add the trap line
(`{aux_name} = {aux_def}`), and `{all_names}` expands to `rtx 3080, other nvidia
card`. There is only one *kept* class, so no `{disambig}`.

```json
{
  "title": "NVIDIA GeForce RTX 3080",
  "subtitle": "cartes RTX 3080 / 3080 Ti vérifiées sur photo (autres GPU NVIDIA écartés)",
  "legend": "<b>rtx 3080</b> = carte RTX 3080 ou 3080 Ti",
  "brands": ["nvidia"],
  "brand_aliases": { "nvidia": "NVIDIA" },
  "brand_order": ["NVIDIA"],
  "price_min": 200,
  "price_max": 500,
  "age_months": 3,
  "max_items": 200,
  "classes": {
    "primary": { "name": "rtx 3080", "def": "an NVIDIA GeForce RTX 3080 or RTX 3080 Ti graphics card (carte graphique / Grafikkarte / scheda video), standalone — the model must be 3080 or 3080 Ti, any brand's cooler" }
  },
  "auxiliary": {
    "name": "other nvidia card",
    "def": "a DIFFERENT NVIDIA GPU, not a 3080/3080 Ti: RTX 3090/3070/2080/1080, RTX 4080/4090, GTX 1660, etc."
  },
  "vars": {
    "product": "NVIDIA GeForce RTX 3080 graphics card",
    "other_note": "anything else: non-NVIDIA GPUs (AMD Radeon, Intel), PC cases, power supplies, cables, monitors, unrelated items"
  },
  "badges": { "rtx 3080": "rtx 3080" },
  "badge_colors": {
    "rtx 3080": ["#eef6ee", "#2f7d43", "#cfe6d2"]
  },
  "prompts": {
    "title_system": "You classify Vinted {product} listings. Reply with EXACTLY one of: {all_names}. {class_defs}. {aux_name} = {aux_def}. OTHER = {other_note}",
    "title_user": "Listing title: \"{title}\"\nSeller brand field: \"{brand}\"\nClassify:",
    "vision_system": "You are verifying a Vinted {product} listing. You are given the PHOTO of the item and its title. Look at the photo carefully. Decide what the item ACTUALLY is. Reply with EXACTLY one of: {all_names}. {class_defs}. {aux_name} = {aux_def}. OTHER = {other_note} When in doubt, the model number printed on the card's cooler or in the title decides: 3080 / 3080 Ti = rtx 3080, any other model = other nvidia card",
    "vision_user": "Title: \"{title}\"\nSeller brand: \"{brand}\"\nClassify from the photo:",
    "audit_system": "Look at this photo of a Vinted {product} listing. First describe the main object(s) in the photo in 1-2 sentences (what is it, and which of {all_names} it looks like). Then on the last line write 'VERDICT: X' where X is one of {all_names}.",
    "audit_user": "Title: \"{title}\"\nBrand: \"{brand}\""
  },
  "title_max_tokens": 1500,
  "vision_max_tokens": 4000
}
```

Run it with `.\run.ps1 -Product rtx3080` (folder `products/rtx3080/`).

**Why the class names are "rtx 3080" and "other nvidia card" (not BLADE/RUBBER):**
the class names are chosen to describe *this* product, in the words a human would use,
so the model's reply is meaningful and the `badges`/`legend` read naturally. The
parser matches these exact strings, so whatever you name them, the pipeline keeps the
`primary` and drops the `auxiliary` + `OTHER`.
