"""Nonblocking metadata prefetch. Network work has a hard child-process deadline."""
from collections import deque
import multiprocessing as MP
import signal
import pickle
import time
import product_weight as W
import product_images
import store_links

BUDGET=2.0
MAX_LOOKUPS=30


def _worker(writer,url,resolver,budget):
    def expired(*_):raise TimeoutError('weight deadline')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,budget)
    try:
        value=resolver(url) or {'weight_missing_reason':'page_unavailable'}
        W.validate(value)
        # Keep the whole Pipe frame below PIPE_BUF: polling must not wait for a partial large message.
        if len(pickle.dumps(value))>3000:
            value.pop('image_url',None)
            if '_sample' in value: value['_sample']['fragments']=value['_sample'].get('fragments',[])[:1]
        if len(pickle.dumps(value))>3000:value={'weight_missing_reason':'metadata_payload_too_large'}
        writer.send(value)
    except BaseException as ex:
        try:writer.send({'weight_missing_reason':'lookup_timeout' if isinstance(ex,TimeoutError) else 'lookup_failed'})
        except (OSError,BrokenPipeError):pass
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);writer.close()


class Lookup:
    def __init__(self,knowledge=None,resolver=None,budget=BUDGET,limit=MAX_LOOKUPS):
        method='fork' if 'fork' in MP.get_all_start_methods() else 'spawn'
        self.context=MP.get_context(method);self.knowledge=knowledge;self.resolver=resolver or product_images.from_product_details
        self.budget=budget;self.limit=limit;self.waiting=deque();self.active={};self.results={};self.deals={};self.requested=set();self.closed=False
    def request(self,deal):
        url=store_links.merchant_url(deal.get('url',''))
        if not url:
            candidates={store_links.merchant_url(store_links.unwrap(x)) for x in deal.get('store_candidates',[])}-{None,''}
            url=next(iter(candidates)) if len(candidates)==1 else None
        if not url or url in self.requested or len(self.requested)>=self.limit:return
        self.requested.add(url);self.deals[url]=dict(deal,url=url);self.waiting.append(url)
    def poll(self):
        for url,(process,reader,started) in list(self.active.items()):
            result=None
            if reader.poll():
                try:result=reader.recv()
                except (EOFError,OSError):result={'weight_missing_reason':'lookup_failed'}
            elif not process.is_alive() or time.monotonic()-started>self.budget+.2:
                result={'weight_missing_reason':'lookup_timeout'}
            if result is not None:
                self.results[url]=result
                if self.knowledge:
                    product=dict(self.deals[url]);W.apply(product,result);self.knowledge.remember(product)
                if process.is_alive():process.terminate()
                process.join(timeout=0);reader.close();self.active.pop(url)
        while self.waiting and len(self.active)<2 and not self.closed:
            url=self.waiting.popleft();reader,writer=self.context.Pipe(duplex=False)
            process=self.context.Process(target=_worker,args=(writer,url,self.resolver,self.budget),daemon=True)
            try:
                process.start();writer.close();self.active[url]=(process,reader,time.monotonic())
            except Exception:
                writer.close();reader.close();self.results[url]={'weight_missing_reason':'lookup_unavailable'}
    def get(self,deal):
        self.poll();values=self.knowledge.lookup(deal) if self.knowledge else {}
        ready=self.results.get(store_links.merchant_url(deal.get('url','')))
        if ready:
            values.update(ready)
            if self.knowledge:
                product=dict(deal);W.apply(product,values);self.knowledge.remember(product)
        if not values:values={'weight_missing_reason':'lookup_pending_or_budget_exhausted'}
        return values
    def close(self):
        self.closed=True;self.poll()
        for process,reader,_ in self.active.values():
            if process.is_alive():process.terminate()
            process.join(timeout=0);reader.close()
        self.active.clear()
        if self.knowledge:self.knowledge.save()
