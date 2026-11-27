"""Bounded optional weight evidence, isolated from publication/delivery history."""
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import zlib
import product_weight as W
import market_prices as M

FILE='weight_knowledge.json.gz'
MAX_BYTES=500_000
MAX_RAW=8_000_000
TTL=180*86400


def identity(deal):
    meta=deal.get('product_meta') or {};meta=meta if isinstance(meta,dict) else {};title=str(deal.get('title',''))
    capacities=sorted(set(re.findall(r'\b\d+(?:\.\d+)?\s*(?:GB|TB|MB)\b',title,re.I)))
    colors=sorted(set(re.findall(r'\b(?:black|white|silver|blue|red|pink|gr[ae]y|green|purple|yellow|orange|teal|navy|sand|graphite)\b',title,re.I)))
    guard={k:meta.get(k) for k in ('capacity','color','size','bundle','quantity')}
    guard.update(title=M.normalize(title),capacities=[M.normalize(x) for x in capacities],colors=[M.normalize(x) for x in colors],
                 condition=deal.get('cond') or meta.get('condition') or 'New',bundle=W.bundle(deal))
    sig=hashlib.sha256(json.dumps(guard,sort_keys=True).encode()).hexdigest()[:24]
    aliases=[]
    asin=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)',deal.get('url',''),re.I)
    if asin:aliases.append('asin:'+asin[1].upper())
    for gtin in (meta.get('gtins') if isinstance(meta.get('gtins'),list) else []):
        if re.fullmatch(r'\d{8,14}',str(gtin)):aliases.append('upc:'+str(gtin))
    brand=M.normalize(meta.get('brand',''))
    if brand and meta.get('mpn'):aliases.append('mpn:'+brand+':'+M.normalize(str(meta['mpn'])))
    if brand and title and re.search(r'\d',title):aliases.append('model:'+brand+':'+M.normalize(str(meta.get('model') or title))+':'+M.normalize(title))
    return [hashlib.sha256((a+'|'+sig).encode()).hexdigest() for a in aliases]


def decode(data):
    if len(data)>MAX_BYTES:raise ValueError('knowledge compressed limit')
    try:
        with gzip.GzipFile(fileobj=__import__('io').BytesIO(data)) as f:raw=f.read(MAX_RAW+1)
        if len(raw)>MAX_RAW:raise ValueError('knowledge raw limit')
        doc=json.loads(raw)
    except (OSError,EOFError,zlib.error) as ex:raise ValueError('knowledge corrupt') from ex
    if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('rows'),dict):raise ValueError('knowledge schema')
    if len(doc['rows'])>10000:raise ValueError('knowledge row limit')
    for key,row in doc['rows'].items():
        if not re.fullmatch('[a-f0-9]{64}',key) or not isinstance(row,dict):raise ValueError('knowledge row')
        W.validate(row)
        if not W.kilograms(row.get('at'),'kg'):raise ValueError('knowledge timestamp')
        for kind in ('pkg','net'):
            prov=row.get('weight_'+kind+'_provenance',{})
            if any(len(str(prov.get(k,'')))>512 for k in ('source','field')):raise ValueError('knowledge provenance limit')
    return doc


def compact(doc,now=None):
    now=time.time() if now is None else now
    rows=sorted(((k,v) for k,v in doc['rows'].items() if now-TTL<=v['at']<=now+1),key=lambda kv:(kv[1]['at'],kv[0]))[-10000:]
    def pack(start):
        raw=json.dumps({'version':1,'rows':dict(rows[start:])},separators=(',',':')).encode()
        return gzip.compress(raw,mtime=0),len(raw)
    low,high=0,len(rows)
    while low<high:
        mid=(low+high)//2;b,n=pack(mid)
        if len(b)<=MAX_BYTES and n<=MAX_RAW:high=mid
        else:low=mid+1
    data,_=pack(low)
    return data


def merge_bytes(a,b,now=None):
    rows=decode(a)['rows']
    for key,value in decode(b)['rows'].items():
        old=rows.get(key,{})
        merged=dict(old)
        for kind in ('pkg','net'):
            pk='weight_'+kind+'_provenance';wk='weight_'+kind+'_kg'
            if value.get(wk) and value.get(pk,{}).get('ts',0)>=old.get(pk,{}).get('ts',0):
                merged[wk]=value[wk];merged[pk]=value[pk]
        merged['at']=max(old.get('at',0),value['at']);rows[key]=merged
    return compact({'version':1,'rows':rows},now)


class Knowledge:
    def __init__(self,path,now=None,log=print):
        self.path=Path(path);self.now=time.time() if now is None else now;self.log=log;self.disabled=False
        self.doc={'version':1,'rows':{}}
        try:
            if self.path.exists():self.doc=decode(self.path.read_bytes())
        except (OSError,ValueError) as ex:self.disabled=True;log('[WEIGHT] knowledge unavailable: '+type(ex).__name__)
    def lookup(self,deal):
        matches=[self.doc['rows'][k] for k in identity(deal) if k in self.doc['rows'] and self.now-TTL<=self.doc['rows'][k]['at']<=self.now+1]
        result={}
        for kind in ('pkg','net'):
            values=[r for r in matches if r.get('weight_'+kind+'_kg')]
            if len({round(r['weight_'+kind+'_kg'],9) for r in values})==1:
                row=max(values,key=lambda r:r['weight_'+kind+'_provenance']['ts'])
                for field in ('weight_'+kind+'_kg','weight_'+kind+'_provenance'):result[field]=copy.deepcopy(row[field])
        return result
    def remember(self,deal):
        if self.disabled or W.bundle(deal):return
        fields={k:copy.deepcopy(deal[k]) for k in W.WEIGHT_FIELDS[:4] if k in deal}
        if not any(fields.get('weight_'+k+'_kg') for k in ('pkg','net')):return
        try: W.validate(fields)
        except ValueError:
            self.log('[WEIGHT] invalid evidence ignored');return
        for key in identity(deal):
            row=dict(self.doc['rows'].get(key,{}));row.update(fields,at=self.now);self.doc['rows'][key]=row
    def save(self):
        if self.disabled:return
        temp=None
        try:
            data=compact(self.doc,self.now);self.doc=decode(data);self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,delete=False) as f:
                temp=f.name;f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(temp,self.path)
        except (OSError,ValueError) as ex:self.disabled=True;self.log('[WEIGHT] knowledge save failed: '+type(ex).__name__)
        finally:
            if temp and os.path.exists(temp):os.unlink(temp)
