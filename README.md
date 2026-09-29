# Vinted Product Finder

I needed a simple scanner of vinted to check on all cheap table tennis blades in 10-30e range, often blades with
broken edges which i can easily repair are in this range. So, first asked hermes to do a scanner for that, it
got complettely bogged down in too complex ways of doing it. Claude solved it all in 2h. It's generic of cause,
just create or json for finding rtx3090s or cheap plutonium lumps. I run in on win10, you want
to fire it on linux/mac etc or in docker, just ask claude to rewrite the runner script.

Scanner of [vinted.fr](https://www.vinted.fr) for recent listings across a list of brands,
filters to a price range and an age window (default ~4 months), and generates a
self-contained HTML page organized by brand. Every listing is verified **on its photo**
by a local LLM, so items that sellers mislabel (e.g. rubbers sold as "raquette", or
unrelated gear) get dropped.

**Generic & multi-product**: one folder per product in `products/`, holding its
config `<name>/<name>.json` (brands, price range, age window, LLM prompts,
labels). Results go into the same product's `vinted/` subfolder, named with
the search date (french format): `products/bois/vinted/bois_28.09.26.html`,
etc. The page shows its request date in the footer. Previous days' pages are
kept per the product's `keep_days` (default 2 — i.e. today + yesterday).

```
.\run.ps1                 # run the default product (bois)
.\run.ps1 -Product nvidia # run a specific product
.\run.ps1 -All            # run every product in products\
.\run.ps1 -Rerun          # full redo of EVERY product (-Refetch -Retitle -Revision)
.\run.ps1 -Rerun -Product bois   # full redo of one product only
```

`-Rerun` is shorthand for `-Refetch -Retitle -Revision`; without `-Product` it
applies to all products (like `-All`). `rerun.bat` in the repo root does exactly
this — double-click it.

## Adding a new product

Copy `products/bois/bois.json` to `products/<name>/<name>.json` (one folder per
product) and edit. Every variable:

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

### `keep_days` *(number, default 2)*
How many days of dated result pages to keep in `products/<name>/vinted/`.
`build_html.py` prunes older `<name>_DD.MM.YY.html` files on every run. Raise
it (e.g. `10`) to keep a longer history.

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

## Product editor (let the LLM write the config)

`work/product_editor.py` is a Gradio web UI that turns a plain-English product
description into a complete, validated `<name>/<name>.json` — no hand-writing
required. It prompts the *same* LLM as the pipeline (`config.json`: `llm_url` /
`model` / `api_key`), feeding it the config spec (`products/JSON.md`) plus two
worked examples, so the output is pipeline-compatible by construction.

```powershell
.\editor.ps1                    # opens http://127.0.0.1:7860
```

The script does the same venv/requirements handling as `run.ps1` (gradio is
already in `requirements.txt`), checks the LLM server, then runs the editor in
the foreground — Ctrl+C stops it. If 7860 is busy with an already-running
editor it picks the next free port and tells you. Equivalently:

```powershell
.\.venv\Scripts\python.exe work\product_editor.py
```

**Workflow:**
1. Fill in *Product name* (folder name), *Description of the product to find*,
   *Brands*, price window and age window (the title is optional — the LLM may choose).
2. **Generate JSON with LLM** — the button greys out while the request is in
   flight (reasoning models can take several minutes); **Cancel LLM call** aborts
   a stuck or unwanted generation. The model's bounded budget escalates
   (4000 → 8000 tokens) only if the first answer is truncated, and a 600 s
   *idle* window (no bytes from the server) is the only automatic cutoff.
3. The result lands in an editable box — fix anything by hand.
4. **Save to products/** validates it (required keys, classes, reply contract)
   and writes `products/<name>/<name>.json`.

The other buttons: **Load existing** fills the form from an existing product
(edit + re-generate, then Save — this is how products get retargeted, e.g.
rtx3080 → rtx3090), **Clear** empties everything, **List products** lists what's
in `products/`, and **Refresh models** re-queries the LLM server. The model
dropdown shows only the models actually *loaded* on the server (LM Studio's
native `/api/v0/models`); a warning appears under it if the `config.json` model
is not among them.

Headless equivalent (CI / one-liner / no browser):

```
python work\product_editor.py --name rtx3090 --desc "NVIDIA RTX 3090 card" \
    --brands nvidia --pmin 200 --pmax 1500 --age 1 --max-items 10 --save
```

(`--json '<exact json>' --save` skips the LLM entirely and just validates + writes.)

## Quick start (Windows)

```powershell
.\run.ps1
```

That's it. The script:
1. creates a virtualenv (`.venv\`) and installs `requirements.txt` if needed;
2. checks the LLM server from `config.json` is up and the model is loaded;
3. writes a dated page `products/<name>/vinted/<name>_DD.MM.YY.html` (and prunes days older than `keep_days`);
4. runs the pipeline: fetch → title classify → vision verify → build HTML.

### Resuming after a failure

The LLM steps are the long pole and can be interrupted. `run.ps1` keeps a per-product
run-state (`work\data\<name>\.run_state.json`) plus per-step checkpoints
(`cls_partial.json`, `vision_partial.json`). **Just re-run `.\run.ps1`** — it prints
which steps it picks up and how many items are already done, and skips finished ones.
Force a redo: `.\run.ps1 -Product X -Retitle`, `-Revision`, `-Refetch`, or `-Clean`
(wipes that product's state and runs from scratch); `-Rerun` forces all three steps.

## How it works

```
run.ps1              orchestrator: venv, LLM check, backup, resumable multi-product pipeline
config.json          LLM endpoint / model / API key (shared by all products)
requirements.txt     python deps (pillow; gradio only for the product editor)
products/
  JSON.md              the config spec the product editor feeds to the LLM
  <name>/<name>.json    one folder per product: config + results (see "Adding a new product")
  <name>/vinted/<name>_DD.MM.YY.html   one page per search day (keep_days old kept)
work/
  common.py          shared helpers (product config, paths, LLM, age cutoff)
  product_editor.py  Gradio UI: LLM generates a product config (see "Product editor")
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

   **The model MUST have a vision part** — step 3 (photo verification) sends each
   listing's actual image to the model and drops the ones whose photo doesn't match.
   That step is what removes sellers who mislabel an item (a rubber sold as "raquette",
   an unrelated gadget under your brand word), so a text-only model cannot be used at
   all: it can't read a photo, so step 3 is impossible and every false positive from
   step 2 survives into the output.

   One model can do both stages (vision models read text too):

   | Stage | Needs | Notes |
   |---|---|---|
   | Title classification | text chat | `max_tokens ≥ 300` for reasoning models (Qwen3.x etc.) |
   | Photo verification | **vision** chat | **required** — Vinted images are webp; the llama.cpp server rejects webp bytes, so scripts convert to PNG via Pillow |

   The reference setup is `unsloth/qwen3.8-27b` (reasoning + vision). Note that a
   vision model is *necessary but not the whole job*: the title stage does the bulk
   filtering, so a pure vision model that is weak at text classification will still
   misroute items — pick something strong at both. `run.ps1` checks the model's
   `type` from LM Studio's `/api/v0/models` and warns at startup if it isn't `vlm`.

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
python build_html.py bois        # instant  -> products/bois/vinted/bois_<today>.html
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
| Products folder location | `config.json`: `products_dir` (path relative to the repo root, or absolute; default `products`) |
| Everything product-specific | `products/<name>/<name>.json` |
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
Each run's page is dated (`<name>_DD.MM.YY.html`) and previous days are kept
per `keep_days` so you can compare runs. Intermediate JSONs in `work\data\<name>\` are all inspectable — `classified.json`
holds title+brand+price+image for everything that passed the first filter, with the LLM
class on each entry.
