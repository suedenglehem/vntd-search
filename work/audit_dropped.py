"""Optional: descriptive second look at items vision dropped (print descriptions)."""
import json, io, os, sys, time, base64, urllib.request
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_product, data_dir, llm_cfg, llm_headers

sys.stdout.reconfigure(encoding='utf-8')
PRODUCT = sys.argv[1] if len(sys.argv) > 1 else 'bois'
cfg = load_product(PRODUCT)
D = data_dir(PRODUCT)
llm = llm_cfg()

SYS = cfg['prompts']['audit_system']
USER_FMT = cfg['prompts']['audit_user']
KEEP = set(cfg.get('keep_classes', ['BLADE', 'RACKET']))

def fetch_img_png_b64(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                               'Referer': 'https://www.vinted.fr/'})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    buf = io.BytesIO()
    Image.open(io.BytesIO(raw)).convert('RGB').save(buf, 'PNG')
    return base64.b64encode(buf.getvalue()).decode()

items = {x['url']: x for x in json.load(io.open(os.path.join(D, 'classified.json'), encoding='utf-8'))}
vis = json.load(io.open(os.path.join(D, 'vision_partial.json'), encoding='utf-8'))
dropped = [u for u, v in vis.items() if v not in KEEP and not v.startswith('ERROR')]
print('auditing %d dropped items' % len(dropped), flush=True)

def look(url):
    it = items[url]
    b64 = fetch_img_png_b64(it['img'])
    body = json.dumps({'model': llm['model'], 'messages': [
        {'role': 'system', 'content': SYS},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': USER_FMT.format(
                title=it['title_clean'], brand=it.get('listed_brand', ''))},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + b64}}]}],
        'max_tokens': 500, 'temperature': 0.0, 'stream': False}).encode()
    for attempt in range(3):
        try:
            r = urllib.request.Request(llm['url'], data=body, headers=llm_headers())
            with urllib.request.urlopen(r, timeout=180) as resp:
                d = json.loads(resp.read().decode('utf-8'))
            return url, d['choices'][0]['message'].get('content', '')
        except Exception:
            time.sleep(2 + attempt * 2)
    return url, 'ERROR'

res = {}
with ThreadPoolExecutor(max_workers=3) as ex:
    for f in as_completed([ex.submit(look, u) for u in dropped]):
        u, txt = f.result()
        res[u] = txt
        it = items[u]
        print('\n## [%s] %s (%s)' % (vis[u], it['title_clean'][:55], it['price']), flush=True)
        print(txt.strip()[:400], flush=True)

json.dump(res, io.open(os.path.join(D, 'audit_dropped.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
print('\nsaved %s' % os.path.join(D, 'audit_dropped.json'))
