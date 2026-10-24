"""Independent IEEE checks for the nonnegative common-quantum exactness gate."""
import hashlib
import json
import math
import os
from pathlib import Path
import random
import struct
import sys

ROOT = Path(__file__).resolve().parents[2]
QA = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.environ['IRON_DATA_DIR'] = str(QA / 'isolated-exact-sum-data')
os.environ['IRON_AUTO_SYNC'] = '0'
from app.services.rating_history import _Sums


def bits(value):
    return struct.pack('>d', value)


rng = random.Random(20261022)
checked = 0; zero_error = 0; failures = []
for exponent in (0, 1, 5, 26, 52, 53, 100, 500, 1000, 1022, 1074):
    for index in range(400):
        parts = rng.randint(1, 40)
        # Dyadic floats sharing this quantum. The sum has at most 53 bits;
        # all partial sums are bounded by the same nonnegative final total.
        integers = [rng.randint(0, (2**53 // parts) - 1) for _ in range(parts)]
        values = [math.ldexp(integer, -exponent) for integer in integers]
        assert sum(integers) <= 2**53
        start = rng.randrange(parts); stop = rng.randrange(start + 1, parts + 1)
        for divisor in (1, 60):
            expected = sum(values[start:stop]) / divisor
            actual, error = _Sums(values).total(start, stop, divisor=divisor)
            checked += 1; zero_error += error == 0
            if error != 0 or bits(actual) != bits(expected):
                failures.append(dict(exponent=exponent, index=index, start=start, stop=stop,
                    divisor=divisor, actual=actual, expected=expected, error=error))

# Include-extra may introduce a finer quantum than the constructor's input.
for values, extra in [([.5, 1., 2.], 2**-20), ([0.], math.ldexp(1., -1074)),
                      ([2**52, 1.], 1.), ([0.125, 0.25], 0.0625)]:
    for divisor in (1, 60):
        expected = sum(values + [extra]) / divisor
        actual, error = _Sums(values).total(0, len(values), extra, True, divisor)
        checked += 1
        if error == 0:
            zero_error += 1
            if bits(actual) != bits(expected):
                failures.append(dict(values=values, extra=extra, divisor=divisor, actual=actual, expected=expected))

report = dict(seed=20261022, cases=checked, zero_error_cases=zero_error, failures=failures,
    source_sha256=hashlib.sha256((ROOT / 'app/services/rating_history.py').read_bytes()).hexdigest(),
    premises=['All values are finite nonnegative IEEE doubles.',
              'Each value is an integer multiple of a common quantum 2^-e, 0<=e<=1074.',
              'When the total integer count is <=2^53, each nonnegative chronological partial sum has <=2^53 quanta.',
              'All such partial sums are representable, so chronological addition and exact scaled-integer addition coincide.',
              'The final division by 60 occurs after the same exact sum in both implementations.'])
(QA / 'exact_sum_proof.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
assert not failures
