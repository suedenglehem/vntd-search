"""Shared helpers: product config, paths, LLM settings.

A "product" is one Vinted search profile defined in <root>/products/<name>.json
(e.g. products/bois.json -> searches table-tennis blades, outputs bois/bois.html).
"""
import json, io, os, sys
from datetime import date, timedelta

WORK = os.path.dirname(os.path.abspath(__file__))          # <root>/work
ROOT = os.path.dirname(WORK)                                # <root>
PRODUCTS_DIR = os.path.join(ROOT, 'products')


def load_product(name):
    p = os.path.join(PRODUCTS_DIR, name + '.json')
    if not os.path.exists(p):
        available = sorted(f[:-5] for f in os.listdir(PRODUCTS_DIR) if f.endswith('.json')) \
            if os.path.isdir(PRODUCTS_DIR) else []
        sys.exit('unknown product "%s" (available: %s)' % (name, ', '.join(available) or 'none'))
    cfg = json.load(io.open(p, encoding='utf-8'))
    cfg['name'] = name
    return cfg


def data_dir(name):
    d = os.path.join(WORK, 'data', name)
    os.makedirs(d, exist_ok=True)
    return d


def out_dir(name):
    d = os.path.join(ROOT, name)
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
