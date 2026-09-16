# Vinted Product Finder

Scans [vinted.fr](https://www.vinted.fr) for recent listings across a list of brands,
filters to a price range and an age window (default ~4 months), and generates a
self-contained HTML page organized by brand. Every listing is verified **on its photo**
by a local LLM, so items that sellers mislabel (e.g. rubbers sold as "raquette", or
unrelated gear) get dropped.

**Generic & multi-product**: one config file per product in `products/`
(brands, price range, age window, LLM prompts, labels). Output goes into a
folder per product: `bois/bois.html`, `nvidia/nvidia.html`, etc. The previous
run's page is kept as `<name>/<name>.prev.html` (exactly one copy).

```
.\run.ps1                 # run the default product (bois)
.\run.ps1 -Product nvidia # run a specific product
.\run.ps1 -All            # run every product in products\
```

## Adding a new product

Copy `products/bois.json` to `products/<name>.json` and edit. Every variable:

### `title` *(string, required)*
Page `<title>` and `<h1>`. Example: `"Bois & raquettes de tennis de table"`.

### `subtitle` *(string, optional)*
One-line explanation printed under the heading (before the legend). Escaped as plain
text — no HTML. Example: `"annonces vérifiées sur photo (revêtements, sacs et vêtements écartés)"`.

### `legend` *(string, optional — may contain HTML)*
Badge legend line, e.g. `"<b>bois</b> = lame seule · <b>raquette</b> = raquette complète"`.
Inserted **raw** (the only config field that is not escaped), so it's the only place
you can put inline HTML in the header.

### `brands` *(string array, required)*
The bare brand words searched on Vinted — one catalog search per entry
(`catalog?search_text=<brand>&query=<brand>`). Lowercase, as sellers write them.
These are also the only brands recognized for grouping (see `brand_aliases`).
Vinted matches a listing to your brand either via the seller's "Marque:" field or the
title — an item only appears under one of these searches.

### `brand_aliases` *(object, optional, default `{}`)*
Lowercase brand word → display name, when you want a non-default capitalization:
`{"dhs": "DHS", "victas": "VICTAS"}`. Without it, the display name is the word
title-cased.

### `brand_order` *(string array, optional)*
Display order of the brand sections on the page (display names, i.e. after
`brand_aliases` is applied). Brands not listed come after, alphabetically; the
catch-all section comes last. Default: alphabetical.

### `price_min` / `price_max` *(number, required)*
Inclusive price window in EUR. The asking price is used — the "incl. buyer
protection" second price in the card title is ignored. Listings outside the window
are dropped before any LLM call (saves tokens).

### `age_months` *(number, default 4)*
Keep only listings published within this many months. Vinted exposes no publish date,
so this is converted at runtime into a minimum listing-ID using the calibrated
ID→date fit (`work/id_date_fit.json`, see "Age filter" below). Effective precision
±1–2 months.

### `max_items` *(number, default 200)*
Page cap. If more items pass all filters, the most recent (highest ID) are kept.

### `keep_classes` *(string array, default `["BLADE","RACKET"]`)*
Which LLM classes count as "your product". Both the title stage output and the
vision stage output are checked against this set; anything else is dropped. Must be
words from the fixed reply vocabulary (see `prompts`).

### `badges` *(object, optional)*
Card badge label per class: `{"BLADE": "bois", "RACKET": "raquette"}`. The label is
shown on each card. Default: the class name lowercased.

### `badge_colors` *(object, optional)*
Per-badge (by **label**) colors: `"<label>": [background, foreground, border]` (CSS
colors). Default: neutral grey.

### `prompts` *(object, required)*
The six LLM prompts — three stages × system/user:

| key | used by | role |
|---|---|---|
| `title_system` | step 2 | defines the classes and what each means for your product |
| `title_user` | step 2 | per-item prompt; placeholders `{title}` (cleaned listing title) and `{brand}` (seller's brand field) |
| `vision_system` | step 3 | photo re-check: what to look for, and the stricter contract (e.g. rubber vs. blade) |
| `vision_user` | step 3 | per-item photo prompt, same `{title}` / `{brand}` placeholders |
| `audit_system` | `audit_dropped.py` | asks for a 1–2 sentence photo description + `VERDICT: X` line |
| `audit_user` | `audit_dropped.py` | same placeholders |

**Contract (must be preserved):** the model's answer is parsed by matching the words
`BLADE`, `RACKET`, `RUBBER`, `OTHER` (first match wins, uppercased). You can rename
what the words *mean* in your product's definitions, but the four words themselves and
the "reply with exactly one of" instruction must stay, or classification silently
degrades to `OTHER`.

### `title_max_tokens` *(number, default 300)*
Completion budget for the title stage. **Must be ≥ 300 for reasoning models**
(Qwen3.x etc.): early tokens go to the hidden `reasoning_content` and the answer
(`content`) only appears at the end — a small budget returns an empty answer.

### `vision_max_tokens` *(number, default 400)*
Same for the vision stage. Keep ≥ 300 for reasoning models.

### `workers` / `vision_workers` *(number, optional, defaults 4 / 3)*
Parallel LLM calls per stage. Lower them if the local server slows down or OOMs
with concurrent requests; raise them if it has headroom.

Then `.\run.ps1 -Product <name>` — that's the whole setup.

**Writing the prompts** is the part that makes or breaks quality: the *title* prompt
decides what counts as your product vs. an accessory (describe the exact object, and
enumerate the look-alikes to reject); the *vision* prompt re-checks the kept items
against the actual photo. Keep the one-word reply contract.

## Quick start (Windows)

```powershell
.\run.ps1
```

That's it. The script:
1. creates a virtualenv (`.venv\`) and installs `requirements.txt` if needed;
2. checks the LLM server from `config.json` is up and the model is loaded;
3. saves the product's previous `<name>/<name>.html` as `<name>/<name>.prev.html`;
4. runs the pipeline: fetch → title classify → vision verify → build HTML.

### Resuming after a failure

The LLM steps are the long pole and can be interrupted. `run.ps1` keeps a per-product
run-state (`work\data\<name>\.run_state.json`) plus per-step checkpoints
(`cls_partial.json`, `vision_partial.json`). **Just re-run `.\run.ps1`** — it prints
which steps it picks up and how many items are already done, and skips finished ones.
Force a redo: `.\run.ps1 -Product X -Retitle`, `-Revision`, `-Refetch`, or `-Clean`
(wipes that product's state and runs from scratch).

## How it works

```
run.ps1              orchestrator: venv, LLM check, backup, resumable multi-product pipeline
config.json          LLM endpoint / model / API key (shared by all products)
requirements.txt     python deps (pillow only)
products/
  bois.json          one file per product (see "Adding a new product")
<name>/<name>.html        output page per product
<name>/<name>.prev.html   last run's page (kept by run.ps1)
work/
  common.py          shared helpers (product config, paths, LLM, age cutoff)
  fetch_all.py       1. fetch + parse brand search pages          (no LLM)
  calibrate2.py      (re)calibrate item-ID -> creation-date fit   (no LLM, only when stale)
  filter_classify.py 2. price/age filter, LLM title classify      (text LLM)
  vision_verify.py   3. LLM re-classification ON THE PHOTO        (vision LLM)
  audit_dropped.py   (optional) descriptive second look at drops  (vision LLM)
  build_html.py      4. merge verdicts, build the HTML page       (no LLM)
  id_date_fit.json   the calibrated ID->date fit (auto-used)
  data/<name>/       per-product intermediate JSON (merged, classified, vision,
                     overrides) + .run_state.json
```

## Prerequisites

1. **Python 3.10+** on PATH. `run.ps1` handles the venv and dependency install.
2. **A local LLM server with an OpenAI-compatible API**, configured in `config.json`:

   | key | meaning |
   |---|---|
   | `llm_url` | base URL, e.g. `http://127.0.0.1:1234` |
   | `api_key` | `""` if no auth; otherwise sent as `Authorization: Bearer <key>` |
   | `model` | model id as reported by `GET <llm_url>/v1/models` |

   One model can do both stages:

   | Stage | Needs | Notes |
   |---|---|---|
   | Title classification | text chat | `max_tokens ≥ 300` for reasoning models (Qwen3.x etc.) |
   | Photo verification | **vision** chat | Vinted images are webp; the llama.cpp server rejects webp bytes, so scripts convert to PNG via Pillow |

   The reference setup is `unsloth/qwen3.8-27b` (reasoning + vision). A vision model
   alone is not enough (the title stage does the bulk filtering), and a text-only model
   alone leaves false positives.

3. **Internet access from a non-datacenter IP.** Vinted is behind DataDome bot
   protection: plain curl with a browser User-Agent works from a home connection, but
   datacenter/cloud IPs get blocked. Run this on your own machine.

### Running the steps manually (optional)

`run.ps1` calls these under the hood; from `work/`, with any python that has Pillow:

```
python fetch_all.py bois         # ~1 min   -> data/bois/merged.json
python filter_classify.py bois   # ~12 min  -> data/bois/classified.json (resumable)
python vision_verify.py bois     # ~5 min   -> data/bois/vision_partial.json (resumable)
python audit_dropped.py bois     # ~1.5 min -> prints descriptions of dropped items
python build_html.py bois        # instant  -> bois/bois.html
```

Without `run.ps1`, scripts fall back to built-in LLM defaults
(`http://127.0.0.1:1234`, `unsloth/qwen3.8-27b`); otherwise set `VT_LLM_URL`,
`VT_LLM_MODEL`, `VT_LLM_API_KEY`.

The HTML is a static file — open it in a browser (or publish it; images load from
Vinted's CDN).

## Configuration

| What | Where |
|---|---|
| LLM endpoint / model / API key (all products) | `config.json` |
| Everything product-specific | `products/<name>.json` |
| Age window | `age_months` in the product file (cutoff derived at runtime) |
| Brand display order / aliases | product file: `brand_order`, `brand_aliases` |

### The age filter (and when to recalibrate)

Vinted exposes **no publication date** anywhere (checked: page HTML, embedded JSON,
JSON-LD, API). Instead, listing IDs increase monotonically with creation time, so the
pipeline converts each product's `age_months` into a minimum ID at runtime. The linear
fit `id → creation date` was calibrated from the Wayback Machine's first-snapshot
dates of ~13,000 archived listing pages; median error ~±1–2 months — acceptable for a
4-month window.

To refresh the fit (only needed after a few months, when the fit drifts):

```
curl -s "http://web.archive.org/cdx/search/cdx?url=vinted.fr/items/&matchType=prefix\
&fl=timestamp,original&collapse=urlkey&limit=20000&from=<YYYYMMDD>" \
  -o work/wayback_recent.txt
cd work && python calibrate2.py     # rewrites work/id_date_fit.json
```

## Known quirks

- **Vision verdicts are ~90% right, not 100%.** It will occasionally call a bare blade
  a "rubber" (photo shows only a face) or a complete racket "OTHER". The audit step
  (`audit_dropped.py`) plus a quick look at the printed descriptions — or the photos —
  is how boundary cases get re-admitted. Manual re-admissions live in
  `work\data\<name>\final_overrides.json` (URL → class); `build_html.py` applies them
  with top priority. That file is per-batch: it's only consulted when present, so a
  fresh run without it is valid (you just lose the hand re-admissions).
- **webp → PNG:** required, or the local server answers `400 Invalid url`.
- Search pages show ~96 listings per brand — one page each, no pagination needed.
- Search results are brand-*field*-driven: items are found by searching each bare
  brand word, so a listing only appears if the seller tagged (or the title mentions)
  one of your brands.

## Reuse

`.\run.ps1` any time you want a fresh page (~20 min per product, mostly LLM time).
It always keeps the previous output as `<name>/<name>.prev.html` so you can compare
runs. Intermediate JSONs in `work\data\<name>\` are all inspectable — `classified.json`
holds title+brand+price+image for everything that passed the first filter, with the LLM
class on each entry.
