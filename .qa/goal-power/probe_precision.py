from pathlib import Path
import sys,json,math,random
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[1]))
from oracle import continuous_best
from app.services.activity_effort import parse_streams
from app.services.power_duration import best_power_efforts
rng=random.Random(31);failures=[];count=0
for magnitude in (1e-308,1e-10,1.,100.,1e308):
    for trial in range(12):
        times=[index for index in range(12)]
        powers=[magnitude+math.ulp(magnitude)*rng.randrange(-4,5) for _ in times]
        expected=continuous_best(times,powers,5)
        value=best_power_efforts(parse_streams([{'type':'time','data':times},{'type':'watts','data':powers}]))['durations'][0]
        count+=1
        if value['status']!='available' or abs(value['start_s']-float(expected['start']))>1e-6:
            failures.append(dict(magnitude=magnitude,trial=trial,powers=powers,expected_start=float(expected['start']),actual=value))
report=dict(cases=count,failures=failures)
(HERE/'probe_precision.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(dict(cases=count,failures=len(failures),first=failures[:1])))
assert not failures
