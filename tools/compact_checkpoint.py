"""Rehearse Phase 0 migration on a separate directory; never overwrite the input."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checkpoints


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    source=args.input.resolve();target=args.output_dir.resolve()
    if target == source.parent or (target / checkpoints.FILE).exists():
        parser.error('use a separate empty output directory')
    original=json.loads(source.read_text())
    target.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target/checkpoints.FILE)
    state,history=checkpoints.Store(target).load()
    protected=('posted','publication_history','processed','last_delivery')
    for key in protected:
        if original['state'].get(key) != state.get(key):
            raise RuntimeError('protected facts changed: '+key)
    report={'before_bytes':source.stat().st_size,'after_bytes':(target/checkpoints.FILE).stat().st_size,
        'assessment_cache':len(state.get('assessment_cache',{})), 'price_events':len(state.get('price_events',{})),
        'protected_facts_equal':True,'backup_archives':len(list((target/'migrations').glob('*.gz')))}
    print(json.dumps(report))
    if report['after_bytes'] > 8_000_000:
        raise SystemExit('target >8 MB; review non-cache state before deployment')

if __name__=='__main__':main()
