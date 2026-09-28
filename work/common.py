"""Shared helpers: product config, paths, LLM settings.

A "product" is one Vinted search profile defined in <products_dir>/<name>/<name>.json
(products_dir: config.json "products_dir", default <root>/products; e.g.
products/bois/bois.json -> outputs products/bois/vinted/bois.html).
"""
import json, io, os, sys
from datetime import date, timedelta

WORK = os.path.dirname(os.path.abspath(__file__))          # <root>/work
ROOT = os.path.dirname(WORK)                                # <root>


def _global_cfg():
    """config.json at the repo root (LLM settings + shared paths)."""
    p = os.path.join(ROOT, 'config.json')
    if os.path.exists(p):
        return json.load(io.open(p, encoding='utf-8'))
    return {}


# products folder: config.json "products_dir" (relative to root, or absolute),
# default "products"
_products_rel = _global_cfg().get('products_dir', 'products')
PRODUCTS_DIR = (_products_rel if os.path.isabs(_products_rel)
                else os.path.join(ROOT, _products_rel))


def load_product(name):
    p = os.path.join(PRODUCTS_DIR, name, name + '.json')
    if not os.path.exists(p):
        available = sorted(d for d in (os.listdir(PRODUCTS_DIR) if os.path.isdir(PRODUCTS_DIR) else [])
                           if os.path.isfile(os.path.join(PRODUCTS_DIR, d, d + '.json')))
        sys.exit('unknown product "%s" (available: %s)' % (name, ', '.join(available) or 'none'))
    cfg = json.load(io.open(p, encoding='utf-8'))
    cfg['name'] = name
    _fill_prompts(cfg)
    return cfg


# ------------------------------------------------------------- classes ----
# A product defines its own free-form class names (e.g. "bois"/"raquette" for
# table tennis, "rtx 3080"/"other nvidia card" for GPUs) in the "classes"
# object: "primary" (always kept) + optional "secondary" (also kept) and an
# optional "auxiliary" trap class (never kept). OTHER is the universal reject
# word, fixed for all products. The LLM reply is parsed against these names.

REJECT_CLASS = 'OTHER'


def keep_names(cfg):
    """Class names that count as the product: primary + secondary (when set)."""
    out = []
    for slot in ('primary', 'secondary'):
        c = (cfg.get('classes') or {}).get(slot) or {}
        if c.get('name'):
            out.append(c['name'])
    return out


def aux_class(cfg):
    """The auxiliary (trap) class dict, or None when the product has none."""
    a = cfg.get('auxiliary') or {}
    return a if a.get('name') else None


def all_names(cfg):
    """Every class name the model may reply: keep names + auxiliary (when set)."""
    a = aux_class(cfg)
    return keep_names(cfg) + ([a['name']] if a else [])


def parse_reply(cfg, txt):
    """Match a model reply to the product's class names.

    Plain substring match, longest name first (so a multi-word name wins over a
    shorter name it contains). Falls back to OTHER when nothing matches.
    """
    t = (txt or '').upper()
    for name in sorted(all_names(cfg) + [REJECT_CLASS], key=len, reverse=True):
        if name.upper() in t:
            return REJECT_CLASS if name == REJECT_CLASS else name
    return REJECT_CLASS


def _fill_prompts(cfg):
    """Expand product variables into the prompt templates so the 'prompts'
    block stays generic and reusable across products.

    Variables (from the 'vars' section + values derived from the 'classes'
    object):
      {product}      product label (vars.product)
      {keep_names}   primary + secondary class names joined, e.g. "bois and raquette"
      {all_names}    keep names + auxiliary (when set), e.g. "bois, raquette, gomme"
      {class_defs}   "BOIS = <def>. RAQUETTE = <def>" from the classes defs
      {aux_name}     auxiliary class name (optional)
      {aux_def}      auxiliary class definition (optional)
      {other_note}   vars.other_note
    Runtime-only placeholders ({title}, {brand}) are left untouched for the
    per-item .format() call in each pipeline step.
    """
    vars_ = cfg.get('vars', {})
    keep = keep_names(cfg)
    aux = aux_class(cfg)
    class_defs = ' '.join('%s = %s' % ((cfg['classes'][s])['name'].upper(),
                                       (cfg['classes'][s])['def'])
                          for s in ('primary', 'secondary')
                          if (cfg.get('classes') or {}).get(s, {}).get('name'))
    class _Keep(dict):
        """Missing keys stay as literal {key} (runtime placeholders like {title})."""
        def __missing__(self, key):
            return '{' + key + '}'

    fmt = _Keep(vars_)
    fmt['keep_names'] = ' and '.join(keep)
    fmt['all_names'] = ', '.join(keep + ([aux['name']] if aux else []))
    fmt['class_defs'] = class_defs
    if aux:
        fmt['aux_name'] = aux['name']
        fmt['aux_def'] = aux.get('def', '')
    prompts = cfg.get('prompts', {})
    for k, v in prompts.items():
        prompts[k] = v.format_map(fmt)


def data_dir(name):
    d = os.path.join(WORK, 'data', name)
    os.makedirs(d, exist_ok=True)
    return d


def out_dir(name):
    """Per-product result folder: products/<name>/vinted (config is a sibling)."""
    d = os.path.join(PRODUCTS_DIR, name, 'vinted')
    os.makedirs(d, exist_ok=True)
    return d


def llm_cfg():
    """LLM endpoint/model/key: set by run.ps1 from config.json; defaults below."""
    url = os.environ.get('VT_LLM_URL', 'http://127.0.0.1:1234').rstrip('/')
    return {
        'url': url + '/v1/chat/completions',
        'model': os.environ.get('VT_LLM_MODEL', 'unsloth/qwen3.8-27b'),
    }


def llm_headers():
    h = {'Content-Type': 'application/json'}
    key = os.environ.get('VT_LLM_API_KEY', '')
    if key:
        h['Authorization'] = 'Bearer ' + key
    return h


def age_window():
    """(cutoff_id, cutoff_date) for a product's age window, from the ID->date fit."""
    fit = json.load(io.open(os.path.join(WORK, 'id_date_fit.json'), encoding='utf-8'))
    today = date.today()
    return fit, today


def cutoff_for(cfg):
    """Minimum item ID for the product's age window. Returns (cutoff_id, cutoff_date)."""
    fit, today = age_window()
    days = round(cfg.get('age_months', 4) * 30.44)
    cutoff_date = today - timedelta(days=days)
    cutoff_id = int(round((cutoff_date.toordinal() - fit['intercept']) / fit['slope']))
    return cutoff_id, cutoff_date
