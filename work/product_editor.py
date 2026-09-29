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
import argparse, copy, io, json, os, re, select, socket, sys, threading, time, urllib.parse

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
_DBG_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_debug.log')


def _dbg(msg):
    """Append one timestamped line to work/llm_debug.log (diagnostic only —
    request start/end, total bytes, and any gap of >2 s without data, which is
    the signal that matters for 'client disconnected' diagnosis)."""
    try:
        with io.open(_DBG_LOG, 'a', encoding='utf-8') as f:
            f.write('%s %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


GEN_LADDER = [4000, 8000]
# Idle window: fires only when the server sends NO data for this long. Generous
# on purpose — a request queued behind another generation (e.g. the pipeline
# running at the same time) is legitimately silent for minutes. The Cancel
# button is always the escape hatch; this only catches a truly dead server.
GEN_TIMEOUT = 600


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
                # Drain for up to ~1 s before close(): closing a socket that
                # still has unread data in its buffer sends a RST on Windows,
                # which LM Studio logs as "client disconnected". A brief
                # bounded drain lets close() send a clean FIN instead.
                c.settimeout(0.5)
                end = time.monotonic() + 1.0
                while time.monotonic() < end:
                    try:
                        if not c.recv(65536):
                            break
                    except OSError:
                        break
                c.close()
            except Exception:
                pass


def _recv_poll(sock, state, timeout):
    """Receive one chunk, polling with select() so the cancel flag is checked
    every ~0.25 s — a blocking socket read cannot be interrupted from another
    thread on Windows, so this polling is what makes Cancel responsive.

    `timeout` is an IDLE window, not a total cap: it fires only when NO byte
    has arrived for that long. A healthy stream never trips it — the server
    keeps sending (even hidden reasoning_content deltas) the whole time the
    model thinks; a stuck/dead server does trip it. Kicking a healthy
    generation for running "too long in total" would close the socket under
    LM Studio mid-generation ("client disconnected").

    Raises Cancelled when state.cancel() has been called; RuntimeError when
    the server stays silent for `timeout` seconds; returns b'' on clean EOF.
    Any silence gap >= 2 s is logged (diagnostic for 'client disconnected')."""
    last = time.monotonic()
    last_byte = time.monotonic()
    warned = None
    while True:
        if state.cancelled:
            raise Cancelled()
        try:
            r, _, _ = select.select([sock], [], [], 0.25)
        except OSError:
            if state.cancelled:  # GUI thread closed the socket under us
                raise Cancelled()
            raise
        if not r:
            gap = time.monotonic() - last_byte
            if gap >= 2 and (warned is None or gap >= warned + 10):
                _dbg('  SILENCE gap %.1f s (no data from server yet)' % gap)
                warned = gap
            if time.monotonic() - last > timeout:
                raise RuntimeError('no data from the LLM server for %d s' % timeout)
            continue
        try:
            chunk = sock.recv(65536)
        except OSError:
            if state.cancelled:
                raise Cancelled()
            raise
        if chunk:
            last_byte = time.monotonic()
            warned = None
        return chunk or b''


class _SSE:
    """Incremental body reader: de-chunks HTTP chunked transfer-encoding when
    present (LM Studio uses it), normalizes line endings, and yields one SSE
    data payload per call. Reads poll the cancel flag, so Cancel stays
    responsive. `initial` is the body already received past the response
    headers; it is chunked framing or plain body depending on `chunked`."""

    def __init__(self, sock, state, timeout, initial, chunked):
        self.sock, self.state = sock, state
        self.timeout = timeout  # idle window (no total cap — see _recv_poll)
        self._chunked = chunked
        self._raw = initial if chunked else b''   # chunked framing bytes
        self._buf = b'' if chunked else initial   # de-chunked body
        self._eof = False

    def _read_wire(self):
        """Append one raw socket read (polling cancel). True if data arrived."""
        chunk = _recv_poll(self.sock, self.state, self.timeout)
        if not chunk:
            self._eof = True
            return False
        if self._chunked:
            self._raw += chunk
        else:
            self._buf += chunk
        return True

    def _next_chunk_payload(self):
        """Consume one chunk of framing from `_raw`, appending its payload to
        `_buf`. Returns False at the terminating 0-size chunk."""
        while b'\r\n' not in self._raw:
            if not self._read_wire():
                raise RuntimeError('connection closed mid-stream')
        size_str, self._raw = self._raw.split(b'\r\n', 1)
        size = int(size_str.split(b';')[0], 16)
        if size == 0:
            self._eof = True
            return False
        while len(self._raw) < size + 2:
            if not self._read_wire():
                raise RuntimeError('connection closed mid-chunk')
        self._buf += self._raw[:size]
        self._raw = self._raw[size + 2:]
        return True

    def _advance(self):
        """Get more de-chunked data into `_buf`. False when the stream ends."""
        if self._chunked:
            if self._eof and not self._raw:
                return False
            return self._next_chunk_payload()
        return not self._eof and self._read_wire()

    def next_data(self):
        """Next SSE data payload (bytes), or None at end of stream."""
        while True:
            if b'\n' in self._buf:
                line, self._buf = self._buf.split(b'\n', 1)
                line = line.rstrip(b'\r')
                if line.startswith(b'data:'):
                    data = line[5:].strip()
                    if data:
                        return data
                continue
            if not self._advance():
                tail = self._buf.strip()
                self._buf = b''
                if tail.startswith(b'data:') and tail[5:].strip():
                    return tail[5:].strip()  # unterminated final line
                return None


def _read_sse(sock, state, timeout):
    """Read a streaming chat completion and fold it back into the shape of a
    non-streaming response: {'choices': [{'message': {'content'},
    'finish_reason'}]}.

    Uses `stream: true`, so the server pushes tokens as they arrive — data to
    poll between reasoning pauses. Abnormal stream end without a
    finish_reason is reported as 'length' (truncated: the bounded ladder may
    escalate, and no JSON re-roll is attempted)."""
    head = b''
    while b'\r\n\r\n' not in head:  # response headers
        chunk = _recv_poll(sock, state, timeout)
        if not chunk:
            raise RuntimeError('connection closed while reading the response')
        head += chunk
    header, rest = head.split(b'\r\n\r\n', 1)
    status = int(header.decode('utf-8', 'replace').split('\r\n')[0].split()[1])
    if status != 200:
        raise RuntimeError('LLM HTTP %s' % status)
    chunked = any(hl.startswith('transfer-encoding:') and 'chunked' in hl
                  for hl in header.decode('utf-8', 'replace').lower().split('\r\n'))
    sse = _SSE(sock, state, timeout, rest, chunked)
    parts, finish = [], None
    while True:
        data = sse.next_data()
        if data is None:
            break  # clean EOF / 0-size chunk
        if data == b'[DONE]':
            break
        try:
            d = json.loads(data.decode('utf-8'))
        except Exception:
            continue
        choices = d.get('choices') or []
        if not choices:
            continue
        piece = (choices[0].get('delta') or {}).get('content')
        if piece:
            parts.append(piece)
        if choices[0].get('finish_reason'):
            finish = choices[0]['finish_reason']
    content = ''.join(parts)
    return {'choices': [{'message': {'content': content},
                         'finish_reason': finish or ('length' if not content else 'stop')}]}


def _http_chat(llm, messages, max_tokens, state, timeout=GEN_TIMEOUT, model=None):
    """One streaming chat completion over a raw socket (no worker thread —
    the reads poll the cancel flag themselves). `state.conn` holds the socket
    so Cancel can close it and stop the server-side generation. Returns a
    response-shaped dict; raises Cancelled on cancel, RuntimeError on
    network error / timeout."""
    p = urllib.parse.urlparse(llm['url'])
    sock = None
    t0 = time.monotonic()
    try:
        sock = socket.create_connection((p.hostname, p.port or 80))
        state.conn = sock
        model = (model or '').strip() or llm['model']
        body = json.dumps({'model': model, 'messages': messages,
                           'temperature': 0.0, 'max_tokens': max_tokens,
                           'stream': True}).encode('utf-8')
        headers = dict(common.llm_headers())
        headers['Accept'] = 'text/event-stream'
        req = ('POST %s HTTP/1.1\r\nHost: %s\r\n' % (p.path or '/', p.hostname)
               + ''.join('%s: %s\r\n' % (k, v) for k, v in headers.items())
               + 'Content-Length: %d\r\n\r\n' % len(body))
        sock.sendall(req.encode('utf-8') + body)
        d = _read_sse(sock, state, timeout)
        c = d['choices'][0]
        _dbg('REQ model=%s max_tokens=%d -> finish=%s content=%d bytes in %.1f s'
             % (model, max_tokens, c.get('finish_reason'),
                len(c['message'].get('content', '') or ''), time.monotonic() - t0))
        return d
    except Cancelled:
        _dbg('REQ model=%s max_tokens=%d CANCELLED after %.1f s'
             % (model, max_tokens, time.monotonic() - t0))
        raise
    except (OSError, RuntimeError) as e:
        _dbg('REQ model=%s max_tokens=%d FAILED after %.1f s: %s'
             % (model, max_tokens, time.monotonic() - t0, e))
        if state.cancelled:  # e.g. socket close raced the flag
            raise Cancelled()
        raise
    finally:
        state.conn = None
        try:
            sock.close()
        except Exception:
            pass


def llm_call(messages, budgets=GEN_LADDER, timeout=GEN_TIMEOUT, state=None, model=None):
    """Bounded, cancellable generation. Tries each max_tokens budget in order;
    a budget 'exhausts' when finish_reason is 'length' (empty/truncated
    answer). Returns (content, finish_reason); finish_reason is 'timeout' if
    the ladder never produced a non-length response. Raises Cancelled if the
    user hits Cancel mid-generation. `model` overrides the configured model
    (the editor's model dropdown)."""
    if state is None:
        state = GenState()
    llm = common.llm_cfg()
    content, finish = '', 'timeout'
    for b in budgets:
        if state.cancelled:
            raise Cancelled()
        d = _http_chat(llm, messages, b, state, timeout, model=model)
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
            'Also set the top-level "description" field to a plain-English "what to find" '
            "for this product (a concise version of the description above). "
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


def on_exit():
    """Exit button: close the tab AND kill this editor's process.

    os._exit(0) (not sys.exit): the server runs in this process, so a hard
    quit is what frees the port. The exit runs on a daemon thread after a
    short delay so the "Closing…" status returned here is already on its way
    to the client before the process dies; window.close() (fired client-side
    by the button's `js` hook) closes the tab where the browser allows it."""
    _dbg('GUI exit: process %d shutting down' % os.getpid())

    def _die():
        time.sleep(0.5)      # let the status response flush before we vanish
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()
    return 'Closing this tab and shutting down the editor…'


def generate(name, description, brands, pmin, pmax, age, max_items, title, model=None, _cancel=None):
    """LLM -> full GUI output tuple. Does NOT save. Bounded: never hangs, and
    cancellable: `_cancel` (a GenState) is what the Cancel button closes.
    `model` is the editor's selected model (overrides config.json).

    Returns (name, description, brands, pmin, pmax, age, max_items, title,
    json_text, status, gen_btn). It ECHOES BACK every input field it consumed:
    those fields are normally edited client-side only (no handler outputs to
    them), so without this, Gradio would revert them to their last server-
    known value the moment the result lands (e.g. a name you renamed from
    rtx3080 -> rtx3090 would snap back to rtx3080). Echoing pins each field to
    the exact value the LLM used. The last element re-enables the Generate
    button (the GUI's first event disables it), on EVERY return path."""
    global _current
    # gr.update(...) is just this dict; built inline so the headless CLI path
    # never needs gradio imported.
    reenable = {'interactive': True, '__type__': 'update'}
    fields = (name, description, brands, pmin, pmax, age, max_items, title)
    _dbg('GUI generate: name=%r description=%r' % (name, description))
    if not (name and description):
        return (*fields, '', 'Fill at least a product name and a description.', reenable)
    state = _cancel or GenState()
    _current = state
    try:
        txt, status = _gen_body(state, name, description, brands, pmin, pmax, age, max_items, title, model)
        return (*fields, txt, status, reenable)
    except Exception as e:  # last-resort net: re-enable the button either way
        return (*fields, '', ('Generation failed: %s.' % e), reenable)
    finally:
        if _current is state:
            _current = None


def _gen_body(state, name, description, brands, pmin, pmax, age, max_items, title, model=None):
    msgs = [{'role': 'system', 'content': build_system()},
            {'role': 'user', 'content': build_user(name, description, brands, pmin, pmax, age, max_items, title)}]
    try:
        txt, finish = llm_call(msgs, state=state, model=model)
    except Cancelled:
        return '', 'Cancelled - the LLM call was stopped. Fix the input and try again.'
    except Exception as e:  # network error / idle timeout — show it, don't trace
        return '', ('The LLM call failed: %s. The server may be busy or the '
                    'connection dropped — try again.' % e)
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
            txt2, _ = llm_call(msgs, state=state, model=model)
        except Cancelled:
            return '', 'Cancelled - the LLM call was stopped. Fix the input and try again.'
        except Exception as e:
            return txt, ('The follow-up LLM call failed: %s. The raw reply is '
                         'shown below - edit it, then Save.' % e)
        js2 = extract_json(txt2)
        if js2 is not None:
            txt, js = txt2, js2
    if js is None:
        why = ('the model spent its whole token budget thinking and never '
               'produced a full answer' if finish in ('length', 'timeout')
               else 'the model did not return valid JSON')
        return txt, 'Could not build the JSON (%s). Raw reply shown below - edit it, then Save.' % why
    if isinstance(js, dict):
        if 'max_items' not in js:
            js['max_items'] = int(max_items) if isinstance(max_items, (int, float)) else 200
        if 'description' not in js:
            js['description'] = description  # keep the round-trip to Load working
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


def list_products():
    """Markdown list of every configured product (name, title, brands, price)."""
    entries = []
    try:
        dirs = sorted(os.listdir(PRODUCTS_DIR))
    except OSError as e:
        return 'Could not list products: %s' % e
    for d in dirs:
        p = os.path.join(PRODUCTS_DIR, d, d + '.json')
        if not os.path.isfile(p):
            continue
        try:
            cfg = json.load(io.open(p, encoding='utf-8'))
        except Exception:
            continue
        brands = ', '.join(cfg.get('brands', [])) or '(none)'
        entries.append('- **%s** — %s (brands: %s, €%s–%s)'
                       % (d, cfg.get('title', ''), brands,
                          cfg.get('price_min', '?'), cfg.get('price_max', '?')))
    return '\n'.join(entries) if entries else 'No products configured yet.'


def load_existing(name):
    name = (name or '').strip().lower()
    p = os.path.join(PRODUCTS_DIR, name, name + '.json')
    if not os.path.exists(p):
        # 9 outputs: brands, pmin, pmax, age, max_items, title, description, json, status
        return (None, None, None, None, None, '', '', '',
                'No product named "%s" to load.' % name)
    cfg = json.load(io.open(p, encoding='utf-8'))
    return (', '.join(cfg.get('brands', [])), cfg.get('price_min'), cfg.get('price_max'),
            cfg.get('age_months', 4), cfg.get('max_items', 200),
            cfg.get('title', ''), cfg.get('description', ''),
            json.dumps(cfg, ensure_ascii=False, indent=2),
            'Loaded %s into the editor.' % name)


# Field defaults shared by the GUI initial values and the Clear button.
DEFAULTS = dict(name='', price_min=10, price_max=30, age=4, max_items=200,
                brands='', title='', description='')


def clear():
    """Flush all input fields, the generated JSON, and the status to defaults."""
    d = DEFAULTS
    return (d['name'], d['price_min'], d['price_max'], d['age'], d['max_items'],
            d['brands'], d['title'], d['description'], '', 'Cleared. Ready for a new product.')


# ----------------------------------------------------------------- GUI ----
def launch_gui(server_name='127.0.0.1', server_port=7860, inbrowser=True):
    import gradio as gr
    llm = common.llm_cfg()

    def query_models():
        """Query the server for its ACTUALLY-LOADED models. Returns (choices,
        value, note). Shows what is running, not just what config.json says;
        if the configured model isn't loaded, pick the largest loaded model
        (the one that can do real work) and flag it in `note` (small text
        shown under the dropdown)."""
        loaded, cur, err = common.list_models()
        ids = [m['id'] for m in loaded]
        if err:
            return [cur], cur, '⚠ could not query the server: %s' % err
        if not ids:
            return [cur], cur, 'No models loaded on the server.'
        choices = list(ids)  # ONLY models actually loaded on the server
        if cur in ids:
            return choices, cur, ''
        best = max(loaded, key=lambda m: m['ctx'])
        return (choices, best['id'],
                '⚠ configured model %s is NOT loaded — using %s (%d ctx). '
                'Switch the model in LM Studio or pick one above.'
                % (cur, best['id'], best['ctx']))

    def note_html(note):
        # gr.HTML (not Markdown): Markdown sanitizes inline style attributes;
        # the note renders in small amber text, empty when there is none.
        return ('<span style="font-size: 0.75em; color: #b45309;">%s</span>' % note
                if note else '')

    base = llm['url'].split('/v1/')[0]
    init_choices, init_model, init_note = query_models()

    # Re-color the two primary buttons (default primary is orange).
    css = ('#btn-gen, #btn-save, #btn-gen:hover, #btn-save:hover { '
           'background-color: #335d8f !important; border-color: #335d8f !important; }')
    with gr.Blocks(title='Vinted Product JSON Editor') as demo:
        gr.Markdown('# Vinted product JSON editor')
        with gr.Row():
            with gr.Column(scale=4, min_width=0):
                model_dd = gr.Dropdown(
                    label='Model (loaded on the LLM server: %s)' % base,
                    choices=init_choices, value=init_model,
                    allow_custom_value=True)
                model_info = gr.HTML(note_html(init_note))
            refresh_btn = gr.Button('Refresh models')
        with gr.Row():
            name = gr.Textbox(label='Product name (folder)', placeholder='rtx3080')
            load_btn = gr.Button('Load existing')
            clear_btn = gr.Button('Clear')
        with gr.Row():
            price_min = gr.Number(label='Price min (EUR)', value=DEFAULTS['price_min'])
            price_max = gr.Number(label='Price max (EUR)', value=DEFAULTS['price_max'])
            age = gr.Number(label='Age (months)', value=DEFAULTS['age'])
            max_items = gr.Number(label='Max items (page cap)', value=DEFAULTS['max_items'], precision=0)
        brands = gr.Textbox(label='Brands / makes (comma-separated)', placeholder='nvidia')
        title = gr.Textbox(label='Page title (optional - LLM may choose)')
        description = gr.Textbox(label='Description of the product to find', lines=4,
                                 placeholder='e.g. NVIDIA GeForce RTX 3080 graphics card, standalone')
        with gr.Row():
            gen_btn = gr.Button('Generate JSON with LLM', variant='primary', elem_id='btn-gen')
            cancel_btn = gr.Button('Cancel LLM call', variant='secondary')
        out = gr.Textbox(label='product.json (editable)', lines=22)
        status = gr.Markdown()
        save_btn = gr.Button('Save to products/', variant='primary', elem_id='btn-save')
        list_btn = gr.Button('List products')
        products_list = gr.Markdown()
        # Very bottom of the form: closes the browser tab and kills this
        # process (os._exit frees the port; a plain button click's HTTP
        # response would otherwise wait on the dying server).
        gr.Markdown('<div style="text-align:center;color:#bbb;margin-top:14px;">— — —</div>')
        exit_btn = gr.Button('Exit editor', variant='secondary', elem_id='btn-exit')
        def do_refresh():
            choices, value, note = query_models()
            return (gr.update(choices=choices, value=value),
                    note_html(note))
        refresh_btn.click(do_refresh, None, [model_dd, model_info])
        def disable_gen():
            # First click event: grey out Generate immediately. Re-enabled by
            # generate() on EVERY return path (success / failure / cancel).
            return gr.update(interactive=False)
        gen_btn.click(disable_gen, None, gen_btn) \
              .then(generate,
                    [name, description, brands, price_min, price_max, age, max_items, title, model_dd],
                    [name, description, brands, price_min, price_max, age, max_items, title,
                     out, status, gen_btn])
        cancel_btn.click(on_cancel, None, status)
        save_btn.click(save, [name, out], status)
        list_btn.click(list_products, None, products_list)
        load_btn.click(load_existing, [name],
                       [brands, price_min, price_max, age, max_items,
                        title, description, out, status])
        clear_btn.click(clear, None,
                        [name, price_min, price_max, age, max_items,
                         brands, title, description, out, status])
        # The `js` hook runs client-side first: best-effort tab close (browsers
        # block window.close() on user-opened tabs, so it's a no-op there); the
        # Python handler then hard-kills this process, freeing the port either way.
        exit_btn.click(on_exit, None, status, js='() => { try { window.close(); } catch (e) {} }')
    demo.launch(server_name=server_name, server_port=server_port, inbrowser=inbrowser, css=css)


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
    ap.add_argument('--model', default=None, help='override the LLM model id')
    ap.add_argument('--save', action='store_true', help='write the product file')
    ap.add_argument('--json', help='save this exact JSON string (skip LLM)')
    ap.add_argument('--port', type=int, default=7860)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    if args.json:  # headless save of hand-written JSON
        print(save(args.name, args.json))
        return

    if args.name and args.desc:  # headless LLM generate (+ optional save)
        # generate() returns (8 echoed inputs, json_text, status, reenable);
        # the CLI only wants the json_text and status.
        out = generate(args.name, args.desc, args.brands,
                       args.pmin, args.pmax, args.age, args.max_items, args.title,
                       model=args.model)
        text, msg = out[8], out[9]
        print(text)
        print('\n--- status: %s' % msg)
        if args.save:
            print(save(args.name, text))
        return

    launch_gui(server_port=args.port, inbrowser=not args.no_browser)


if __name__ == '__main__':
    main()
