import json
import os
from pathlib import Path


class State:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'state.json'
        self.values = json.loads(self.path.read_text()) if self.path.exists() else {}
        if not isinstance(self.values, dict):
            raise ValueError('Estado inválido; revisar data/state.json')

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value
        temp = self.path.with_suffix('.tmp')
        with temp.open('w') as file:
            json.dump(self.values, file)
            file.flush()
            os.fsync(file.fileno())
        temp.replace(self.path)
