"""Step 2: price/age filter + LLM title classification (parallel, resumable)."""
import re, io, os, sys, json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_product, data_dir, llm_cfg, llm_headers, cutoff_for

sys.stdout.reconfigure(encoding='utf-8')
PRODUCT = sys.argv[1] if len(sys.argv) > 1 else 'bois'
cfg = load_product(PRODUCT)
D = data_dir(PRODUCT)
llm = llm_cfg()

CUTOFF_ID, CUTOFF_DATE = cutoff_for(cfg)
P_MIN, P_MAX = cfg['price_min'], cfg['price_max']
SYS = cfg['prompts']['title_system']
USER_FMT = cfg['prompts']['title_user']
MAX_TOKENS = cfg.get('title_max_tokens', 300)
WORKERS = cfg.get('workers', 4)

items = json.load(io.open(os.path.join(D, 'merged.json'), encoding='utf-8'))
print('input: %d' % len(items))

def clean_title(t):
    return re.split(r',\s*Marque:', t)[0].strip()

cand = []
for it in items:
    if it.get('price') is None or it['price'] < P_MIN or it['price'] > P_MAX:
        continue
    if it['iid'] < CUTOFF_ID:
        continue
    it['title_clean'] = clean_title(it['title'])
    cand.append(it)
print('after price %g-%g + recency (id>=%d, %s): %d' % (P_MIN, P_MAX, CUTOFF_ID, CUTOFF_DATE, len(cand)))

def llm_classify(it):
    body = json.dumps({
        'model': llm['model'],
        'messages': [
            {'role': 'system', 'content': SYS},
            {'role': 'user', 'content': USER_FMT.format(
                title=it['title_clean'], brand=it.get('listed_brand', ''))}
        ],
        'temperature': 0.0,
        'max_tokens': MAX_TOKENS,
        'stream': False,
    }).encode('utf-8')
    for attempt in range(4):
        try:
            req = urllib.request.Request(llm['url'], data=body, headers=llm_headers())
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode('utf-8'))
            txt = d['choices'][0]['message'].get('content', '').strip().upper()
            for k in ('BLADE', 'RACKET', 'RUBBER', 'OTHER'):
                if k in txt:
                    return it['url'], k
            return it['url'], 'OTHER'
        except Exception:
            time.sleep(2 + attempt * 2)
    return it['url'], 'ERROR'

partial = os.path.join(D, 'cls_partial.json')
done = {}
if os.path.exists(partial):
    prev = json.load(io.open(partial, encoding='utf-8'))
    if isinstance(prev, dict):
        done.update(prev)
    else:  # legacy list format
        for r in prev:
            done[r['url']] = r['class']
    done = {k: v for k, v in done.items() if v != 'ERROR'}
    print('resuming, already done: %d' % len(done))

todo = [it for it in cand if it['url'] not in done]
print('to classify: %d' % len(todo))
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(llm_classify, it): it for it in todo}
    for n, f in enumerate(as_completed(futs), 1):
        url, cls = f.result()
        done[url] = cls
        if n % 20 == 0:
            el = time.time() - t0
            print('  %d/%d  %.0fs elapsed, ~%.0fs eta' % (n, len(todo), el, el / n * (len(todo) - n)), flush=True)
        if n % 25 == 0:
            json.dump(done, io.open(partial, 'w', encoding='utf-8'), ensure_ascii=False)

results = []
for it in cand:
    d = dict(it); d['class'] = done[it['url']]
    results.append(d)
json.dump(results, io.open(os.path.join(D, 'classified.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
from collections import Counter
print('classes: %s' % Counter(r['class'] for r in results))
print('saved %s' % os.path.join(D, 'classified.json'))
