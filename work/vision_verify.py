"""Step 3: re-classify title-kept items ON THE PHOTO (image + title).

Drops false positives (e.g. rubbers mislabeled as rackets, bags, clothing).
"""
import json, io, os, sys, time, base64, urllib.request
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_product, data_dir, llm_cfg, keep_names, classify_reply, ESCALATE_BUDGET

sys.stdout.reconfigure(encoding='utf-8')
PRODUCT = sys.argv[1] if len(sys.argv) > 1 else 'bois'
cfg = load_product(PRODUCT)
D = data_dir(PRODUCT)
llm = llm_cfg()

SYS = cfg['prompts']['vision_system']
USER_FMT = cfg['prompts']['vision_user']
# Small budget for the common case; classify_reply() escalates to 4000 for the
# few photos whose reasoning exhausts it, then rejects if still empty.
MAX_TOKENS = cfg.get('vision_max_tokens', 400)
KEEP = set(keep_names(cfg))
WORKERS = cfg.get('vision_workers', 3)

def fetch_img_png_b64(url):
    """Download the (webp) image and return base64 of a PNG re-encode.

    The local server rejects webp bytes; PNG is accepted. Retried; raises on
    final failure."""
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                                       'Referer': 'https://www.vinted.fr/'})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            buf = io.BytesIO()
            Image.open(io.BytesIO(raw)).convert('RGB').save(buf, 'PNG')
            return base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            last = e
            time.sleep(2 + attempt * 2)
    raise last

def verify(it):
    try:
        b64 = fetch_img_png_b64(it['img'])
    except Exception as e:
        return it['url'], 'ERROR:' + str(e)[:60]
    messages = [{'role': 'system', 'content': SYS},
                {'role': 'user', 'content': [
                    {'type': 'text',
                     'text': USER_FMT.format(title=it['title_clean'],
                                              brand=it.get('listed_brand', ''))},
                    {'type': 'image_url',
                     'image_url': {'url': 'data:image/png;base64,' + b64}}]}]
    return it['url'], classify_reply(llm, cfg, messages, MAX_TOKENS, timeout=180)

items = json.load(io.open(os.path.join(D, 'classified.json'), encoding='utf-8'))
keep = [x for x in items if x['class'] in KEEP]
print('verifying %d items with vision ...' % len(keep), flush=True)

partial = os.path.join(D, 'vision_partial.json')
done = {}
if os.path.exists(partial):
    done = json.load(io.open(partial, encoding='utf-8'))
    print('resuming, done: %d' % len(done), flush=True)

todo = [it for it in keep if it['url'] not in done]
print('to verify: %d' % len(todo), flush=True)
t0 = time.time()
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(verify, it): it for it in todo}
    for n, f in enumerate(as_completed(futs), 1):
        url, cls = f.result()
        done[url] = cls
        el = time.time() - t0
        print('  %d/%d  %s -> %s  (~%ds eta)' % (
            n, len(todo), done[url], url.split('/items/')[1][:30],
            int(el / n * (len(todo) - n))), flush=True)
        if n % 10 == 0:
            json.dump(done, io.open(partial, 'w', encoding='utf-8'))

json.dump(done, io.open(partial, 'w', encoding='utf-8'))
from collections import Counter
print('vision results: %s' % Counter(done.values()))
print('saved %s' % partial)
