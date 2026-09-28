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


def _fill_prompts(cfg):
    """Expand product variables into the prompt templates so the 'prompts'
    block stays generic and reusable across products.

    Variables (from the 'vars' section + values derived from the config):
      {product}          product label, e.g. "table-tennis blade"
      {classes}          keep_classes joined, e.g. "BLADE, RACKET"
      {other_classes}    keep_classes + auxiliary_class (when defined),
                         e.g. "BLADE, RACKET, RUBBER"; just keep_classes when
                         the product has no auxiliary class
      {class_defs}       "BLADE = <def>. RACKET = <def>" from vars.product_class
      {auxiliary_class}  vars.auxiliary_class (optional; absent for products
                         with a single kept class)
      {auxiliary_note}   vars.auxiliary_note (optional)
      {other_note}       vars.other_note
      {auxiliary_hint}   vars.auxiliary_hint (optional)
    Runtime-only placeholders ({title}, {brand}) are left untouched for the
    per-item .format() call in each pipeline step.
    """
    vars_ = cfg.get('vars', {})
    keep = cfg.get('keep_classes', [])
    product_class = vars_.get('product_class', {})
    class_defs = ' '.join('%s = %s' % (k, product_class[k])
                          for k in keep if k in product_class)
    aux = [vars_['auxiliary_class']] if 'auxiliary_class' in vars_ else []
    class _Keep(dict):
        """Missing keys stay as literal {key} (runtime placeholders like {title})."""
        def __missing__(self, key):
            return '{' + key + '}'

    fmt = _Keep(vars_)
    fmt['classes'] = ', '.join(keep)
    fmt['keep_classes'] = ' and '.join(keep)
    fmt['other_classes'] = ', '.join(keep + aux)
    fmt['class_defs'] = class_defs
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
