"""Persistent document storage, with automatic migration from the old NeDB file."""
import copy
import json
import os
from pathlib import Path
from threading import RLock
from tinydb import TinyDB
from tinydb.storages import Storage

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND.parent / 'data'


def initial_state():
    def read(name):
        return json.loads((DATA / f'{name}.json').read_text(encoding='utf-8'))
    return dict(organization=read('organization'), customers=read('customers'),
                invoices=read('invoices'), conversations=[], transactions=[], payments=[], activity=[])


class AtomicJSONStorage(Storage):
    """Replace the complete file atomically so interrupted writes leave old data intact."""
    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if self.path.exists():
            return json.loads(self.path.read_text(encoding='utf-8'))

    def write(self, data):
        temporary = self.path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.path)


def legacy_state(path):
    """Replay NeDB's append-only snapshots. Never modify the source file."""
    state = None
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            document = json.loads(line)
            if document.get('_id') == 'demo':
                state = None if document.get('$$deleted') else document
    return state


class Store:
    def __init__(self, directory=None, mongo_uri=None):
        self.lock = RLock()
        self.client = None
        if mongo_uri:
            from pymongo import MongoClient
            self.client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            self.collection = self.client[os.getenv('MONGODB_DATABASE', 'acme_collections')]['workspace']
            self.database_name = 'MongoDB'
            if self.collection.find_one({'_id': 'demo'}) is None:
                self._put(initial_state())
        else:
            directory = Path(directory or BACKEND / 'storage')
            directory.mkdir(parents=True, exist_ok=True)
            self.db = TinyDB(directory / 'workspace.json', storage=AtomicJSONStorage)
            self.database_name = 'Embedded TinyDB'
            if not self.db.all():
                self.db.insert(legacy_state(directory / 'workspace.db') or initial_state())

    def get(self):
        with self.lock:
            state = self.collection.find_one({'_id': 'demo'}) if self.client is not None else self.db.all()[0]
            return copy.deepcopy(dict(state))

    def _put(self, state):
        if self.client is not None:
            self.collection.replace_one({'_id': 'demo'}, {**state, '_id': 'demo'}, upsert=True)
        else:
            document = self.db.all()[0]
            self.db.update(lambda row: (row.clear(), row.update(state)), doc_ids=[document.doc_id])

    def mutate(self, operation):
        # Mutate a copy; validation failures never persist partial changes.
        with self.lock:
            state = self.get()
            result = operation(state)
            self._put(state)
            return result

    def close(self):
        if self.client is not None:
            self.client.close()
        else:
            self.db.close()
