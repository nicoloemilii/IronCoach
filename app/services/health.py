import math
from datetime import date, datetime, timedelta
from statistics import median
from app.db.iron_models import HealthMetric
from app.db.models import DailyMetrics

METRICS = {
    'ctl': {'unit': 'punti', 'remote': 'ctl', 'label': 'Fitness · CTL', 'text': 'Stima del carico cronico: descrive la continuità recente, non una prestazione.'},
    'atl': {'unit': 'punti', 'remote': 'atl', 'label': 'Fatica · ATL', 'text': 'Stima del carico acuto. Un valore elevato rispetto al tuo storico segnala lavoro recente, non misura direttamente la fatica del corpo.'},
    'tsb': {'unit': 'punti', 'remote': None, 'label': 'Forma · TSB', 'text': 'Bilancio tra fitness e fatica. Un valore negativo indica carico acuto superiore al cronico; va letto con sonno e sensazioni.'},
    'sleep_hours': {'unit': 'ore', 'remote': 'sleepSecs', 'label': 'Sonno', 'text': 'Il sonno sostiene il recupero. Durata e qualità percepita vanno interpretate insieme.'},
    'hrv_ms': {'unit': 'ms', 'remote': 'hrv', 'label': 'HRV', 'text': 'Confronta la variabilità cardiaca con il tuo riferimento personale e con misurazioni dello stesso metodo.'},
    'resting_hr_bpm': {'unit': 'bpm', 'remote': 'restingHR', 'label': 'FC a riposo', 'text': 'Variazioni rispetto al tuo riferimento possono accompagnare stress e recupero; da sole non ne identificano la causa.'},
    'steps': {'unit': 'passi', 'remote': 'steps', 'label': 'Passi', 'text': 'Descrivono il movimento quotidiano; non equivalgono al carico sportivo.'},
    'stress_score': {'unit': '/100', 'remote': None, 'label': 'Stress percepito (manuale)', 'text': 'Indicatore dipendente dalla fonte: lo stress percepito non è direttamente confrontabile con quello del dispositivo.'},
    'stress_level': {'unit': 'livello Intervals', 'remote': 'stress', 'label': 'Stress · Intervals', 'text': 'Valore nella scala originale Intervals. Non viene normalizzato su 100 né confrontato con lo stress percepito.'},
    'daily_activity_load': {'unit': 'punti', 'remote': None, 'label': 'Carico quotidiano (manuale)', 'text': 'Carico quotidiano inserito manualmente.'},
    'body_battery': {'unit': '/100', 'remote': None, 'label': 'Body Battery', 'text': 'Stima del dispositivo, da interpretare insieme a sensazioni e storico.'},
    'body_battery_min': {'unit': '/100', 'remote': 'BodyBatteryMin', 'label': 'Body Battery · minimo del giorno', 'text': 'Minimo giornaliero: non è il valore attuale e non viene usato come recupero corrente.'},
    'body_battery_max': {'unit': '/100', 'remote': 'BodyBatteryMax', 'label': 'Body Battery · massimo del giorno', 'text': 'Massimo giornaliero: non è il valore attuale e non viene usato come recupero corrente.'},
    'daily_ctl_load': {'unit': 'punti', 'remote': 'ctlLoad', 'label': 'Carico quotidiano · modello CTL', 'text': 'Carico del giorno utilizzato per il modello di fitness. Non è stress fisiologico né Body Battery.'},
    'daily_atl_load': {'unit': 'punti', 'remote': 'atlLoad', 'label': 'Carico quotidiano · modello ATL', 'text': 'Carico del giorno utilizzato per il modello di fatica; distinto dal carico quotidiano inserito manualmente.'},
}


def import_wellness(db, records):
    count = 0; errors = []
    if not isinstance(records, list):
        raise ValueError('Elenco wellness non valido.')
    for item in records:
        try:
            if not isinstance(item, dict):
                raise ValueError()
            day = date.fromisoformat(item['id']).isoformat()
            values = {}
            custom = item.get('customFields') if isinstance(item.get('customFields'), dict) else {}
            for kind, spec in METRICS.items():
                remote = spec['remote']
                if not remote or (remote not in item and remote not in custom):
                    continue
                value = item[remote] if remote in item else custom[remote]
                if value is not None:
                    if isinstance(value, bool):
                        raise ValueError()
                    value = float(value)
                    if not math.isfinite(value) or (kind not in ('tsb',) and value < 0):
                        raise ValueError()
                    if kind == 'sleep_hours':
                        value /= 3600
                        if value > 24: raise ValueError()
                    if kind in ('body_battery_min', 'body_battery_max') and value > 100: raise ValueError()
                values[kind] = value
            if item.get('ctl') is not None and item.get('atl') is not None:
                values['tsb'] = float(item['ctl']) - float(item['atl'])
            elif 'ctl' in item or 'atl' in item:
                values['tsb'] = None
            now = datetime.utcnow().isoformat() + 'Z'
            for kind, value in values.items():
                row = db.query(HealthMetric).filter_by(date=day, kind=kind, source='intervals').first()
                if not row:
                    row = HealthMetric(date=day, kind=kind, source='intervals'); db.add(row)
                row.value = value; row.unit = METRICS[kind]['unit']; row.observed_at = day; row.synced_at = now
            db.commit(); count += 1
        except (ValueError, TypeError, KeyError, OverflowError):
            db.rollback(); errors.append({'error': 'Giorno wellness non importato: dati non validi.'})
    return count, errors


def series(db, start, end):
    rows = db.query(HealthMetric).filter(HealthMetric.date >= str(start), HealthMetric.date <= str(end)).all()
    chosen = {}
    live_manual_days = set()
    # Legacy manual edits remain live; imported values never overwrite them.
    for row in db.query(DailyMetrics).filter(DailyMetrics.date >= str(start), DailyMetrics.date <= str(end)).all():
        live_manual_days.add(row.date)
        for kind, spec in METRICS.items():
            value = getattr(row, kind, None)
            if value is not None:
                chosen[(row.date, kind)] = dict(date=row.date, kind=kind, value=value, source='manual', unit=spec['unit'], observed_at=row.date, synced_at=None)
    for row in sorted(rows, key=lambda x: x.source == 'intervals'):
        key = (row.date, row.kind)
        # Migrated copies must not resurrect a manual value subsequently cleared.
        if row.source == 'manual' and row.date in live_manual_days: continue
        if row.value is None: continue
        chosen[key] = {k: getattr(row, k) for k in ('date', 'kind', 'value', 'source', 'unit', 'observed_at', 'synced_at')}
    return sorted(chosen.values(), key=lambda x: (x['date'], x['kind']))


def _baseline(points, latest):
    if not latest:
        return None
    baseline_start = str(date.fromordinal(max(1, date.fromisoformat(latest['date']).toordinal() - 28)))
    previous = [r['value'] for r in points
                if baseline_start <= r['date'] < latest['date'] and r['source'] == latest['source']]
    return round(median(previous), 2) if len(previous) >= 7 else None


def reference_range(points, positive_only=False):
    """Descriptive Tukey interval, not clinical or performance thresholds."""
    values = sorted(point['value'] for point in points
                    if isinstance(point.get('value'), (int, float)) and not isinstance(point['value'], bool)
                    and math.isfinite(point['value']) and (not positive_only or point['value'] > 0))
    if len(values) < 7:
        return None
    def quantile(q):
        index = (len(values) - 1) * q
        lo, hi = math.floor(index), math.ceil(index)
        return values[lo] + (values[hi] - values[lo]) * (index - lo)
    first, third = quantile(.25), quantile(.75)
    spread = third - first
    return {'lower': max(0, first - 1.5 * spread), 'upper': third + 1.5 * spread,
            'count': len(values), 'method': 'descriptive_iqr'}


def effective_metrics(db, day):
    day = date.fromisoformat(str(day))
    start = date.fromordinal(max(1, day.toordinal() - 28))
    points = series(db, start, day)
    latest = {r['kind']: r for r in points if r['date'] == str(day)}
    values = {kind: point['value'] for kind, point in latest.items()}
    for kind, baseline_key in [('hrv_ms', 'hrv_baseline_ms'), ('resting_hr_bpm', 'resting_hr_baseline_bpm')]:
        metric = latest.get(kind)
        if metric:
            baseline = _baseline([r for r in points if r['kind'] == kind], metric)
            # Preserve an explicitly supplied manual baseline when no derived
            # one exists. An imported signal must never use a manual baseline.
            if baseline is not None or metric['source'] != 'manual':
                values[baseline_key] = baseline
    return values


def health_response(db, start, end):
    points = series(db, date.fromordinal(max(1, start.toordinal() - 28)), end)
    result = []
    for kind, spec in METRICS.items():
        values = [r for r in points if r['kind'] == kind]
        visible = [r for r in values if r['date'] >= str(start)]
        latest = visible[-1] if visible else None
        baseline = _baseline(values, latest) if kind in ('hrv_ms', 'resting_hr_bpm') else None
        reference = None
        if latest and kind in ('hrv_ms', 'resting_hr_bpm', 'sleep_hours'):
            since = str(date.fromordinal(max(1, date.fromisoformat(latest['date']).toordinal() - 28)))
            reference = reference_range([point for point in values
                                         if since <= point['date'] < latest['date'] and point['source'] == latest['source']],
                                        positive_only=kind in ('hrv_ms', 'resting_hr_bpm'))
        result.append({'kind': kind, 'label': spec['label'], 'unit': spec['unit'], 'points': visible,
                       'latest': latest, 'baseline': baseline, 'reference_range': reference, 'explanation': spec['text'],
                       'stale': bool(latest and (end - date.fromisoformat(latest['date'])).days > 1)})
        if not latest:
            missing = {
                'body_battery': 'Non è disponibile un valore attuale di Body Battery. I minimi e massimi giornalieri sono misure diverse.',
                'body_battery_min': 'BodyBatteryMin non ricevuto. In Intervals.icu configura il campo wellness e abilita il download wellness da Garmin.',
                'body_battery_max': 'BodyBatteryMax non ricevuto. In Intervals.icu configura il campo wellness e abilita il download wellness da Garmin.',
                'stress_level': 'Stress Intervals non ricevuto: è una valutazione soggettiva, distinta dallo stress Garmin.',
                'stress_score': 'Nessuno stress percepito inserito; lo stress Garmin non viene sostituito da una stima.',
                'daily_activity_load': 'Campo manuale non compilato. Il carico allenante importato è mostrato nelle card CTL/ATL giornaliere.'
            }
            result[-1]['unavailable_reason'] = missing.get(kind, 'Nessuna osservazione disponibile nel periodo selezionato.')
    return {'from': str(start), 'to': str(end), 'metrics': result}


def recovery_response(db, day, activities, health_metrics):
    """Explain date-specific recovery without promoting yesterday's measures to today."""
    from app.services.readiness import calculate_daily_state
    from app.services.planning import load_settings
    from app.services.athlete_profile import load_profile
    row = db.query(DailyMetrics).filter_by(date=str(day)).first()
    keys = ('sleep_hours', 'hrv_ms', 'hrv_baseline_ms', 'resting_hr_bpm', 'resting_hr_baseline_bpm',
            'stress_score', 'steps', 'daily_activity_load', 'body_battery')
    metrics = {key: getattr(row, key) for key in keys} if row else {}
    metrics.update(effective_metrics(db, day))
    profile = load_settings(db)
    ftp = profile['thresholds']['cycling_ftp_watts']['value']
    if not ftp:
        legacy = load_profile()
        ftp = (legacy.get('cycling') or {}).get('ftp_watts') or legacy.get('ftp_watts') or 170
    result = calculate_daily_state(activities, metrics, ftp, day)
    labels = {'sleep': 'Sonno', 'hrv': 'HRV con riferimento personale', 'resting_hr': 'FC a riposo con riferimento personale',
              'stress': 'Stress', 'steps': 'Passi', 'daily_activity': 'Carico quotidiano', 'body_battery': 'Body Battery'}
    result['available_inputs'] = [label for key, label in labels.items() if result['factors'].get(key) is not None]
    result['missing_inputs'] = [label for key, label in labels.items() if result['factors'].get(key) is None]
    result['latest_observations'] = [{'label': item['label'], **item['latest']} for item in health_metrics
                                     if item['kind'] in ('sleep_hours', 'hrv_ms', 'resting_hr_bpm') and item['latest']]
    if result['readiness'] is None:
        result['explanation'] = f'Per il {day} non risultano indicatori di recupero utilizzabili. Il solo carico degli allenamenti non basta.'
        older = sorted({item['date'] for item in result['latest_observations'] if item['date'] < str(day)})
        if older:
            result['explanation'] += f' Le ultime misure visibili risalgono al {older[-1]}: non vengono assegnate automaticamente a un giorno diverso.'
    else:
        result['explanation'] = f"Stima basata su {len(result['available_inputs'])} indicatori disponibili su 7 per il {day}. Gli altri indicatori sono facoltativi; la copertura descrive i dati disponibili, non una probabilità di recupero."
    return result
