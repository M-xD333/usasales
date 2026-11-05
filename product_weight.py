"""Explicit product/net weight only; units are mandatory, shipping weight is excluded."""
import html
from html.parser import HTMLParser
import json
import math
import re
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
    weight = kilograms(product.get('weight'))
    if weight is not None:
        return weight
    properties = product.get('additionalProperty', [])
    if isinstance(properties, dict):
        properties = [properties]
    if not isinstance(properties, list):
        return None
    values = [kilograms(p.get('value'), p.get('unitCode') or p.get('unitText'))
              for p in properties if isinstance(p, dict) and
              str(p.get('name', '')).strip().lower() in ('weight', 'item weight', 'net weight', 'product weight')]
    known = {v for v in values if v is not None}
    return known.pop() if len(known) == 1 else None


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
    parser = Specs()
    parser.feed(markup or '')
    items = [p for script in parser.scripts for p in products(script)]
    def identity(url):
        parts = urlsplit(urljoin(base, url))
        return parts.hostname, parts.path.rstrip('/'), parts.query
    exact = [p for p in items if isinstance(p.get('url'), str) and identity(p['url']) == identity(base)]
    selected = exact or ([items[0]] if len(items) == 1 and not items[0].get('url') else [])
    known = {v for p in selected if (v := from_product(p)) is not None}
    if known:
        return known.pop() if len(known) == 1 else None
    # Multiple product/variant records must never borrow a recommendation's specs.
    if len(items) > 1 and not exact:
        return None
    values = set()
    for label, value in zip(parser.cells, parser.cells[1:]):
        match = re.fullmatch(r'(?:item |net |product )?weight\s*:?(?:\s*\(([^)]+)\))?', label, re.I)
        if match and (kg := kilograms(value, match.group(1))) is not None:
            values.add(kg)
    return values.pop() if len(values) == 1 else None


def display(value):
    kg = kilograms(value, 'kg')
    return (f'{kg:.4f}'.rstrip('0').rstrip('.') + ' кг') if kg is not None else 'не указан'
