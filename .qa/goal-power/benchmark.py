"""Isolated raw-curve benchmark. No DB, provider or app.main."""
from pathlib import Path
import hashlib,json,random,sys,time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from app.services.activity_effort import parse_streams
from app.services.power_duration import best_power_efforts
HERE=Path(__file__).resolve().parent;report={'synthetic_only':True,'scenarios':[]}
count=int(sys.argv[1]) if len(sys.argv)>1 else 250000
for kind in ('constant','periodic','random'):
    randomizer=random.Random(42)
    times=list(range(count));power=([200]*count if kind=='constant' else [100+index%2*100 for index in times]
                                  if kind=='periodic' else [randomizer.randrange(50,400) for _ in times])
    started=time.perf_counter();parsed=parse_streams([{'type':'time','data':times},{'type':'watts','data':power}],elapsed_seconds=count-1)
    parsed_seconds=time.perf_counter()-started;started=time.perf_counter();value=best_power_efforts(parsed);seconds=time.perf_counter()-started
    assert value['status']=='available'
    item={'points':count,'kind':kind,'parse_seconds':parsed_seconds,'curve_seconds':seconds,'durations':[row['status'] for row in value['durations']]}
    report['scenarios'].append(item);report['source_sha256']=hashlib.sha256((ROOT/'app/services/power_duration.py').read_bytes()).hexdigest()
    (HERE/f'benchmark-{count}.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(item),flush=True)
