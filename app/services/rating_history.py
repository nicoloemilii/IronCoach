"""Request-local history indexes; ambiguous floating-point boundaries fall back.

Counts/medians are exact. Range sums use exact binary integer prefixes, with a
conservative bound for the legacy chronological floating-point sum. When that
bound can change a public score or formatted explanation, the legacy calculation
is used. Adversarial boundary-heavy inputs can therefore retain its higher cost.
"""
from bisect import bisect_left
from collections import defaultdict,deque
import math
from statistics import median
import sys

from app.services.athlete_clock import as_utc_naive,activity_day
from app.services.training_load import sport_key
from app.services.observed_load import finite_load


class _Median:
    def __init__(self,values):
        self.values=sorted(set(values));self.positions={value:index+1 for index,value in enumerate(self.values)}
        self.tree=[0]*(len(self.values)+1);self.count=0

    def add(self,value,delta):
        if value is None or value<=0:return
        index=self.positions[value];self.count+=delta
        while index<len(self.tree):self.tree[index]+=delta;index+=index&-index

    def select(self,rank):
        index=0;step=1<<(len(self.values).bit_length()-1)
        while step:
            candidate=index+step
            if candidate<len(self.tree) and self.tree[candidate]<rank:
                rank-=self.tree[candidate];index=candidate
            step>>=1
        return self.values[index]

    def median(self):
        middle=self.count//2
        return self.select(middle+1) if self.count%2 else (self.select(middle)+self.select(middle+1))/2


class _Sums:
    def __init__(self,values):
        values=[float(value) if value is not None else None for value in values]
        ratios=[value.as_integer_ratio() if value is not None else None for value in values]
        exponent=max((denominator.bit_length()-1 for pair in ratios if pair for denominator in [pair[1]]),default=0)
        self.scale=1<<exponent;self.exponent=exponent;self.sums=[0];self.missing=[0]
        for value,pair in zip(values,ratios):
            exact=pair[0]<<(exponent-(pair[1].bit_length()-1)) if pair else 0
            self.sums.append(self.sums[-1]+exact)
            self.missing.append(self.missing[-1]+(value is None))

    def total(self,start,end,extra=None,include_extra=False,divisor=1):
        if self.missing[end]!=self.missing[start] or (include_extra and extra is None):return None
        exact=self.sums[end]-self.sums[start];count=end-start
        if include_extra:
            extra=float(extra)
            numerator,denominator=extra.as_integer_ratio();shift=self.exponent-(denominator.bit_length()-1)
            if shift<0:
                exact<<=-shift;scale=self.scale<<-shift;exact+=numerator
            else:
                scale=self.scale;exact+=numerator<<shift
            count+=1
        else:scale=self.scale
        try:value=exact/scale
        except OverflowError:return 'fallback'
        if not math.isfinite(value):return 'fallback'
        error=0.
        # Nonnegative readings are integer multiples of the common binary
        # quantum. Below 2**53 quanta every chronological partial sum is exact,
        # including dyadic fractions such as .5; no rounding fallback is needed.
        if abs(exact)>2**53:
            gamma=count*sys.float_info.epsilon
            if gamma>=.1:return 'fallback'
            error=abs(value)*(gamma/(1-gamma))+math.ulp(value)+count*math.ulp(0.)
        return value/divisor,error/divisor


class HistoryContext:
    def __init__(self,activities,loads,health,component,legacy_distribution,recovery,stamp_before):
        self.rows=activities;self.loads=loads;self.component=component;self.legacy_distribution=legacy_distribution
        self.recovery_fn=recovery;self.stamp_before=stamp_before;self.active=deque();self.unknown=0;self.fallbacks=0
        self.stamps=[as_utc_naive(row.start_time) for row in activities]
        self.positions={row.id:index for index,row in enumerate(activities)}
        self.kinds={row.id:sport_key(row.sport) for row in activities}
        self.duration={row.id:finite_load(row.duration_seconds) for row in activities}
        self.load_sums=_Sums([loads[row.id] for row in activities]);self.duration_sums=_Sums([self.duration[row.id] for row in activities])
        values=defaultdict(lambda:[[],[]])
        for row in activities:
            load=loads[row.id];duration=self.duration[row.id];key=self.kinds[row.id]
            if load is not None and load>0:values[key][0].append(load)
            if duration is not None and duration>0:values[key][1].append(duration)
        self.medians={key:(_Median(pair[0]),_Median(pair[1])) for key,pair in values.items()}
        self.health={};self.health_dates={};self.recovery_cache={}
        for kind,points in health.items():
            ordered=sorted(points,key=lambda point:point['date']);self.health[kind]=ordered
            self.health_dates[kind]=[point['date'] for point in ordered]

    def advance(self,stamp):
        lower=self.stamp_before(stamp,42)
        while self.active and as_utc_naive(self.active[0].start_time)<lower:
            row=self.active.popleft();self._update(row,-1)

    def _update(self,row,delta):
        pair=self.medians.get(self.kinds[row.id])
        if pair:
            pair[0].add(self.loads[row.id],delta);pair[1].add(self.duration[row.id],delta)
        self.unknown+=delta*(self.loads[row.id] is None)

    def add(self,row):self.active.append(row);self._update(row,1)

    def dose(self,activity):
        load=self.loads[activity.id];duration=self.duration[activity.id];pair=self.medians.get(self.kinds[activity.id])
        if load is not None and load>0 and pair and pair[0].count>=3:
            value,baseline,unit=load,pair[0].median(),'punti di carico'
        elif duration is not None and duration>0 and pair and pair[1].count>=3:
            value,baseline,unit=duration/60,pair[1].median()/60,'minuti'
        else:
            return self.component('stimulus','Stimolo registrato',65,60,
                'Seduta registrata. Mancano tre precedenti della stessa disciplina per valutarne la proporzione.'),False
        if baseline<=0 or not math.isfinite(baseline) or not math.isfinite(value):
            return self.component('stimulus','Stimolo registrato',65,60,
                'Il riferimento numerico non è utilizzabile per confrontare questa seduta.'),False
        ratio=value/baseline;score=85 if ratio<=1.3 else 70 if ratio<=1.6 else 50 if ratio<=2 else 30
        description=('Stimolo contenuto: compatibile con una seduta breve o di recupero.' if ratio<.5 else
                     'Stimolo proporzionato alle sedute recenti.' if ratio<=1.3 else
                     'Stimolo superiore al solito: richiede una motivazione nel piano.' if ratio<=1.6 else
                     'Incremento marcato della singola seduta: il recupero successivo va protetto.')
        return self.component('stimulus','Proporzione dello stimolo',score,60,
            f'{value:.0f} {unit}; riferimento personale {baseline:.0f}. {description}'),True

    def _fallback(self,activity):
        self.fallbacks+=1
        return self.legacy_distribution(activity,self.loads[activity.id],list(self.active),self.loads)

    def distribution(self,activity):
        stamp=as_utc_naive(activity.start_time)
        if stamp.toordinal()<=21 or not self.active or as_utc_naive(self.active[0].start_time)>self.stamp_before(stamp,21):return None
        indices=[bisect_left(self.stamps,self.stamp_before(stamp,days)) for days in (28,21,14,7,0)]
        use_load=self.loads[activity.id] is not None and self.unknown==0
        sums=self.load_sums if use_load else self.duration_sums
        extra=self.loads[activity.id] if use_load else self.duration[activity.id]
        groups=[sums.total(indices[index],indices[index+1],extra,include_extra=index==3,divisor=1 if use_load else 60) for index in range(4)]
        if any(group=='fallback' for group in groups):return self._fallback(activity)
        current=groups[3];reference=[group for group in groups[:3] if group is not None and group[0]>0]
        if current is None or len(reference)<2:return None
        value,error=current;baseline=median(group[0] for group in reference)
        if not math.isfinite(baseline) or baseline<=0:return self._fallback(activity)
        ratio=value/baseline
        if not math.isfinite(ratio):return self._fallback(activity)
        if error or any(group[1] for group in reference):
            low=median(max(0.,value-error) for value,error in reference)
            high=median(value+error for value,error in reference)
            if low<=0 or not math.isfinite(high):return self._fallback(activity)
            rlo=math.nextafter(max(0.,value-error)/high,-math.inf);rhi=math.nextafter((value+error)/low,math.inf)
            bucket=lambda ratio:85 if ratio<=1.15 else 75 if ratio<=1.3 else 55 if ratio<=1.5 else 30
            if (bucket(rlo)!=bucket(rhi) or (rlo>1.3)!=(rhi>1.3) or
                f'{max(0.,value-error):.0f}'!=f'{value+error:.0f}' or f'{low:.0f}'!=f'{high:.0f}' or
                f'{(rlo-1)*100:+.0f}'!=f'{(rhi-1)*100:+.0f}'):
                return self._fallback(activity)
        score=85 if ratio<=1.15 else 75 if ratio<=1.3 else 55 if ratio<=1.5 else 30
        label='Accumulo elevato: evita altro lavoro impegnativo.' if ratio>1.3 else 'Distribuzione compatibile con il volume recente.'
        return self.component('distribution','Accumulo negli ultimi 7 giorni',score,25,
            f'{value:.0f} {"punti" if use_load else "minuti"}, riferimento {baseline:.0f} ({(ratio-1)*100:+.0f}%). {label}')

    def recovery(self,activity):
        day=activity_day(activity.start_time)
        if day not in self.recovery_cache:
            lower=str(day.fromordinal(max(1,day.toordinal()-29)));upper=str(day)
            selected={kind:points[bisect_left(self.health_dates[kind],lower):bisect_left(self.health_dates[kind],upper)] for kind,points in self.health.items()}
            self.recovery_cache[day]=self.recovery_fn(activity,selected)
        cached=self.recovery_cache[day]
        return dict(cached) if cached is not None else None
