"""O4: explicit package and net weights, never interchangeable; units mandatory."""
import html
from html.parser import HTMLParser
import json
import math
import re
import time
import os
from urllib.parse import urljoin, urlsplit

FACTORS = {'kg': 1, 'kgs': 1, 'kilogram': 1, 'kilograms': 1, 'kgm': 1, 'кг': 1,
           'g': .001, 'gram': .001, 'grams': .001, 'grm': .001, 'г': .001,
           'lb': .45359237, 'lbs': .45359237, 'pound': .45359237, 'pounds': .45359237, 'lbr': .45359237,
           'oz': .028349523125, 'ounce': .028349523125, 'ounces': .028349523125, 'onz': .028349523125}


def kilograms(value, unit=None):
    if isinstance(value, dict):
        return kilograms(value.get('value'), value.get('unitCode') or value.get('unitText'))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = html.unescape(value).strip()
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([A-Za-zА-Яа-я]+)?', text)
        if not match:
            return None
        value, stated_unit = match.groups()
        unit = stated_unit or unit
    factor = FACTORS.get(str(unit or '').strip().rsplit('/', 1)[-1].lower())
    try:
        amount = float(value)
    except (ValueError, TypeError):
        return None
    return amount * factor if factor and math.isfinite(amount) and amount > 0 else None


def from_product(product):
    """Compatibility: this legacy scalar is NET only."""
    return from_product_fields(product).get('weight_net_kg')


class Specs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.cells = [], []
        self.script = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.script = []
        if tag in ('td', 'th', 'dt', 'dd'):
            self.cell = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            try:
                self.scripts.append(json.loads(''.join(self.script)))
            except ValueError:
                pass
            self.script = None
        if tag in ('td', 'th', 'dt', 'dd') and self.cell is not None:
            self.cells.append(' '.join(''.join(self.cell).split()))
            self.cell = None


def products(obj):
    if isinstance(obj, list):
        for value in obj:
            yield from products(value)
    elif isinstance(obj, dict):
        types = obj.get('@type', [])
        if types == 'Product' or isinstance(types, list) and 'Product' in types:
            yield obj
        for key in ('@graph', 'item', 'itemListElement', 'mainEntity'):
            if key in obj:
                yield from products(obj[key])


def from_markup(markup, base):
    """Compatibility scalar; new callers must use extract() for both kinds."""
    return extract(markup, base).get('weight_net_kg')


def display(value):
    kg = kilograms(value, 'kg')
    return (f'{kg:.4f}'.rstrip('0').rstrip('.') + ' кг') if kg is not None else 'не указан'


PKG_LABELS = {'shipping weight', 'package weight', 'weight (with packaging)',
              'weight with packaging', 'gross weight', 'packaged weight'}
NET_LABELS = {'item weight', 'product weight', 'net weight', 'weight (without packaging)'}
WEIGHT_FIELDS = ('weight_pkg_kg','weight_net_kg','weight_pkg_provenance','weight_net_provenance',
                 'weight_sum_kg','weight_sum_provenance','weight_estimate_kg','weight_missing_reason','weight_components','weight_estimate_range_kg','weight_estimate_basis')


def field_kind(label):
    label=' '.join(str(label).replace('\xa0',' ').lower().strip(' :').split())
    if label in PKG_LABELS:return 'pkg',None
    if label in NET_LABELS:return 'net',None
    match=re.fullmatch(r'(.+?)\s*\((kg|g|lb|lbs|oz|pounds|grams)\)',label)
    if match:
        kind,_=field_kind(match[1]);return kind,match[2]
    return None,None


def _fields(observations, source, at=None):
    result={};at=time.time() if at is None else at
    for kind in ('pkg','net'):
        candidates=[(kg,field) for k,kg,field in observations if k==kind and kg is not None]
        unique={round(kg,9) for kg,_ in candidates}
        if len(unique)==1:
            kg,field=candidates[0]
            result['weight_'+kind+'_kg']=kg
            result['weight_'+kind+'_provenance']={'source':str(source)[:512],'field':str(field)[:128],'ts':at}
        elif len(unique)>1:result['weight_missing_reason']='conflicting_'+kind+'_weights'
    if not result:result['weight_missing_reason']='no_explicit_weight_with_units'
    return result


def from_product_fields(product, source='', at=None):
    observations=[]
    if not isinstance(product,dict):return {}
    if product.get('weight') is not None:observations.append(('net',kilograms(product['weight']),'Product.weight'))
    properties=product.get('additionalProperty',[])
    if isinstance(properties,dict):properties=[properties]
    for prop in properties if isinstance(properties,list) else []:
        if not isinstance(prop,dict):continue
        kind,unit=field_kind(prop.get('name',''))
        if kind:observations.append((kind,kilograms(prop.get('value'),prop.get('unitCode') or prop.get('unitText') or unit),prop.get('name')))
    return _fields(observations,source or product.get('url',''),at)


def extract(markup, base, at=None):
    if re.search(r'(?:validateCaptcha|captcha/|api-services-support@amazon|cf-chl-|Just a moment)',markup or '',re.I):
        return {'weight_missing_reason':'challenge_page'}
    if re.search(r'id=["\']variation_(?:color|size|style|capacity)',markup or '',re.I):
        return {'weight_missing_reason':'ambiguous_variants'}
    parser=Specs();parser.feed(markup or '')
    items=[p for script in parser.scripts for p in products(script)]
    if any('ProductGroup' in json.dumps(script) or '"hasVariant"' in json.dumps(script) for script in parser.scripts):
        return {'weight_missing_reason':'ambiguous_variants'}
    def identity(url):
        parts=urlsplit(urljoin(base,url));return parts.hostname,parts.path.rstrip('/'),parts.query
    exact=[p for p in items if isinstance(p.get('url'),str) and identity(p['url'])==identity(base)]
    # Variant groups have no safe page-global specification scope, even with one exact child URL.
    if len(items)>1 or any(p.get('hasVariant') or p.get('isVariantOf') for p in items):
        return {'weight_missing_reason':'ambiguous_variants'}
    if items and not (exact or len(items)==1 and not items[0].get('url')):
        return {'weight_missing_reason':'product_identity_mismatch'}
    observations=[]
    for product in exact or items:
        fields=from_product_fields(product,base,at)
        for kind in ('pkg','net'):
            if fields.get('weight_'+kind+'_kg'):
                observations.append((kind,fields['weight_'+kind+'_kg'],fields['weight_'+kind+'_provenance']['field']))
        if fields.get('weight_missing_reason','').startswith('conflicting_'):
            return fields
    for label,value in zip(parser.cells,parser.cells[1:]):
        kind,unit=field_kind(label)
        if label.lower().strip(' :')=='package dimensions':
            # Only the semicolon-delimited mass, never any dimension or unitless number.
            tail=value.rsplit(';',1)
            if len(tail)==2:observations.append(('pkg',kilograms(tail[1].strip()),'Package Dimensions'))
        elif kind:observations.append((kind,kilograms(value,unit),label))
    # Amazon detail-bullets use li/span instead of table cells.
    plain=html.unescape(re.sub(r'<[^>]+>',' ',markup or ''))
    plain=' '.join(plain.replace('\u200e','').replace('\u200f','').split())
    mass=r'(\d+(?:\.\d+)?\s*(?:pounds?|lbs?|ounces?|oz|kg|g))\b(?!\s*(?:\d|[-–]\s*\d))'
    for match in re.finditer(r'\b(Shipping Weight|Package Weight|Item Weight|Product Weight|Net Weight)\s*:\s*'+mass,plain,re.I):
        label,value=match.groups();observations.append((field_kind(label)[0],kilograms(value),label))
    for match in re.finditer(r'\bPackage Dimensions\s*:\s*[^;:]{1,150};\s*'+mass,plain,re.I):
        observations.append(('pkg',kilograms(match[1]),'Package Dimensions'))
    return _fields(observations,base,at)


def apply(target, values):
    for field in WEIGHT_FIELDS:
        if field in values:target[field]=values[field]
    # Compatibility export is always NET, never the package value.
    target['weight_kg']=target.get('weight_net_kg')
    return target


def bundle(deal):
    return deal.get('cat')=='combo' or bool(re.search(r'\b(?:combo|bundle)\b',deal.get('title',''),re.I))


def prepare(deal, estimates=None):
    # A measured bundle package is usable only when explicitly attached to that bundle.
    if bundle(deal) and not deal.get('weight_pkg_kg'):
        parts=deal.get('weight_components',[])
        if not parts:
            for k in WEIGHT_FIELDS:deal.pop(k,None)
            deal['weight_missing_reason']='bundle_components_incomplete'
        else:
            totals={kind:[] for kind in ('pkg','net')}
            for part in parts:
                for kind in totals:
                    kg=kilograms(part.get('weight_'+kind+'_kg'),'kg')
                    qty=part.get('quantity',1)
                    totals[kind].append(kg*qty if kg and type(qty)==int and 0<qty<=100 else None)
            known=next((values for values in totals.values() if all(v is not None for v in values)),None)
            for k in ('weight_net_kg','weight_net_provenance','weight_sum_kg','weight_sum_provenance'):deal.pop(k,None)
            if known:
                deal['weight_sum_kg']=sum(known)
                deal['weight_sum_provenance']={'source':'components','field':'sum; not measured parcel','ts':time.time()}
            else:deal['weight_missing_reason']='bundle_components_incomplete'
    enabled=os.getenv('WEIGHT_ESTIMATES','1')=='1' if estimates is None else estimates
    measured=any(deal.get('weight_'+k+'_kg') for k in ('pkg','net','sum'))
    deal.pop('weight_estimate_kg',None)
    if not enabled or measured:
        deal.pop('weight_estimate_range_kg',None);deal.pop('weight_estimate_basis',None)
    elif bundle(deal):
        ranges=[]
        for part in deal.get('weight_components',[]):
            prepare(part,estimates=True)
            measured_part=next((part.get('weight_'+k+'_kg') for k in ('pkg','net','sum') if part.get('weight_'+k+'_kg')),None)
            bounds=[measured_part,measured_part] if measured_part else part.get('weight_estimate_range_kg')
            qty=part.get('quantity',1)
            if not bounds or type(qty)!=int or not 0<qty<=100:ranges=[];break
            ranges.append([bounds[0]*qty,bounds[1]*qty])
        if ranges:
            deal['weight_estimate_range_kg']=[sum(r[0] for r in ranges),sum(r[1] for r in ranges)]
            deal['weight_estimate_basis']={'version':'estimate_v1','kind':'component_sum','samples':0}
        else:
            deal.pop('weight_estimate_range_kg',None);deal.pop('weight_estimate_basis',None)
    elif not deal.get('weight_estimate_range_kg'):
        import weight_estimates
        deal.update(weight_estimates.estimate(deal.get('cat')))
    return deal


def status(deal):
    for kind in ('pkg','net'):
        if kilograms(deal.get('weight_'+kind+'_kg'),'kg'):
            return {'found':True,'source':(deal.get('weight_'+kind+'_provenance') or {}).get('source','unknown'),
                    'kind':kind,'missing_reason':''}
    if deal.get('weight_sum_kg'):
        return {'found':True,'source':'components','kind':'net','missing_reason':'component_sum_not_measured_package'}
    return {'found':False,'source':'','kind':'unknown','missing_reason':deal.get('weight_missing_reason','not_looked_up')}


def display_deal(deal):
    prepare(deal)
    for kind,label in (('pkg','с упаковкой'),('net','без упаковки')):
        if kilograms(deal.get('weight_'+kind+'_kg'),'kg'):return display(deal['weight_'+kind+'_kg'])+' ('+label+')'
    if kilograms(deal.get('weight_sum_kg'),'kg'):return '≈ сумма: '+display(deal['weight_sum_kg'])+' (комплектующие)'
    bounds=deal.get('weight_estimate_range_kg')
    if bounds:
        value='-'.join(f'{x:.4f}'.rstrip('0').rstrip('.') for x in bounds)+' кг'
        return ('≈ сумма: '+value+' (оценка по комплектующим)') if (deal.get('weight_estimate_basis') or {}).get('kind')=='component_sum' else '≈ '+value+' (оценка по категории)'
    return 'не указан'


def validate(deal):
    for field in ('weight_pkg_kg','weight_net_kg','weight_sum_kg','weight_estimate_kg'):
        if field in deal and deal[field] is not None and (isinstance(deal[field],bool) or not isinstance(deal[field],(int,float)) or kilograms(deal[field],'kg') is None):raise ValueError('invalid '+field)
        provenance=deal.get(field.replace('_kg','_provenance'))
        if deal.get(field) and field!='weight_estimate_kg':
            if not isinstance(provenance,dict) or not all(isinstance(provenance.get(k),str) and provenance[k] for k in ('source','field')):
                raise ValueError('missing weight provenance')
            ts=provenance.get('ts')
            if isinstance(ts,bool) or not isinstance(ts,(int,float)) or not math.isfinite(ts):raise ValueError('invalid weight timestamp')
    bounds=deal.get('weight_estimate_range_kg')
    if bounds is not None:
        if not isinstance(bounds,list) or len(bounds)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in bounds) or bounds[0]>bounds[1]:raise ValueError('invalid estimate range')
        basis=deal.get('weight_estimate_basis')
        if not isinstance(basis,dict) or basis.get('version')!='estimate_v1' or basis.get('kind') not in ('category','component_sum') or type(basis.get('samples'))!=int:raise ValueError('invalid estimate basis')
    components=deal.get('weight_components')
    if components is not None:
        if not isinstance(components,list) or len(components)>30:raise ValueError('invalid weight components')
        for part in components:
            if not isinstance(part,dict) or 'weight_components' in part:raise ValueError('invalid weight component')
            qty=part.get('quantity',1)
            if type(qty)!=int or not 0<qty<=100:raise ValueError('invalid component quantity')
            validate(part)
    return deal
