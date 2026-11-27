"""One request per sampled URL, <=25 URLs, normal public pages; no retries or alternate routes."""
import argparse
from collections import Counter
import json
import multiprocessing as MP
from pathlib import Path
import re
import signal
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
import product_weight as W


def fetch(writer,row):
    def expired(*_):raise TimeoutError('probe deadline')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,8)
    start=time.monotonic();response=None;result={**row}
    try:
        response=requests.get(row['url'],headers={'User-Agent':'usasales-weight-probe/1.0 (+https://github.com/M-xD333/usasales)'},
                              timeout=(3,5),allow_redirects=False,stream=True)
        result['http']=response.status_code;result['ttfb_s']=round(time.monotonic()-start,3)
        if response.status_code==200:
            chunks=[];size=0
            for chunk in response.iter_content(16384):
                chunks.append(chunk);size+=len(chunk)
                if size>1_000_000:break
            page=b''.join(chunks).decode(response.encoding or 'utf-8',errors='replace')
            result.update(W.extract(page,row['url']));parser=W.Specs();parser.feed(page)
            result['weight_labels']=[s[:80] for s in parser.cells if re.search(r'weight|package dimensions',s,re.I)][:10]
            result['fragments']=[m[0][:900] for m in list(re.finditer(r'<tr\b[^>]*>(?:(?!</tr>)[\s\S]){0,2000}?</tr>',page,re.I)) if re.search(r'weight|package dimensions',m[0],re.I)][:2]
            result['bytes']=size
    except Exception as ex:result['error']=type(ex).__name__
    finally:
        if response is not None:response.close()
        result['elapsed_s']=round(time.monotonic()-start,3)
        writer.send(result);writer.close()


def run(rows):
    context=MP.get_context('fork');out=[]
    for row in rows[:25]:
        reader,writer=context.Pipe(duplex=False);process=context.Process(target=fetch,args=(writer,row));process.start();writer.close()
        if reader.poll(9):
            try:out.append(reader.recv())
            except EOFError:out.append(dict(row,error='worker_failed'))
        else:out.append(dict(row,error='deadline'))
        if process.is_alive():process.terminate()
        process.join(timeout=.1);reader.close()
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='/tmp/weight-runner-probe.json');a=p.parse_args()
    sample=json.loads(Path('docs/evidence/weight-sample-2026-11-27.json').read_text())['rows']
    rows=[];seen=set()
    for r in sample:
        if r['route']=='bestbuy.com' or r['url'] in seen:continue # Known 403: do not seek another route.
        seen.add(r['url']);rows.append({k:r.get(k) for k in ('url','title','route','category','source_id')})
    out=run(rows);Path(a.output).write_text(json.dumps({'rows':out},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'requests':len(out),'http':dict(Counter(str(r.get('http')) for r in out)),
                      'pkg':sum(bool(r.get('weight_pkg_kg')) for r in out),'net':sum(bool(r.get('weight_net_kg')) for r in out)}))
