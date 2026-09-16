"""Step 4: merge verdicts and build the product HTML page (cards grouped by brand)."""
import json, io, os, sys, html, re
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_product, data_dir, out_dir, cutoff_for

sys.stdout.reconfigure(encoding='utf-8')
PRODUCT = sys.argv[1] if len(sys.argv) > 1 else 'bois'
cfg = load_product(PRODUCT)
D = data_dir(PRODUCT)
items = json.load(io.open(os.path.join(D, 'classified.json'), encoding='utf-8'))

# canonical brand map: lowercase brand -> display name
ALIASES = cfg.get('brand_aliases', {})
KNOWN = {b.lower(): ALIASES.get(b, b[0].upper() + b[1:]) for b in cfg['brands']}
BRAND_ORDER = cfg.get('brand_order', sorted(set(KNOWN.values())))
OTHER = cfg.get('other_label', 'Other')
KEEP = set(cfg.get('keep_classes', ['BLADE', 'RACKET']))
BADGES = cfg.get('badges', {})
BADGE_COLORS = cfg.get('badge_colors', {})

def title_brand(t):
    # brand name explicitly written in the title wins (longest names first)
    tl = ' ' + t.lower() + ' '
    for name in sorted(KNOWN, key=len, reverse=True):
        if re.search(r'(?<![a-z0-9])' + re.escape(name) + r'(?![a-z0-9])', tl):
            return KNOWN[name]
    return None

def assign_brand(it):
    b = title_brand(it.get('title_clean', ''))
    if b:
        return b
    b = KNOWN.get((it.get('listed_brand') or '').strip().lower())
    if b:
        return b
    for sb in it.get('search_brands', []):
        b = KNOWN.get(sb.lower())
        if b:
            return b
    return OTHER

def short_title(t, brand):
    s = t.strip()
    s = re.sub(r'[,.]\s*(bon|tres bon|très bon|satisfaisant|excellent)[^,]*état[^,]*', '', s, flags=re.I)
    s = re.sub(r',\s*Taille:.*$', '', s, flags=re.I)
    s = re.sub(r'(?i)\b%s\b' % re.escape(brand.lower()), '', s)
    s = re.sub(r'\(\s*\)', '', s)
    s = re.sub(r'\s{2,}', ' ', s).strip(' ,.-')
    return s[:80]

def esc(x):
    return html.escape(x or '', quote=True)

# vision verdicts (if present) + manual overrides
vis = {}
vp = os.path.join(D, 'vision_partial.json')
if os.path.exists(vp):
    vis = json.load(io.open(vp, encoding='utf-8'))
over = {}
ov = os.path.join(D, 'final_overrides.json')
if os.path.exists(ov):
    over = json.load(io.open(ov, encoding='utf-8'))

for it in items:
    it['vision'] = vis.get(it['url'])
    it['final'] = over.get(it['url']) or it.get('vision') or it.get('class')

title_kept = [it for it in items if it.get('class') in KEEP]
keep = [it for it in title_kept if it.get('final') in KEEP]
if vis:
    print('title-kept: %d, image-adjudicated: %d' % (len(title_kept), len(keep)))
else:
    print('kept (no vision): %d' % len(keep))

MAX_TOTAL = cfg.get('max_items', 200)
if len(keep) > MAX_TOTAL:
    keep.sort(key=lambda x: x['iid'], reverse=True)
    keep = keep[:MAX_TOTAL]
    print('capped to %d most recent' % MAX_TOTAL)

groups = {}
for it in keep:
    b = assign_brand(it)
    it['brand_final'] = b
    groups.setdefault(b, []).append(it)

def brand_key(b):
    if b in BRAND_ORDER:
        return (0, BRAND_ORDER.index(b))
    if b == OTHER:
        return (2, 0)
    return (1, b)

order = sorted(groups, key=brand_key)

# badge CSS from config
badge_css = []
for label, (bg, fg, border) in BADGE_COLORS.items():
    badge_css.append('.badge.%s { background:%s; color:%s; border-color:%s; }'
                     % (re.sub(r'[^a-z0-9-]', '-', label.lower()), bg, fg, border))
badge_css = '\n  '.join(badge_css)

rows_html = []
for b in order:
    lst = sorted(groups[b], key=lambda x: x['price'])
    rows_html.append('<section class="brand">')
    rows_html.append('<h2>%s <span class="cnt">%d</span></h2>' % (esc(b), len(lst)))
    rows_html.append('<div class="grid">')
    for it in lst:
        badge = BADGES.get(it.get('final'), it.get('final', '').lower() or 'item')
        price = '%.2f' % it['price']
        img = it.get('img') or ''
        imgtag = ('<img src="%s" alt="" loading="lazy">' % esc(img)) if img else '<div class="noimg">—</div>'
        rows_html.append(
            '<a class="card" href="%s" target="_blank" rel="noopener">'
            '<span class="ph">%s</span>'
            '<span class="tx"><b>%s</b> <i class="badge %s">%s</i><br>'
            '<span class="pr">%s €</span></span></a>' % (
                esc(it['url']), imgtag, esc(short_title(it['title_clean'], b)),
                re.sub(r'[^a-z0-9-]', '-', badge.lower()), badge, price))
    rows_html.append('</div>')
    rows_html.append('</section>')

cutoff_id, cutoff_date = cutoff_for(cfg)
TPL = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@</title>
<style>
  :root { --bg:#f4f5f9; --card:#fff; --line:#e3e6ee; --ink:#1c2333; --mut:#6b7280; --acc:#2563eb; }
  * { box-sizing:border-box; }
  body { margin:0; padding:28px clamp(14px,4vw,40px); background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--mut); font-size:13px; margin-bottom:22px; max-width:820px; line-height:1.5; }
  section.brand { margin-bottom:30px; }
  h2 { font-size:17px; margin:0 0 12px; }
  .cnt { color:var(--mut); font-weight:400; font-size:13px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:12px; }
  .card { display:block; background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:10px; text-decoration:none; color:var(--ink); transition:box-shadow .12s, transform .12s; }
  .card:hover { box-shadow:0 4px 14px rgba(30,40,90,.12); transform:translateY(-2px); }
  .ph { display:block; height:150px; margin-bottom:8px; }
  .ph img { width:100%; height:100%; object-fit:cover; border-radius:8px; background:#eef0f5; display:block; }
  .noimg { display:flex; align-items:center; justify-content:center; height:100%; color:var(--mut);
           border-radius:8px; background:#eef0f5; }
  .tx { font-size:13px; line-height:1.35; display:block; }
  .tx b { font-weight:600; }
  .pr { color:var(--acc); font-weight:700; font-size:14px; }
  .badge { font-style:normal; font-size:10px; padding:1px 6px; border-radius:8px; vertical-align:1px;
           border:1px solid var(--line); color:var(--mut); text-transform:uppercase; letter-spacing:.03em; }
  @@BADGE_CSS@@
  .foot { color:var(--mut); font-size:12px; margin-top:26px; }
</style>
</head>
<body>
  <h1>@@TITLE@@</h1>
  <div class="sub">@@TOTAL@@ annonces, @@PRICE_MIN@@–@@PRICE_MAX@@ €, publiées depuis le @@CUTOFF@@ (≈@@AGE@@ mois), triées par marque puis par prix.
  @@SUBTITLE@@
  @@LEGEND@@
  Cliquer sur une carte ouvre l'annonce sur Vinted.</div>
@@ROWS@@
  <div class="foot">Généré le @@TODAY@@ à partir des pages de recherche Vinted.fr (une page ≈ 96 annonces/marque).
  Âge estimé via l'identifiant de l'annonce (±1–2 mois) — Vinted n'expose pas la date de publication.</div>
</body>
</html>
"""
html_out = (TPL
            .replace('@@TITLE@@', esc(cfg['title']))
            .replace('@@BADGE_CSS@@', badge_css)
            .replace('@@TOTAL@@', str(len(keep)))
            .replace('@@PRICE_MIN@@', ('%g' % cfg['price_min']))
            .replace('@@PRICE_MAX@@', ('%g' % cfg['price_max']))
            .replace('@@CUTOFF@@', esc(str(cutoff_date)))
            .replace('@@AGE@@', ('%g' % cfg.get('age_months', 4)))
            .replace('@@SUBTITLE@@', esc(cfg.get('subtitle', '')))
            .replace('@@LEGEND@@', cfg.get('legend', ''))
            .replace('@@ROWS@@', '\n'.join(rows_html))
            .replace('@@TODAY@@', esc(str(date.today()))))

outdir = out_dir(PRODUCT)
out = os.path.join(outdir, PRODUCT + '.html')
io.open(out, 'w', encoding='utf-8').write(html_out)
print('wrote %s (%d bytes)' % (out, len(html_out)))
print('per brand: %s' % {b: len(groups[b]) for b in order})
