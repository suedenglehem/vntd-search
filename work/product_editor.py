"""Product JSON editor.

Gradio UI (or headless CLI) that uses the local LLM to turn a plain-English
product description + brands + price range + age window into a complete,
validated `products/<name>/<name>.json` compatible with the pipeline.

The LLM is pointed at the same server as the pipeline (config.json: llm_url /
model / api_key), so it is automatically configurable.

GUI:      python product_editor.py
Headless: python product_editor.py --name rtx3080 --desc "NVIDIA RTX 3080 card" \
              --brands nvidia --pmin 200 --pmax 500 --age 3 --save
"""
import argparse, copy, http.client, io, json, os, re, sys, threading, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common

PRODUCTS_DIR = common.PRODUCTS_DIR
SPEC = os.path.join(PRODUCTS_DIR, 'JSON.md')
EXAMPLES = {
    'full (2 kept classes + trap)': os.path.join(PRODUCTS_DIR, 'bois', 'bois.json'),
    'minimal (1 kept class, no trap)': os.path.join(PRODUCTS_DIR, 'stiga_blade', 'stiga_blade.json'),
}


# ----------------------------------------------------------------- LLM ----
# The local model is a *reasoning* model: hidden reasoning is billed against
# max_tokens and the answer comes last, and an unbounded budget on a big
# system prompt (spec + two examples) lets it "think" for minutes. So
# generation is BOUNDED: start at a modest budget, and only if that budget is
# exhausted (finish_reason 'length' -> empty or truncated answer) escalate to
# the cap. A hard per-attempt client timeout means the GUI never hangs.
GEN_LADDER = [4000, 8000]
GEN_TIMEOUT = 180


class Cancelled(Exception):
    """Raised inside the HTTP worker when the user hits Cancel."""


class GenState:
    """Holds the in-flight HTTP connection + cancel flag for one generation."""
    def __init__(self):
        self.conn = None
        self.cancelled = False

    def cancel(self):
        """Stop the in-flight request: close the socket (aborts the client
        read AND tells the server to stop generating) and set the flag so the
        ladder never starts its next budget level."""
        self.cancelled = True
        c = self.conn
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def _http_chat(llm, messages, max_tokens, state, timeout=GEN_TIMEOUT):
    """One chat completion over a worker-thread http.client connection.

    The request runs in a thread (urllib's blocking read cannot be
    interrupted). `state.conn` holds the connection so Cancel can close it;
    a closed socket makes the worker raise, which we translate to Cancelled.
    Returns the parsed response dict; raises Cancelled on cancel, other
    Exception on network error/timeout."""
    p = urllib.parse.urlparse(llm['url'])
    conn = http.client.HTTPConnection(p.hostname, p.port or 80, timeout=timeout)
    state.conn = conn
    path = p.path + (('?' + p.query) if p.query else '')
    headers = common.llm_headers()
    payload = json.dumps({'model': llm['model'], 'messages': messages,
                          'temperature': 0.0, 'max_tokens': max_tokens,
                          'stream': False})
    result = {'d': None, 'err': None}

    def work():
        try:
            conn.request('POST', path, body=payload, headers=headers)
            r = conn.getresponse()
            raw = r.read()
            result['d'] = (r.status, json.loads(raw.decode('utf-8')))
        except BaseException as e:  # ConnectionResetError/timeout/closed-socket
            result['err'] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        state.cancel()  # hard timeout -> abort like a cancel
        raise Cancelled()
    state.conn = None
    if result['err'] is not None:
        if state.cancelled:
            raise Cancelled()
        raise result['err']
    status, d = result['d']
    if status != 200:
        raise RuntimeError('LLM HTTP %s' % status)
    return d


def llm_call(messages, budgets=GEN_LADDER, timeout=GEN_TIMEOUT, state=None):
    """Bounded, cancellable generation. Tries each max_tokens budget in order;
    a budget 'exhausts' when finish_reason is 'length' (empty/truncated
    answer). Returns (content, finish_reason); finish_reason is 'timeout' if
    the ladder never produced a non-length response. Raises Cancelled if the
    user hits Cancel mid-generation."""
    if state is None:
        state = GenState()
    llm = common.llm_cfg()
    content, finish = '', 'timeout'
    for b in budgets:
        if state.cancelled:
            raise Cancelled()
        d = _http_chat(llm, messages, b, state, timeout)
        c = d['choices'][0]
        content = c['message'].get('content', '') or ''
        finish = c.get('finish_reason')
        if finish != 'length':
            break  # complete answer
    return content, finish


def build_system():
    """The full authoring spec + both reference configs, as the system prompt."""
    parts = [
        "You are an expert author of Vinted product config files. Follow the spec "
        "below EXACTLY, matching its field names, the class model, and the prompt "
        "contract. Then output ONLY a single JSON object for the requested product "
        "- no prose, no markdown code fences.",
        "\n===== SPEC =====\n" + io.open(SPEC, encoding='utf-8').read(),
    ]
    for label, path in EXAMPLES.items():
        if os.path.exists(path):
            parts.append("\n===== EXAMPLE: %s =====\n%s" % (label, io.open(path, encoding='utf-8').read()))
    return "\n".join(parts)


def build_user(name, description, brands, pmin, pmax, age, max_items, title):
    def n(x, d):
        return ('%g' % x) if x not in (None, '') else d
    return ("Product name (folder): %s\n"
            "What to find (description): %s\n"
            "Brands/makes to search, comma-separated: %s\n"
            "Price range (EUR): %s to %s\n"
            "Age window (months): %s\n"
            "Max items on the page (max_items): %s\n"
            "Suggested page title: %s\n\n"
            "Choose the class names (primary; secondary and auxiliary ONLY if the "
            "description genuinely calls for them) from the product itself - e.g. for a "
            "graphics card the primary class might be named after the card, not 'blade'. "
            "Produce the complete product JSON object now. Output only JSON."
            % (name, description, brands or '(none)', n(pmin, ''), n(pmax, ''),
               n(age, 4), n(max_items, 200), title or '(choose a fitting title)'))


def extract_json(txt):
    t = (txt or '').strip()
    t = re.sub(r'^```(?:json)?\s*', '', t)
    t = re.sub(r'\s*```$', '', t)
    try:
        return json.loads(t)
    except Exception:
        a, b = t.find('{'), t.rfind('}')
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except Exception:
                return None
    return None


def validate(cfg):
    """Structural check + prompt-fill check (undefined placeholders). Returns an
    error string or None. Works on a copy so the caller's cfg stays un-filled."""
    for k in ('title', 'brands', 'price_min', 'price_max'):
        if k not in cfg:
            return 'Missing required field: %s' % k
    cls = cfg.get('classes') or {}
    if not (cls.get('primary') or {}).get('name'):
        return 'classes.primary.name is required.'
    for slot in ('primary', 'secondary'):
        c = cls.get(slot)
        if c and not c.get('name'):
            return 'classes.%s.name is required.' % slot
    aux = cfg.get('auxiliary')
    if aux and not aux.get('name'):
        return 'auxiliary.name is required.'
    if not (cfg.get('vars') or {}).get('other_note'):
        return 'vars.other_note is required.'
    prompts = cfg.get('prompts') or {}
    for k in ('title_system', 'title_user', 'vision_system', 'vision_user',
              'audit_system', 'audit_user'):
        if k not in prompts:
            return 'Missing prompt: %s' % k
    test = copy.deepcopy(cfg)
    try:
        common._fill_prompts(test)
    except Exception as e:
        return 'Prompt fill failed: %s' % e
    for k, v in test.get('prompts', {}).items():
        for m in re.findall(r'\{(\w+)\}', v):
            if m not in ('title', 'brand'):
                return 'Prompt "%s" references an undefined placeholder {%%s}.' % (k, m)
    return None


# The in-flight generation the Cancel button can stop (single-user local GUI).
_current = None


def on_cancel():
    """Stop the in-flight LLM call, if any. The worker thread aborts and
    generate() returns a 'Cancelled' status that overwrites this line."""
    s = _current
    if s is None:
        return 'Nothing to cancel.'
    s.cancel()
    return 'Stopping the LLM call…'


def generate(name, description, brands, pmin, pmax, age, max_items, title, _cancel=None):
    """LLM -> (json_text, status). Does NOT save. Bounded: never hangs, and
    cancellable: `_cancel` (a GenState) is what the Cancel button closes."""
    global _current
    if not (name and description):
        return '', 'Fill at least a product name and a description.'
    state = _cancel or GenState()
    _current = state
    try:
        return _gen_body(state, name, description, brands, pmin, pmax, age, max_items, title)
    finally:
        if _current is state:
            _current = None


def _gen_body(state, name, description, brands, pmin, pmax, age, max_items, title):
    msgs = [{'role': 'system', 'content': build_system()},
            {'role': 'user', 'content': build_user(name, description, brands, pmin, pmax, age, max_items, title)}]
    try:
        txt, finish = llm_call(msgs, state=state)
    except Cancelled:
        return '', 'Cancelled - the LLM call was stopped. Fix the input and try again.'
    if finish == 'timeout' and not txt:
        return '', ('The model did not answer within the time limit (its reasoning '
                    'ran out of budget). Try again, or shorten the description.')
    js = extract_json(txt)
    # Retry ONLY when the model finished cleanly (finish 'stop') but the reply
    # wasn't JSON. If the answer was cut off (length) or timed out, a re-roll on
    # the same large prompt rarely helps and just doubles the wait - so show the
    # raw text and let the user finish it by hand instead.
    if js is None and finish == 'stop':
        msgs = msgs + [{'role': 'assistant', 'content': txt},
                       {'role': 'user', 'content':
                        'That was not valid JSON. Output ONLY the JSON object, no prose or fences.'}]
        try:
            txt2, _ = llm_call(msgs, state=state)
        except Cancelled:
            return '', 'Cancelled - the LLM call was stopped. Fix the input and try again.'
        js2 = extract_json(txt2)
        if js2 is not None:
            txt, js = txt2, js2
    if js is None:
        why = ('the model spent its whole token budget thinking and never '
               'produced a full answer' if finish in ('length', 'timeout')
               else 'the model did not return valid JSON')
        return txt, 'Could not build the JSON (%s). Raw reply shown below - edit it, then Save.' % why
    if isinstance(js, dict) and 'max_items' not in js:
        js['max_items'] = int(max_items) if isinstance(max_items, (int, float)) else 200
    return json.dumps(js, ensure_ascii=False, indent=2), 'Generated. Review / edit, then Save.'


def save(name, json_text):
    name = (name or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9_]+', name):
        return 'Invalid product name "%s" (lowercase letters, digits, underscore).' % name
    try:
        cfg = json.loads(json_text)
    except Exception as e:
        return 'The JSON does not parse: %s' % e
    err = validate(cfg)
    if err:
        return 'Validation failed: %s' % err
    d = os.path.join(PRODUCTS_DIR, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + '.json')
    json.dump(cfg, io.open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    return 'Saved %s\nRun it: .\\run.ps1 -Product %s' % (p, name)


def load_existing(name):
    name = (name or '').strip().lower()
    p = os.path.join(PRODUCTS_DIR, name, name + '.json')
    if not os.path.exists(p):
        return None, None, None, None, None, '', 'No product named "%s" to load.' % name
    cfg = json.load(io.open(p, encoding='utf-8'))
    return (', '.join(cfg.get('brands', [])), cfg.get('price_min'), cfg.get('price_max'),
            cfg.get('age_months', 4), cfg.get('max_items', 200),
            json.dumps(cfg, ensure_ascii=False, indent=2),
            'Loaded %s into the editor.' % name)


# ----------------------------------------------------------------- GUI ----
def launch_gui(server_name='127.0.0.1', server_port=7860, inbrowser=True):
    import gradio as gr
    llm = common.llm_cfg()
    with gr.Blocks(title='Vinted Product JSON Editor') as demo:
        gr.Markdown('# Vinted product JSON editor\n'
                    'LLM: `%s` @ `%s`' % (llm['model'], llm['url']))
        with gr.Row():
            name = gr.Textbox(label='Product name (folder)', placeholder='rtx3080')
            load_btn = gr.Button('Load existing')
        with gr.Row():
            price_min = gr.Number(label='Price min (EUR)', value=10)
            price_max = gr.Number(label='Price max (EUR)', value=30)
            age = gr.Number(label='Age (months)', value=4)
            max_items = gr.Number(label='Max items (page cap)', value=200, precision=0)
        brands = gr.Textbox(label='Brands / makes (comma-separated)', placeholder='nvidia')
        title = gr.Textbox(label='Page title (optional - LLM may choose)')
        description = gr.Textbox(label='Description of the product to find', lines=4,
                                 placeholder='e.g. NVIDIA GeForce RTX 3080 graphics card, standalone')
        with gr.Row():
            gen_btn = gr.Button('Generate JSON with LLM', variant='primary')
            cancel_btn = gr.Button('Cancel LLM call', variant='secondary')
        out = gr.Textbox(label='product.json (editable)', lines=22)
        status = gr.Markdown()
        save_btn = gr.Button('Save to products/', variant='primary')
        gen_btn.click(generate,
                      [name, description, brands, price_min, price_max, age, max_items, title],
                      [out, status])
        cancel_btn.click(on_cancel, None, status)
        save_btn.click(save, [name, out], status)
        load_btn.click(load_existing, [name],
                       [brands, price_min, price_max, age, max_items, out, status])
    demo.launch(server_name=server_name, server_port=server_port, inbrowser=inbrowser)


# ----------------------------------------------------------------- CLI ----
def main():
    ap = argparse.ArgumentParser(description='Generate / edit a Vinted product JSON.')
    ap.add_argument('--name')
    ap.add_argument('--desc')
    ap.add_argument('--brands', default='')
    ap.add_argument('--pmin', type=float)
    ap.add_argument('--pmax', type=float)
    ap.add_argument('--age', type=float)
    ap.add_argument('--max-items', type=int, default=200)
    ap.add_argument('--title', default='')
    ap.add_argument('--save', action='store_true', help='write the product file')
    ap.add_argument('--json', help='save this exact JSON string (skip LLM)')
    ap.add_argument('--port', type=int, default=7860)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    if args.json:  # headless save of hand-written JSON
        print(save(args.name, args.json))
        return

    if args.name and args.desc:  # headless LLM generate (+ optional save)
        text, msg = generate(args.name, args.desc, args.brands,
                             args.pmin, args.pmax, args.age, args.max_items, args.title)
        print(text)
        print('\n--- status: %s' % msg)
        if args.save:
            print(save(args.name, text))
        return

    launch_gui(server_port=args.port, inbrowser=not args.no_browser)


if __name__ == '__main__':
    main()
