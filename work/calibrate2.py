import re, io, sys
from datetime import datetime, date

sys.stdout.reconfigure(encoding='utf-8')
by_id = {}
for line in io.open('wayback_recent.txt', encoding='utf-8', errors='replace'):
    m = re.match(r'(\d{8})\d*\s+https://www\.vinted\.fr/items/(\d+)-', line)
    if not m:
        continue
    ts, iid = m.groups()
    iid = int(iid)
    d = datetime.strptime(ts[:8], '%Y%m%d').date()
    if iid not in by_id or d < by_id[iid]:
        by_id[iid] = d   # oldest (first) snapshot per id

pairs = sorted(by_id.items())
print('unique item ids:', len(pairs))
xs = [p[0] for p in pairs]
ys = [p[1].toordinal() for p in pairs]
print('id range:', min(xs), '-', max(xs))
print('snap range:', date.fromordinal(min(ys)), '-', date.fromordinal(max(ys)))

# Use only the TOP quartile of ids (most recently created => tightest bounds)
n = len(xs)
top = pairs[n*3//4:]
tx = [p[0] for p in top]
ty = [p[1].toordinal() for p in top]
nt = len(tx)
sx, sy = sum(tx), sum(ty)
sxx = sum(x*x for x in tx)
sxy = sum(x*y for x, y in zip(tx, ty))
slope = (nt*sxy - sx*sy) / (nt*sxx - sx*sx)   # days per id
intercept = (sy - slope*sx) / nt
print(f'\nfit (top quartile, n={nt}): ordinal = {slope:.6e} * id + {intercept:.3f}')

def pred_date(iid):
    return date.fromordinal(int(round(slope*iid + intercept)))

# residuals on the fit set
res = sorted(abs(int(round(slope*x+intercept)) - y) for x, y in zip(tx, ty))
print('median abs residual (days):', res[len(res)//2])
print('p90 abs residual (days):', res[int(len(res)*0.9)])
print('p95 abs residual (days):', res[int(len(res)*0.95)])

print('\nspot predictions:')
for iid in [9495432983, 9963046444, 10004090622, 10015468360]:
    print(f'  id {iid} -> {pred_date(iid)}')

# Inverse: id predicted to be created on a given date
def id_for_date(d):
    return int(round((d.toordinal() - intercept) / slope))

TODAY = date(2026, 9, 15)
CUTOFF_DATE = date(2026, 5, 15)   # 4 months ago
cutoff_id = id_for_date(CUTOFF_DATE)
print(f'\nTODAY={TODAY}  CUTOFF_DATE={CUTOFF_DATE}')
print(f'CUTOFF_ID (created >= cutoff): {cutoff_id}')
print(f'  sanity: pred_date(cutoff_id) = {pred_date(cutoff_id)}')
print(f'  pred_date(TODAY-id ~) = {pred_date(id_for_date(TODAY))}  (id {id_for_date(TODAY)})')

# save the fit for the pipeline
io.open('id_date_fit.json', 'w').write(__import__('json').dumps({
    'slope': slope, 'intercept': intercept, 'cutoff_id': cutoff_id,
    'cutoff_date': CUTOFF_DATE.isoformat(), 'today': TODAY.isoformat(),
    'median_resid_days': res[len(res)//2]
}))
print('\nsaved id_date_fit.json')
