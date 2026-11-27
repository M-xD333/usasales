"""Owner O5: versioned ranges, never package/net evidence."""
import json
from pathlib import Path

TABLE=json.loads((Path(__file__).parent/'config'/'estimate_v1.json').read_text())


def estimate(category,rows=()):
    seed=TABLE['ranges_kg'].get(category)
    if seed is None:return {}
    # Only measured PACKAGE observations calibrate shipping ranges. Deduplicate alias copies.
    known={}
    for row in rows:
        if row.get('category')!=category or not row.get('weight_pkg_kg'):continue
        p=row.get('weight_pkg_provenance',{})
        key=(p.get('source'),p.get('field'),p.get('ts'),row['weight_pkg_kg'])
        known[key]=row['weight_pkg_kg']
    lower,upper=seed
    if len(known)>=3:
        lower=min(lower,min(known.values())*.75)
        upper=max(upper,max(known.values())*1.25)
    return {'weight_estimate_range_kg':[round(lower,4),round(upper,4)],
            'weight_estimate_basis':{'version':TABLE['version'],'samples':len(known),'kind':'category'}}
