import os
import sys
import json
from pathlib import Path
from .functional_health import read_health


def healthy():
    directory = Path(os.getenv('DATA_DIR', 'data'))
    names = [n.strip() for n in os.getenv('SHOP_MODULES', 'demo').split(',') if n.strip()]
    result = read_health(directory, names)
    print(json.dumps(result, ensure_ascii=False))
    return result['healthy']


if __name__ == '__main__':
    sys.exit(0 if healthy() else 1)
