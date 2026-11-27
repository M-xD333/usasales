"""Optional, bounded, single-attempt weight-only edits. No publication-floor mutations."""
import copy
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import zlib
import requests
import product_weight as W

FILE='weight_edits.json.gz'
MAX_BYTES=400_000
MAX_RAW=8_000_000
WINDOW=86400
RANK={'pending':0,'editing':1,'edit_unknown':2,'edited':3}


def decode(data):
    if len(data)>MAX_BYTES:raise ValueError('edit compressed limit')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:raw=f.read(MAX_RAW+1)
        if len(raw)>MAX_RAW:raise ValueError('edit raw limit')
        doc=json.loads(raw)
    except (OSError,EOFError,zlib.error) as ex:raise ValueError('edit corrupt') from ex
    if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('rows'),dict) or type(doc.get('canary_done')) is not bool:raise ValueError('edit schema')
    if len(doc['rows'])>2000:raise ValueError('edit row cap')
    for key,r in doc['rows'].items():
        if not isinstance(r,dict) or r.get('state') not in RANK or r.get('kind') not in ('text','photo') or type(r.get('message_id')) is not int:raise ValueError('edit row')
        if any(type(r.get(k)) not in (int,float) or not math.isfinite(r[k]) for k in ('sent_at','updated_at')):raise ValueError('edit timestamp')
        if not isinstance(r.get('payload'),str) or len(r['payload'])>12000 or hashlib.sha256(r['payload'].encode()).hexdigest()!=r.get('hash'):raise ValueError('edit payload integrity')
        if not isinstance(r.get('chat_id'),str) or len(r['chat_id'])>128 or not isinstance(r.get('product'),dict):raise ValueError('edit identity')
    return doc


def compact(doc,now=None):
    now=time.time() if now is None else now
    rows=sorted(((k,r) for k,r in doc['rows'].items() if now-WINDOW<=r['sent_at']<=now+1),key=lambda kv:(kv[1]['sent_at'],kv[0]))[-2000:]
    def pack(start):
        raw=json.dumps(dict(version=1,canary_done=doc['canary_done'],rows=dict(rows[start:])),ensure_ascii=False,separators=(',',':')).encode()
        return gzip.compress(raw,mtime=0),len(raw)
    low,high=0,len(rows)
    while low<high:
        mid=(low+high)//2;data,size=pack(mid)
        if len(data)<=MAX_BYTES and size<=MAX_RAW:high=mid
        else:low=mid+1
    return pack(low)[0]


def merge_bytes(a,b,now=None):
    x,y=decode(a),decode(b);rows=x['rows']
    for key,row in y['rows'].items():
        old=rows.get(key)
        if old is None or (RANK[row['state']],row['updated_at'])>(RANK[old['state']],old['updated_at']):rows[key]=row
    return compact({'version':1,'rows':rows,'canary_done':x['canary_done'] or y['canary_done']},now)


def mode():
    path=Path(__file__).parent/'config'/'late_weight_edit.json'
    try:policy=json.loads(path.read_text())
    except (OSError,ValueError):policy={}
    selected=os.getenv('LATE_WEIGHT_EDITS',policy.get('mode','off'))
    if selected=='on' and not policy.get('public_preview_verified'):return 'canary'
    return selected if selected in ('off','canary','on') else 'off'


def weight_line(payload):
    lines=[s for s in payload.splitlines() if s.startswith('⚖️ Вес:')]
    return lines[0] if len(lines)==1 else None


def replacement(record,fields):
    old=weight_line(record['payload'])
    if not old or '(с упаковкой)' in old:return None
    if not fields.get('weight_pkg_kg') and not (fields.get('weight_net_kg') and '(без упаковки)' not in old):return None
    product=copy.deepcopy(record['product']);W.apply(product,fields);W.prepare(product,estimates=False)
    status=W.status(product)
    if not status['found'] or status['kind'] not in ('pkg','net'):return None
    import html
    new='⚖️ Вес: '+html.escape(W.display_deal(product))
    changed=record['payload'].replace(old,new,1)
    # The entire rest of the payload is byte-for-byte identical: prices/links cannot change.
    if changed.replace(new,old,1)!=record['payload'] or changed==record['payload']:return None
    plain=html.unescape(re.sub('<[^>]*>','',changed));limit=1024 if record['kind']=='photo' else 4096
    return changed if len(plain.encode('utf-16-le'))<=limit*2 else None


def public_unchanged(record,session=requests):
    """Fail closed if the public copy is unavailable or was manually changed."""
    import html
    channel='usasalessearch';post=str(record['message_id'])
    try:
        response=session.get('https://t.me/s/'+channel+'?before='+str(record['message_id']+1),
                             headers={'User-Agent':'usasales-public-edit-check/1.0'},timeout=(2,4))
        if response.status_code!=200 or len(response.content)>1_000_000:return False
        segment=next((s for s in response.text.split('data-post="') if s.startswith(channel+'/'+post+'"')),None)
        if segment is None:return False
        match=re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>',segment,re.S)
        if not match:return False
        current=match[1]
        def plain(value):return ' '.join(html.unescape(re.sub('<[^>]*>',' ',value.replace('<br>',' '))).split())
        if plain(current)!=plain(record['payload']):return False
        expected={html.unescape(u) for u in re.findall(r'<a[^>]+href="([^"]+)"',record['payload'])}
        actual={html.unescape(u) for u in re.findall(r'<a[^>]+href="([^"]+)"',current)}
        if not expected.issubset(actual):return False
        return all(u in expected or u in plain(record['payload']) for u in actual)
    except (requests.RequestException,ValueError):return False


class Registry:
    def __init__(self,path,now=None,log=print):
        self.path=Path(path);self.now=time.time() if now is None else now;self.log=log;self.disabled=False
        self.doc={'version':1,'canary_done':False,'rows':{}}
        try:
            if self.path.exists():self.doc=decode(self.path.read_bytes())
        except (OSError,ValueError):self.disabled=True;log('[WEIGHT EDIT] invalid optional registry; disabled')
    def save(self):
        if self.disabled:return False
        tmp=None
        try:
            data=compact(self.doc,self.now);kept=decode(data)['rows']
            for key in list(self.doc['rows']):
                if key not in kept:self.doc['rows'].pop(key)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,delete=False) as f:
                tmp=f.name;f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path);return True
        except (OSError,ValueError):self.disabled=True;self.log('[WEIGHT EDIT] optional save failed');return False
        finally:
            if tmp and os.path.exists(tmp):os.unlink(tmp)
    def observe(self,deal,text,receipt):
        if self.disabled:return
        result=receipt.get('result') if isinstance(receipt,dict) else None
        if not isinstance(result,dict) or type(result.get('message_id')) is not int:return
        chat=str((result.get('chat') or {}).get('id') or os.getenv('TG_CHAT_ID',''))
        if not chat:return
        kind='photo' if result.get('photo') else 'text'
        if kind=='photo':
            import bot
            text=bot.photo_caption(text,deal.get('url'))['caption']
        if not weight_line(text) or '(с упаковкой)' in weight_line(text):return
        product={k:copy.deepcopy(deal[k]) for k in ('url','title','name','cat','cond') if k in deal}
        meta=deal.get('product_meta') or {}
        product['product_meta']={k:copy.deepcopy(meta[k]) for k in ('brand','mpn','gtins','model','capacity','color','size','quantity') if k in meta}
        key=hashlib.sha256((chat+':'+str(result['message_id'])).encode()).hexdigest()
        if key in self.doc['rows']:return
        self.doc['rows'][key]={'message_id':result['message_id'],'chat_id':chat,'kind':kind,'payload':text,
                'hash':hashlib.sha256(text.encode()).hexdigest(),'product':product,'sent_at':self.now,'updated_at':self.now,'state':'pending'}
        self.save()
    def pending(self):
        return [r for r in self.doc['rows'].values() if r['state']=='pending' and self.now-WINDOW<=r['sent_at']<=self.now]
    def apply_ready(self,lookup,token,session=requests,selected=None,dry=False,preflight=None):
        selected=mode() if selected is None else selected
        if self.disabled or selected=='off' or (selected=='canary' and self.doc['canary_done']):return []
        changes=[]
        for r in self.pending()[:10]:
            new=replacement(r,lookup.get(r['product']))
            if new is None:continue
            if dry:changes.append({'message_id':r['message_id'],'payload':new});continue
            if not token:return changes
            check=preflight or public_unchanged
            if not check(r):continue
            r.update(state='editing',updated_at=self.now)
            if selected=='canary':self.doc['canary_done']=True # At most ONE attempt, including ambiguous outcomes.
            if not self.save():return changes
            method='editMessageCaption' if r['kind']=='photo' else 'editMessageText'
            field='caption' if r['kind']=='photo' else 'text'
            payload={'chat_id':r['chat_id'],'message_id':r['message_id'],field:new,'parse_mode':'HTML'}
            if field=='text':payload['link_preview_options']={'is_disabled':False}
            try:
                response=session.post('https://api.telegram.org/bot'+token+'/'+method,json=payload,timeout=(3,8))
                ok=response.status_code==200 and response.json().get('ok') is True
            except (requests.RequestException,ValueError):ok=False
            r.update(state='edited' if ok else 'edit_unknown',updated_at=self.now)
            if ok:r['edited_weight_line']=weight_line(new);self.log('[WEIGHT EDIT] confirmed message_id='+str(r['message_id']))
            else:self.log('[WEIGHT EDIT] unconfirmed; no retry')
            self.save();changes.append({'message_id':r['message_id'],'ok':ok})
            if selected=='canary':break
            break # One request per pass; no burst and no retry.
        return changes
