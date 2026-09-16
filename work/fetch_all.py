"""Step 1: fetch Vinted.fr catalog pages for each brand of a product and parse cards."""
import re, io, os, sys, json, time, subprocess
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_product, data_dir

sys.stdout.reconfigure(encoding='utf-8')
PRODUCT = sys.argv[1] if len(sys.argv) > 1 else 'bois'
cfg = load_product(PRODUCT)
D = data_dir(PRODUCT)

BRANDS = cfg['brands']
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

class CardParser(HTMLParser):
    """Collect per-card: href (from <a href=/items/ID>), title (same <a>), img (data-testid)."""
    def __init__(self):
        super().__init__()
        self.cards = {}   # iid -> {'title','img'}
        self.hrefs = {}   # iid -> '/items/...'
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == 'a':
            m = re.match(r'/items/(\d+)', d.get('href', ''))
            if m:
                iid = m.group(1)
                self.hrefs.setdefault(iid, d['href'].split('?')[0])
                if d.get('title'):
                    self.cards.setdefault(iid, {})['title'] = d['title']
        elif tag == 'img':
            m = re.match(r'product-item-id-(\d+)--image--img', d.get('data-testid', ''))
            if m and d.get('src'):
                self.cards.setdefault(m.group(1), {}).setdefault('img', d['src'])

def parse_html(h):
    p = CardParser(); p.feed(h)
    out = []
    for iid, c in p.cards.items():
        href = p.hrefs.get(iid)
        if not href or not c.get('title'):
            continue
        out.append({'iid': int(iid), 'url': 'https://www.vinted.fr' + href,
                    'title': c['title'], 'img': c.get('img', '')})
    return out

def fetch(brand):
    url = 'https://www.vinted.fr/catalog?search_text=%s&query=%s' % (brand, brand)
    fn = os.path.join(D, 'cat_%s.html' % brand)
    subprocess.run(['curl', '-s', '-o', fn,
                    '-H', 'User-Agent: ' + UA,
                    '-H', 'Accept-Language: fr-FR,fr;q=0.9,en;q=0.8',
                    url, '--max-time', '40'], check=False)
    if not os.path.exists(fn) or os.path.getsize(fn) < 100000:
        return None
    return parse_html(io.open(fn, encoding='utf-8', errors='replace').read())

all_items = {}
for b in BRANDS:
    try:
        items = fetch(b)
    except Exception as e:
        print(b, 'ERROR', e); items = None
    if items is None:
        print('%s: FETCH FAILED' % b); time.sleep(3); continue
    for it in items:
        t = it['title']
        pm = re.findall(r'(\d+[.,]\d{2})\s*€', t)
        it['price'] = float(pm[0].replace(',', '.')) if pm else None
        bm = re.search(r'Marque:\s*([^,]+),', t)
        it['listed_brand'] = bm.group(1).strip() if bm else ''
        em = re.search(r'État:\s*([^,]+),', t)
        it['etat'] = em.group(1).strip() if em else ''
        prev = all_items.get(it['url'])
        if prev:
            sb = set(prev.get('search_brands', [])); sb.add(b)
            prev['search_brands'] = sorted(sb)
        else:
            it['search_brands'] = [b]
            all_items[it['url']] = it
    print('%s: %d cards' % (b, len(items)), flush=True)
    time.sleep(2.5)

json.dump(list(all_items.values()),
          io.open(os.path.join(D, 'merged.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('TOTAL unique items: %d' % len(all_items))
