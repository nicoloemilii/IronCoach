'use strict';
// Pure rule and synthetic DOM tests. No browser, provider or account access.
const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const status = require('../metric-status.js');

const day = '2026-10-18';
const previous = days => new Date(new Date(`${day}T12:00:00Z`).valueOf() - days * 86400000).toISOString().slice(0, 10);
const history = (values = [50, 51, 52, 53, 54, 55, 56], source = 'intervals') => values.map((value, i) => ({date:previous(i + 1), source, value}));
const metric = (kind, value, extra = {}) => ({kind, latest:{value, source:'intervals', date:day}, points:[], stale:false, ...extra});
const trend = extra => ({kind:'hrv_ms', current:53, baseline:52, stale:false, counts:{recent:3, baseline:7}, reference_range:{lower:47, upper:59, count:7}, ...extra});

test('missing, null and non-finite measurements stay unknown instead of becoming zero', () => {
  for (const value of [undefined, null, NaN, Infinity, -Infinity, '', '0']) {
    assert.equal(status.metric(metric('steps', value)).tone, 'unknown', String(value));
  }
  assert.equal(status.metric({kind:'steps'}).tone, 'unknown');
  assert.equal(status.metric(metric('steps', 0)).tone, 'neutral');
  assert.equal(status.metric(metric('body_battery', 0)).tone, 'critical');
  assert.equal(status.readiness(0, 100).tone, 'critical');
  assert.equal(status.assessment({score:0}).tone, 'critical');
  assert.equal(status.coverage(0).tone, 'caution');
});

test('stale observations and local estimates never receive a favorable state', () => {
  const points = history();
  assert.equal(status.metric(metric('hrv_ms', 53, {points, stale:true})).tone, 'unknown');
  assert.equal(status.metric(metric('hrv_ms', 53, {points:history(undefined, 'local_estimate'), latest:{value:53, source:'local_estimate', date:day}})).tone, 'neutral');
  assert.equal(status.metric(metric('tsb', -35, {latest:{value:-35, source:'local_estimate', date:day}})).tone, 'neutral');
  assert.equal(status.metric(metric('ctl', 100)).tone, 'neutral', 'A higher chronic load is not intrinsically favorable.');
});

test('personal history uses seven prior observations from the same source and the last 28 days', () => {
  const points = history();
  points.push({date:day, source:'intervals', value:999}, {date:previous(-1), source:'intervals', value:999}, {date:previous(29), source:'intervals', value:999});
  points.push(...history([1000, 1001, 1002, 1003, 1004, 1005, 1006], 'manual'));
  const band = status.band(metric('hrv_ms', 53, {points}));
  assert.equal(band.count, 7); assert.equal(band.lower, 47); assert.equal(band.upper, 59);
  assert.equal(status.band(metric('hrv_ms', 53, {points:history([50,51,52,53,54,55]).concat(history(undefined, 'manual'))})), null);
  assert.equal(status.band(metric('hrv_ms', 53, {latest:{value:53, source:'intervals'}, points:history()})), null);
});

test('personal outliers are never optimal merely because a value is higher or lower', () => {
  for (const [kind, values, current, low, high] of [
    ['hrv_ms', [50,51,52,53,54,55,56], 53, 46, 60],
    ['resting_hr_bpm', [50,51,52,53,54,55,56], 53, 46, 60],
    ['sleep_hours', [6,6.2,6.4,6.6,6.8,7,7.2], 6.6, 5.3, 8]
  ]) {
    const points = history(values);
    assert.equal(status.metric(metric(kind, current, {points})).tone, 'good', kind);
    assert.ok(['critical','caution','watch'].includes(status.metric(metric(kind, low, {points})).tone), `${kind}: low outlier`);
    assert.ok(['critical','caution','watch'].includes(status.metric(metric(kind, high, {points})).tone), `${kind}: high outlier`);
    assert.equal(status.metric(metric(kind, current, {points:[]})).tone, 'unknown', `${kind}: missing history`);
  }
  assert.equal(status.metric(metric('hrv_ms', 47, {points:history()})).tone, 'watch', 'A lower in-band reading can still be worth following relative to the median.');
  assert.equal(status.metric(metric('hrv_ms', 59, {points:history()})).tone, 'good', 'An inclusive upper boundary is not an outlier.');
});

test('individual classification uses the same source and 28-day horizon as the personal interval', () => {
  for (const kind of ['hrv_ms','resting_hr_bpm']) {
    const clean = history();
    const contaminated = clean.concat(history([1000,1001,1002,1003,1004,1005,1006], 'manual'),
      Array.from({length:10}, (_,i) => ({date:previous(29+i),source:'intervals',value:999})),
      [{date:previous(-1),source:'intervals',value:999}, {date:day,source:'intervals',value:999}]);
    assert.equal(status.metric(metric(kind, 53, {points:contaminated})).tone,
      status.metric(metric(kind, 53, {points:clean})).tone, kind);
  }
});

test('nonpositive HRV and resting heart rate cannot be green or contribute to a historical reference', () => {
  for (const kind of ['hrv_ms','resting_hr_bpm']) {
    for (const value of [0,-1]) assert.equal(status.metric(metric(kind, value, {points:history(), reference_range:{lower:0, upper:100, count:7}})).tone, 'caution', `${kind}: ${value}`);
    const points = history([50,51,52,53,54,55,0,-10]);
    assert.equal(status.band(metric(kind, 53, {points})), null, 'Six valid readings plus invalid zeros do not form a reference.');
  }
});

test('TSB exposes all five levels and ATL only uses matching source and date context', () => {
  assert.equal(status.metric(metric('tsb', -30)).tone, 'critical');
  assert.equal(status.metric(metric('tsb', -29.9)).tone, 'caution');
  assert.equal(status.metric(metric('tsb', -25)).tone, 'caution');
  assert.equal(status.metric(metric('tsb', -24.9)).tone, 'watch');
  assert.equal(status.metric(metric('tsb', -15)).tone, 'positive');
  assert.equal(status.metric(metric('tsb', -5)).tone, 'good');
  assert.equal(status.metric(metric('tsb', 30)).tone, 'watch');
  const atl = metric('atl', 70);
  assert.equal(status.metric(atl, [metric('tsb', -26)]).tone, 'caution');
  for (const balance of [
    metric('tsb', -26, {latest:{value:-26, source:'manual', date:day}}),
    metric('tsb', -26, {latest:{value:-26, source:'intervals', date:previous(1)}}),
    metric('tsb', -26, {stale:true}), metric('tsb', null), metric('tsb', -24)
  ]) assert.equal(status.metric(atl, [balance]).tone, 'neutral');
  for (const [ctl, tone] of [[40,'critical'], [45,'caution'], [50,'watch'], [60,'positive'], [70,'good']]) {
    assert.equal(status.metric(atl,[metric('ctl',ctl)]).tone,tone,`ATL/CTL ${70/ctl}`);
  }
  assert.equal(status.metric(atl,[metric('ctl',40,{latest:{value:40,date:previous(1),source:'intervals'}})]).tone,'neutral');
});

test('readiness boundaries preserve uncertainty and distinguish recovery priorities', () => {
  for (const value of [null, undefined, NaN, -1, 101]) assert.equal(status.readiness(value, 100).tone, 'unknown');
  assert.equal(status.readiness(34.9, 100).tone, 'critical');
  assert.equal(status.readiness(35, 100).tone, 'caution');
  assert.equal(status.readiness(49.9, 100).tone, 'caution');
  assert.equal(status.readiness(50, 100).tone, 'watch');
  assert.equal(status.readiness(64.9, 100).tone, 'watch');
  assert.equal(status.readiness(65, 100).tone, 'positive');
  assert.equal(status.readiness(79.9, 100).tone, 'positive');
  assert.equal(status.readiness(80, 100).tone, 'good');
  assert.equal(status.readiness(100, 100).tone, 'good');
  assert.equal(status.readiness(90, 49).tone, 'unknown');
  assert.equal(status.readiness(90, 50).tone, 'good');
});

test('assessment and target coverage thresholds do not mark missing evidence as a failure', () => {
  assert.equal(status.assessment({}).tone, 'unknown');
  for (const score of [null, NaN, -1, 101]) assert.equal(status.assessment({score}).tone, 'unknown');
  for (const [score,tone] of [[0,'critical'],[34.9,'critical'],[35,'caution'],[49.9,'caution'],[50,'watch'],[64.9,'watch'],[65,'positive'],[79.9,'positive'],[80,'good'],[100,'good']]) {
    assert.equal(status.assessment({score}).tone,tone,String(score));
  }
  assert.equal(status.coverage(99.9).tone, 'caution');
  assert.equal(status.coverage(100).tone, 'good');
  for (const score of [null, NaN, -1, 101]) assert.equal(status.coverage(score).tone, 'unknown');
  assert.equal(status.coverage(100, true).tone, 'neutral', 'A personal confirmation is contextual, not measured performance.');
});

test('six explicit levels have distinct colors and unknown evidence never masquerades as detraining', () => {
  const tones=['critical','caution','watch','positive','good','detraining'];
  assert.equal(new Set(tones.map(tone=>status.palette[tone])).size,6);
  for (const tone of tones) assert.match(status.palette[tone],/^#[0-9a-f]{6}$/i,tone);
  const rgb=tone=>status.palette[tone].slice(1).match(/../g).map(value=>parseInt(value,16)/255);
  const hue=tone=>{
    const [r,g,b]=rgb(tone),max=Math.max(r,g,b),min=Math.min(r,g,b),delta=max-min;
    return ((max===r?(g-b)/delta:max===g?(b-r)/delta+2:(r-g)/delta+4)*60+360)%360;
  };
  for(const [tone,minimum,maximum] of [['critical',0,15],['caution',15,45],['watch',45,70],['positive',70,110],['good',110,170]]){
    assert.ok(hue(tone)>=minimum&&hue(tone)<=maximum,`${tone} keeps its red-to-green color identity`);
  }
  assert.ok(Math.max(...rgb('detraining'))-Math.min(...rgb('detraining'))<.15,'Detraining is a muted gray.');
  assert.notEqual(status.palette.unknown,status.palette.detraining);
  for (const [score,tone,level] of [[0,'critical',1],[40,'caution',2],[55,'watch',3],[70,'positive',4],[85,'good',5]]) {
    const result=status.five(score,'Etichetta','Motivo');
    assert.equal(result.tone,tone); assert.equal(result.level,level);
  }
  for (const item of [metric('ctl',null),metric('ctl',50,{stale:true}),metric('ctl',50),metric('hrv_ms',null)]) {
    assert.notEqual(status.metric(item).tone,'detraining');
  }
  assert.notEqual(status.readiness(null).tone,'detraining');
  assert.notEqual(status.assessment({score:null}).tone,'detraining');
});

test('gray requires an observed CTL decline of more than ten percent over at least fourteen days', () => {
  const recent=history([51,52,53,54,55,56]);
  const anchor={date:previous(14),source:'intervals',value:60};
  assert.equal(status.metric(metric('ctl',48,{points:recent.concat(anchor)})).tone,'detraining');
  assert.equal(status.metric(metric('ctl',48,{points:recent.concat(anchor)})).level,6);
  assert.notEqual(status.metric(metric('ctl',48,{points:history([60,59,58,57,56,55,54])})).tone,'detraining','A short decline does not establish detraining.');
  assert.notEqual(status.metric(metric('ctl',54,{points:recent.concat(anchor)})).tone,'detraining','Exactly ten percent does not cross the declared rule.');
  assert.notEqual(status.metric(metric('ctl',48,{points:recent.concat({...anchor,source:'manual'})})).tone,'detraining','A different source is not a reference.');
  assert.notEqual(status.metric(metric('ctl',48,{points:recent.concat(anchor),stale:true})).tone,'detraining');
  assert.notEqual(status.metric(metric('ctl',48,{points:recent.concat(anchor),latest:{value:48,date:day,source:'local_estimate'}})).tone,'detraining');
});

test('CTL reference selection is chronological even when observations arrive out of order', () => {
  const points=history([51,52,53,54,55,56]);
  const recentAnchor={date:previous(14),source:'intervals',value:100};
  const olderAnchor={date:previous(28),source:'intervals',value:50};
  const first=status.metric(metric('ctl',48,{points:points.concat(recentAnchor,olderAnchor)}));
  const second=status.metric(metric('ctl',48,{points:points.concat(olderAnchor,recentAnchor)}));
  assert.equal(first.tone,'detraining'); assert.deepEqual(first,second);
});

test('each metric keeps its own color even while the weekly workload is excessive', () => {
  const peers=[metric('tsb',-35),metric('ctl',50),metric('atl',85),
    metric('sleep_hours',6.6,{points:history([6,6.2,6.4,6.6,6.8,7,7.2])}),
    metric('hrv_ms',44,{points:history()})];
  assert.equal(status.metric(peers[0],peers).tone,'critical');
  assert.equal(status.metric(peers[3],peers).tone,'good');
  assert.equal(status.metric(peers[4],peers).tone,'watch');
  assert.equal(new Set(peers.map(item=>status.metric(item,peers).tone)).size>=3,true);
});

test('trends require explicit recent and baseline counts plus a usable historical interval', () => {
  for (const counts of [undefined, null, {}, {recent:3}, {baseline:7}, {recent:2, baseline:7}, {recent:3, baseline:6}, {recent:NaN, baseline:7}, {recent:3, baseline:Infinity}]) {
    assert.equal(status.trend(trend({counts})).tone, 'unknown', JSON.stringify(counts));
  }
  assert.equal(status.trend(trend()).tone, 'good');
  assert.equal(status.trend(trend({current:60})).tone, 'watch');
  assert.equal(status.trend(trend({current:46})).tone, 'watch');
  assert.equal(status.trend(trend({reference_range:null})).tone, 'unknown');
  assert.equal(status.trend(trend({reference_range:{lower:NaN, upper:NaN, count:7}})).tone, 'unknown');
  assert.equal(status.trend(trend({stale:true})).tone, 'unknown');
});

test('DOM application is idempotent and preserves accessible status labels and existing content', t => {
  const dom = new JSDOM('<article><h2>Sonno</h2><strong>7 ore</strong><small>Intervals.icu</small><p class="original">Spiegazione iniziale.</p></article>');
  t.after(() => dom.window.close()); const card = dom.window.document.querySelector('article');
  status.apply(card, status.metric(metric('sleep_hours', 6.6, {points:history([6,6.2,6.4,6.6,6.8,7,7.2])})));
  status.apply(card, status.metric(metric('sleep_hours', 8, {points:history([6,6.2,6.4,6.6,6.8,7,7.2])})));
  assert.ok(['watch','caution','critical'].includes(card.dataset.tone));
  assert.equal(card.querySelectorAll('.metric-status').length, 1);
  assert.equal(card.querySelectorAll('.status-reason').length, 1);
  assert.equal(card.querySelector('strong').nextElementSibling.className, 'metric-status');
  assert.equal(card.querySelector('.metric-status-icon').getAttribute('aria-hidden'), 'true');
  const label = card.querySelector('.metric-status>span:last-child');
  assert.ok(label.textContent.length > 0); assert.notEqual(label.getAttribute('aria-hidden'), 'true');
  assert.equal(card.querySelector('.original').textContent, 'Spiegazione iniziale.');
  assert.equal(card.querySelector('small').textContent, 'Intervals.icu');
  status.apply(card, status.unknown('Servono altri dati.'), false);
  assert.equal(card.dataset.tone, 'unknown'); assert.equal(card.querySelectorAll('.status-reason').length, 0);
});

test('DOM labels and reasons are inert text and invalid tones fall back to an explicit unknown icon', t => {
  const dom = new JSDOM('<article></article>'); t.after(() => dom.window.close());
  const card = dom.window.document.querySelector('article');
  status.apply(card, {tone:'invalid-tone', label:'<img src=x onerror=alert(1)>', reason:'<script>danger()</script>'});
  assert.equal(card.dataset.tone, 'unknown');
  assert.equal(card.querySelector('img,script'), null);
  assert.match(card.querySelector('.metric-status').textContent, /<img/);
  assert.match(card.querySelector('.status-reason').textContent, /<script>/);
  assert.equal(card.querySelector('.metric-status-icon').textContent, '?');
  assert.doesNotThrow(() => status.apply(null, status.unknown()));
});
