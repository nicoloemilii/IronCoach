import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_URL = "http://127.0.0.1:8000"

class ApiClient:
    def __init__(self, base_url=BASE_URL):
        self.base_url = base_url.rstrip("/")
    def _url(self, path, params=None):
        url=f"{self.base_url}{path}"
        if params:
            clean={k:v for k,v in params.items() if v is not None}
            if clean: url += "?" + urlencode(clean)
        return url
    def get(self,path,params=None):
        try:
            with urlopen(self._url(path,params),timeout=20) as r: return json.loads(r.read().decode('utf-8'))
        except HTTPError as e: raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8',errors='replace')[:300]}") from e
        except (URLError,TimeoutError) as e: raise RuntimeError(f"Backend non raggiungibile: {e}") from e
    def _request(self,method,path,payload=None,params=None,timeout=30):
        req=Request(self._url(path,params),data=json.dumps(payload or {}).encode('utf-8'),headers={'Content-Type':'application/json'},method=method)
        try:
            with urlopen(req,timeout=timeout) as r:
                raw=r.read().decode('utf-8'); return json.loads(raw) if raw else {}
        except HTTPError as e: raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8',errors='replace')[:300]}") from e
        except (URLError,TimeoutError) as e: raise RuntimeError(f"Backend non raggiungibile: {e}") from e
    def post(self,path,payload=None,params=None,timeout=30): return self._request('POST',path,payload,params,timeout)
    def put(self,path,payload=None,params=None,timeout=30): return self._request('PUT',path,payload,params,timeout)
    def health(self): return self.get('/health')
    def coach_today(self): return self.get('/coach/today')
    def activities(self): return self.get('/activities')
    def fitness(self): return self.get('/analysis/fitness')
    def fitness_sports(self): return self.get('/analysis/fitness/sports')
    def quality(self): return self.get('/analysis/quality')
    def profile(self): return self.get('/athlete/profile')
    def save_profile(self,payload): return self.put('/athlete/profile',payload)
    def save_daily_metrics(self,payload): return self.put('/coach/daily-metrics',payload)
    def workouts(self,limit=100): return self.get('/coach/workouts',{'limit':limit})
    def create_workout(self,sport=None): return self.post('/coach/workouts',{'sport':sport} if sport else {})
    def evaluate_workout(self,workout_id,payload): return self.post(f'/coach/workouts/{workout_id}/evaluate',payload)
    def match_workout(self,workout_id,payload=None): return self.post(f'/coach/workouts/{workout_id}/match',payload or {})
    def sync_intervals(self,days=3650): return self.post('/sync/intervals',params={'days':days},timeout=120)
    def import_fit(self,directory='data/fit'): return self.post('/import/fit',params={'directory':directory},timeout=120)
