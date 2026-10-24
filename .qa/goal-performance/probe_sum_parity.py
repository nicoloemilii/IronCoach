"""Why a floating running sum or fsum swap cannot promise unchanged judgments."""
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['IRON_DATA_DIR'] = str(Path(__file__).parent / 'isolated-data')
os.environ['IRON_AUTO_SYNC'] = '0'
from app.services.activity_rating import _distribution

stamp = datetime(2026, 10, 22, 11)
history = []; loads = {}
for index, days in enumerate((28, 21, 14)):
    row = SimpleNamespace(id=index + 1, start_time=stamp - timedelta(days=days), duration_seconds=3600)
    history.append(row); loads[row.id] = 1.
for index in range(25):
    row = SimpleNamespace(id=index + 4, start_time=stamp - timedelta(hours=25 - index), duration_seconds=3600)
    history.append(row); loads[row.id] = .05
current = SimpleNamespace(id=100, start_time=stamp, duration_seconds=3600)
loads[current.id] = .05
legacy = _distribution(current, loads[current.id], history, loads)
exactly_rounded = math.fsum([.05] * 26)
result = dict(legacy_component=legacy, legacy_sum=sum([.05] * 26), fsum=exactly_rounded,
    fsum_would_select_score=85 if exactly_rounded <= 1.15 else 75 if exactly_rounded <= 1.3 else 55,
    cancellation_example=dict(full_prefix=sum([1e16, 1., 1.]), removed_prefix=1e16,
                              prefix_difference=sum([1e16, 1., 1.]) - 1e16,
                              chronological_window_sum=sum([1., 1.])))
target = Path(__file__).with_suffix('.json')
target.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
