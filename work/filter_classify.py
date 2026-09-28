"""Step 2: price/age filter + LLM title classification (parallel, resumable)."""
import re, io, os, sys, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_product, data_dir, llm_cfg, cutoff_for, classify_reply, ESCALATE_BUDGET

sys.stdout.reconfigure(encoding='utf-8')
PRODUCT = sys.argv[1] if len(sys.argv) > 1 else 'bois'
cfg = load_product(PRODUCT)
D = data_dir(PRODUCT)
llm = llm_cfg()

CUTOFF_ID, CUTOFF_DATE = cutoff_for(cfg)
P_MIN, P_MAX = cfg['price_min'], cfg['price_max']
SYS = cfg['prompts']['title_system']
USER_FMT = cfg['prompts']['title_user']
# Title reasoning needs measured on bois: p50 ~560, p90 ~1500 tokens, with a
# few runaway titles that never stop thinking. So start small and escalate
# 300 -> 1500 -> 4000 (ESCALATE_BUDGET) before giving up (OTHER).
TITLE_LADDER = [cfg.get('title_max_tokens', 300), 1500, ESCALATE_BUDGET]
WORKERS = cfg.get('workers', 4)

# Smoke-test limit (run.ps1 -Limit N, exported as VT_TEST_LIMIT): classify only
# the first N candidates this run. The rest stay unclassified and resume when
# the run completes without -Limit — the checkpoint is shared, so nothing is lost.
TEST_LIMIT = int(os.environ.get('VT_TEST_LIMIT', '0') or 0)

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
    messages = [
        {'role': 'system', 'content': SYS},
        {'role': 'user', 'content': USER_FMT.format(
            title=it['title_clean'], brand=it.get('listed_brand', ''))}
    ]
    return it['url'], classify_reply(llm, cfg, messages, TITLE_LADDER,
                                     timeout=120)

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

remaining = len(cand) - len(done)
todo = [it for it in cand if it['url'] not in done]
if TEST_LIMIT and len(todo) > TEST_LIMIT:
    todo = todo[:TEST_LIMIT]
    print('test limit: classifying first %d of %d remaining (rest resume later)' % (TEST_LIMIT, remaining))
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
        if n % 20 == 0:
            json.dump(done, io.open(partial, 'w', encoding='utf-8'), ensure_ascii=False)
# Final save: the periodic save above may never have fired (e.g. a -Limit run
# of fewer than 20 items), but the checkpoint must persist so a later full run
# resumes where this one stopped instead of re-classifying from scratch.
json.dump(done, io.open(partial, 'w', encoding='utf-8'), ensure_ascii=False)

# Only items actually classified this session (a -Limit run classifies a
# subset; the rest stay for a later full run and rejoin via the checkpoint).
results = []
for it in cand:
    if it['url'] in done:
        d = dict(it); d['class'] = done[it['url']]
        results.append(d)
json.dump(results, io.open(os.path.join(D, 'classified.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1)
from collections import Counter
print('classes: %s' % Counter(r['class'] for r in results))
print('saved %s' % os.path.join(D, 'classified.json'))
