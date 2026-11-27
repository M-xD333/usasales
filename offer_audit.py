"""Bounded best-effort decision evidence, never publication/deduplication state."""
import copy
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
import zlib
import product_weight as W
import market_prices as M
import store_links

FILE = 'offer_audit.json.gz'
MAX_BYTES = 1_500_000
MAX_RAW = 24_000_000
MAX_ROWS = 25_000
TTL = 14 * 86400
LIMITS = {'id':64,'listing':64,'key':80,'source':64,'store':64,'store_basis':12,'candidate_key':80,
          'category':32,'title':160,'decision':32,'reason':240,'was_kind':16,'trust':16}


def number(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def decode(data):
    if len(data)>MAX_BYTES: raise ValueError('audit compressed size limit')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f: raw=f.read(MAX_RAW+1)
    except zlib.error as ex: raise ValueError('invalid compressed audit') from ex
    if len(raw)>MAX_RAW: raise ValueError('audit decompressed size limit')
    doc=json.loads(raw)
    if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('rows'),list):
        raise ValueError('invalid audit schema')
    if len(doc['rows'])>MAX_ROWS: raise ValueError('audit row limit')
    for row in doc['rows']:
        status=row.get('weight_status')
        if status is not None and (not isinstance(status,dict) or type(status.get('found')) is not bool or status.get('kind') not in ('pkg','net','unknown') or any(not isinstance(status.get(k),str) or len(status[k])>512 for k in ('source','missing_reason'))):
            raise ValueError('invalid audit weight status')
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or len(row[k])>n for k,n in LIMITS.items()):
            raise ValueError('invalid audit row')
        if any(not number(row.get(k)) for k in ('ts','last_ts','seq','rules_version')):
            raise ValueError('invalid audit timestamp/version')
        if any(row.get(k) is not None and not number(row[k]) for k in ('price','was','seen_ts','published_ts')):
            raise ValueError('invalid audit price/time')
    if any(not number(doc.get(k)) for k in ('started_at','truncated_before','dropped_capacity')):
        raise ValueError('invalid audit coverage metadata')
    return doc


def _pack(doc):
    raw=json.dumps(doc,ensure_ascii=False,separators=(',',':')).encode()
    return gzip.compress(raw,compresslevel=6,mtime=0),len(raw)


def compact(doc, now, limit=MAX_BYTES):
    doc=copy.deepcopy(doc);cutoff=now-TTL
    rows=[r for r in doc['rows'] if cutoff<=r['last_ts']<=now+1]
    for r in rows: r['ts']=max(r['ts'],cutoff)
    rows.sort(key=lambda r:(r['last_ts'],r['id']))
    removed=rows[:-MAX_ROWS] if len(rows)>MAX_ROWS else []
    rows=rows[-MAX_ROWS:]
    def candidate(skip):
        dropped=removed+rows[:skip]
        return dict(doc,rows=rows[skip:],
                    dropped_capacity=doc.get('dropped_capacity',0)+len(dropped),
                    truncated_before=max([doc.get('truncated_before',0)]+[r['last_ts'] for r in dropped]))
    result=candidate(0);data,raw_size=_pack(result)
    if len(data)>limit or raw_size>MAX_RAW:
        low,high=0,len(rows)
        while low<high:
            middle=(low+high)//2
            packed,unpacked_size=_pack(candidate(middle))
            if len(packed)<=limit and unpacked_size<=MAX_RAW: high=middle
            else: low=middle+1
        result=candidate(low);data,raw_size=_pack(result)
    if len(data)>limit or raw_size>MAX_RAW: raise ValueError('audit metadata exceeds size limit')
    return result,data


def merge_bytes(remote, local, now=None):
    a,b=decode(remote),decode(local);rows={}
    for row in a['rows']+b['rows']:
        old=rows.get(row['id'])
        if old is None or (row['last_ts'],row['seq'])>=(old['last_ts'],old['seq']): rows[row['id']]=row
    doc={'version':1,'started_at':min(a['started_at'],b['started_at']),
         'truncated_before':max(a.get('truncated_before',0),b.get('truncated_before',0)),
         'dropped_capacity':max(a.get('dropped_capacity',0),b.get('dropped_capacity',0)), 'rows':list(rows.values())}
    return compact(doc,time.time() if now is None else now)[1]


class Ledger:
    def __init__(self,path,now,rules_version,log=print):
        self.path,self.now,self.rules,self.log=Path(path),now,rules_version,log
        self.pending={};self.objects={};self.seq=0;self.disabled=False
        self.doc={'version':1,'started_at':now,'truncated_before':0,'dropped_capacity':0,'rows':[]}
        try:
            if self.path.is_symlink(): raise ValueError('audit path is a symlink')
            if self.path.exists():
                if self.path.stat().st_size>MAX_BYTES: raise ValueError('audit size limit')
                self.doc=decode(self.path.read_bytes())
        except (OSError,ValueError,EOFError) as ex:
            self.disabled=True;self.log('[AUDIT] unavailable: '+type(ex).__name__)

    def record(self,d,decision,reason=''):
        if self.disabled:return
        try:
            self.seq+=1
            source=str(d.get('source_id',''));item=str(d.get('id',''))
            listing=hashlib.sha256((source+'\0'+item).encode()).hexdigest()
            # Resolve to a public product key only when the primary URL is actually known.
            url=M.canonical_url(d.get('url'))
            asin=d.get('asin')
            key=('asin:'+asin.upper()) if isinstance(asin,str) and len(asin)==10 else (
                 'url:'+hashlib.sha256(url.encode()).hexdigest() if url else 'listing:'+listing)
            candidates = {M.canonical_url(store_links.unwrap(u)) for u in d.get('store_candidates', []) if isinstance(u,str)}
            candidates = {u for u in candidates if u and M.shop(u) and store_links.merchant_url(u)}
            candidate_key = ('url:'+hashlib.sha256(next(iter(candidates)).encode()).hexdigest()) if len(candidates)==1 else ''
            merchant = url if store_links.merchant_url(url) else (next(iter(candidates)) if len(candidates)==1 else '')
            if id(d) not in self.objects:
                rid=hashlib.sha256((listing+'\0'+str(self.now)+'\0'+str(d.get('price'))).encode()).hexdigest()
                self.objects[id(d)]=(d,rid)
            rid=self.objects[id(d)][1]
            row={'id':rid,'listing':listing,'key':key,'candidate_key':candidate_key,'ts':self.now,'last_ts':self.now,'seq':self.seq,
                 'seen_ts':d.get('fetched_ts'),'published_ts':d.get('published_ts'),
                 'source':source,'store':M.shop(merchant) or d.get('store') or 'unknown',
                 'store_basis':('url' if merchant==url else 'advertised') if merchant else 'declared' if d.get('store') else 'unknown',
                 'category':d.get('cat','unknown'),'price':d.get('price'),'was':d.get('original'),
                 'was_kind':d.get('original_kind') or 'n/a','trust':d.get('trust','unknown'),
                 'title':d.get('title',''),'decision':decision,'reason':reason,'rules_version':self.rules}
            row['weight_status']=W.status(d)
            for k,n in LIMITS.items():row[k]=str(row.get(k,''))[:n]
            for k in ('price','was','seen_ts','published_ts'):
                if not number(row.get(k)):row[k]=None
            self.pending[rid]=row
            if decision in ('sending','published','delivery_unknown'):self.flush()
        except Exception as ex:
            self.disabled=True;self.log('[AUDIT] record failed: '+type(ex).__name__)

    def flush(self,final=False):
        if self.disabled:return
        try:
            rows={r['id']:r for r in self.doc['rows']};rows.update(self.pending)
            if final:
                # Preserve transitions/deliveries; coalesce unchanged per-listing decisions only within one UTC day.
                last={};out=[]
                for row in sorted(rows.values(),key=lambda r:(r['ts'],r['id'])):
                    if row['decision']=='evaluating':row=dict(row,decision='interrupted',reason='assessment did not finish')
                    k=(row['listing'],int(row['ts']//86400))
                    prev=last.get(k)
                    fields=('key','category','price','was','decision','reason','rules_version','store','store_basis','weight_status')
                    if prev and row['decision'] not in ('published','sending','delivery_unknown') and all(prev.get(f)==row.get(f) for f in fields):
                        prev['last_ts']=max(prev['last_ts'],row['last_ts']);prev['seen_ts']=row['seen_ts']
                    else:out.append(row);last[k]=row
                rows={r['id']:r for r in out}
            doc=dict(self.doc,rows=list(rows.values()));doc,data=compact(doc,self.now)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,suffix='.tmp',delete=False) as f:
                tmp=Path(f.name);f.write(data);f.flush();os.fsync(f.fileno())
            try:os.replace(tmp,self.path)
            finally:tmp.unlink(missing_ok=True)
            self.doc=doc
            if final:self.pending.clear();self.objects.clear()
        except Exception as ex:
            self.disabled=True;self.log('[AUDIT] write failed: '+type(ex).__name__)
