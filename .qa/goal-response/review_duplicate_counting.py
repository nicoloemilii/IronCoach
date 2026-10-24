"""Compare only resolved-pair counting, using synthetic exact-group membership."""
from collections import Counter
import json
from pathlib import Path
import time


def old_counts(groups, resolved):
    return [sum(set(pair) <= identifiers for pair in resolved) for identifiers in groups]


def indexed_counts(groups, resolved):
    membership = {identifier: index for index, identifiers in enumerate(groups) for identifier in identifiers}
    reviewed = Counter()
    for first, second in resolved:
        index = membership.get(first)
        if index is not None and index == membership.get(second):
            reviewed[index] += 1
    return [reviewed[index] for index in range(len(groups))]


def run():
    reports = []
    for count in (500, 2000):
        groups = [{index * 2 + 1, index * 2 + 2} for index in range(count)]
        resolved = {tuple(sorted(identifiers)) for identifiers in groups}
        resolved.update([(1, 3), (1, count * 2 + 100), (count * 2 + 200, count * 2 + 201)])
        begin = time.perf_counter()
        expected = old_counts(groups, resolved)
        old_ms = (time.perf_counter() - begin) * 1000
        begin = time.perf_counter()
        actual = indexed_counts(groups, resolved)
        indexed_ms = (time.perf_counter() - begin) * 1000
        assert actual == expected == [1] * count
        reports.append({'exact_groups': count, 'resolved_pairs': len(resolved),
                        'old_component_ms': round(old_ms, 3), 'indexed_component_ms': round(indexed_ms, 3),
                        'all_group_counts_identical': True,
                        'cross_group_and_unselected_pairs_ignored': True})
    output = {'scope': 'Synthetic counting component only, excluding SQL/HTTP/frontend; not a production SLA',
              'complexity': {'old': 'groups * resolved_pairs', 'indexed': 'group_members + resolved_pairs + groups'},
              'reports': reports}
    Path(__file__).with_name('review_duplicate_counting.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps(output))


if __name__ == '__main__':
    run()
