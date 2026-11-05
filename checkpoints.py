"""Atomic state + history generations, with strict legacy migration and recovery."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import time

FILE = 'checkpoint.json'
BACKUP = 'checkpoint.backup.json'


class CheckpointError(ValueError):
    pass


def validate_state(state):
    if not isinstance(state, dict):
        raise CheckpointError('state must be a JSON object; refusing to reset delivery history')
    for key in ('posted', 'processed', 'pending', 'sources', 'assessment_cache', 'price_events'):
        if key in state and not isinstance(state[key], dict):
            raise CheckpointError('invalid state field: ' + key)
    for name in ('posted', 'processed'):
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in state.get(name, {}).values()):
            raise CheckpointError('invalid delivery timestamp: ' + name)
    if 'review' in state and not isinstance(state['review'], list):
        raise CheckpointError('review must be a list')
    return state


def validate_history(history):
    if not isinstance(history, dict) or any(not isinstance(v, list) for v in history.values()):
        raise CheckpointError('history must contain lists of price observations')
    for values in history.values():
        for sample in values:
            if not isinstance(sample, list) or len(sample) < 3 or not isinstance(sample[0], (int, float)) or not isinstance(sample[1], (int, float)):
                raise CheckpointError('invalid price observation')
            if len(sample) == 3:
                sample.append('legacy')
            if any(isinstance(v, bool) or not math.isfinite(v) for v in sample[:2]):
                raise CheckpointError('invalid numeric price observation')
    return history


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or not isinstance(bundle.get('generation_id'), int) or bundle['generation_id'] < 1:
        raise CheckpointError('invalid checkpoint generation')
    validate_state(bundle.get('state'))
    validate_history(bundle.get('history'))
    return bundle


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as ex:
        raise CheckpointError(f'cannot read {Path(path).name}: {type(ex).__name__}') from ex


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.generation = 0
        self.previous = None

    def load(self):
        primary = self.root / FILE
        if primary.exists() or (self.root / BACKUP).exists():
            errors = []
            for name in (FILE, BACKUP):
                try:
                    bundle = validate_bundle(read_json(self.root / name))
                    self.generation = bundle['generation_id']
                    self.previous = copy.deepcopy(bundle)
                    if name == BACKUP:
                        print('[WARN] recovered atomic checkpoint backup; primary checkpoint is invalid')
                    return bundle['state'], bundle['history']
                except CheckpointError as ex:
                    errors.append(str(ex))
            raise CheckpointError('; '.join(errors))
        state_path, history_path = self.root / 'state.json', self.root / 'history.json'
        if state_path.exists():
            state = validate_state(read_json(state_path))
        else:
            legacy = self.root / 'seen.json'
            old = read_json(legacy) if legacy.exists() else {}
            if not isinstance(old, dict):
                raise CheckpointError('invalid legacy delivery state')
            state = {'version': 3, 'processed': {k: v for k, v in old.items() if not k.startswith('k:')},
                     'posted': {}, 'pending': {}, 'sources': {}, 'review': []}
        history = validate_history(read_json(history_path)) if history_path.exists() else {}
        return state, history

    def save(self, state, history):
        validate_state(state)
        validate_history(history)
        bundle = {'generation_id': self.generation + 1, 'saved_at': time.time(),
                  'state': state, 'history': history}
        # The previous known-good generation is backed up before publishing the next one.
        if self.previous:
            atomic_json(self.root / BACKUP, self.previous)
        atomic_json(self.root / FILE, bundle)
        self.generation += 1
        self.previous = copy.deepcopy(bundle)
        # Compatibility exports are never the authority when checkpoint.json exists.
        atomic_json(self.root / 'state.json', state)
        atomic_json(self.root / 'history.json', history)
