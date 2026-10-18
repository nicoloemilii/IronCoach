'use strict';
// Synthetic DOM only: no browser, API, device or account access.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const source = fs.readFileSync(path.join(__dirname, '../usability.js'), 'utf8');

function setup(t, { dismissed = false, blockedStorage = false, snapshot = null } = {}) {
  const errors = []; const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(html, {url:'https://fixture.invalid/app', runScripts:'outside-only', virtualConsole});
  const { window } = dom; const $ = selector => window.document.querySelector(selector);
  if (dismissed) window.sessionStorage.setItem('iron-coach:hide-next-steps:v1', '1');
  if (blockedStorage) Object.defineProperty(window, 'sessionStorage', { get() { throw new Error('Storage unavailable'); } });
  if (snapshot) window.ironViewSnapshot = snapshot;
  window.eval(source); t.after(() => { assert.deepEqual(errors, []); window.close(); });
  const loaded = values => window.document.dispatchEvent(new window.CustomEvent('iron:loaded', { detail: values }));
  const page = (id, userInitiated) => window.document.dispatchEvent(new window.CustomEvent('iron:pagechange', { detail:{id,userInitiated} }));
  return { $, window, loaded, page };
}

test('initial setup offers profile and activity actions without demanding wellness or accounts', t => {
  const { $, loaded } = setup(t);
  assert.equal($('#getting-started').hidden, true);
  loaded({hasProfile:false,hasActivities:false,intervalsConfigured:false,hasGoals:false});
  assert.equal($('#getting-started').hidden, false);
  assert.equal($('#getting-started-steps').children.length, 2);
  assert.equal($('#getting-started-steps a').getAttribute('href'), '#athlete');
  assert.match($('#getting-started-steps').textContent, /JSON o FIT/);
  assert.equal($('#getting-started-goal').hidden, false);
  assert.doesNotMatch($('#getting-started').textContent, /obbligatorio|HRV|Strava/);
});

test('manual/imported activities and a profile finish setup without requiring a goal or connection', t => {
  const { $, loaded } = setup(t);
  loaded({hasProfile:true,hasActivities:true,intervalsConfigured:false,hasGoals:false});
  assert.equal($('#getting-started').hidden, true);
  loaded({hasProfile:false,hasActivities:true,intervalsConfigured:false,hasGoals:true});
  assert.equal($('#getting-started-steps').children.length, 1);
  assert.equal($('#getting-started-steps a').getAttribute('href'), '#athlete');
  assert.equal($('#getting-started-goal').hidden, true);
});

test('configured Intervals leads directly to synchronization instead of another connection request', t => {
  const { $, loaded } = setup(t);
  loaded({hasProfile:true,hasActivities:false,intervalsConfigured:true,hasGoals:true});
  assert.equal($('#getting-started-steps').children.length, 1);
  assert.match($('#getting-started-steps').textContent, /Intervals.icu è configurato/);
  assert.equal($('#getting-started-steps a').getAttribute('href'), '#connections');
});

test('dismissing guidance is retained for the session and places focus on visible content', t => {
  const { $, window, loaded } = setup(t);
  loaded({hasProfile:false,hasActivities:false}); $('#dismiss-getting-started').click();
  assert.equal($('#getting-started').hidden, true);
  assert.equal(window.sessionStorage.getItem('iron-coach:hide-next-steps:v1'), '1');
  assert.equal(window.document.activeElement, $('#today .section-heading h2'));
  loaded({hasProfile:false,hasActivities:false}); assert.equal($('#getting-started').hidden, true);
});

test('a previous dismissal and unavailable session storage do not break page loading', t => {
  const previous = setup(t, {dismissed:true}); previous.loaded({hasProfile:false,hasActivities:false});
  assert.equal(previous.$('#getting-started').hidden, true);
  const blocked = setup(t, {blockedStorage:true}); blocked.loaded({hasProfile:false,hasActivities:false});
  assert.equal(blocked.$('#getting-started').hidden, false);
  blocked.$('#dismiss-getting-started').click(); assert.equal(blocked.$('#getting-started').hidden, true);
});

test('current navigation has one accessible marker and user navigation focuses the page title', t => {
  const { $, window, page } = setup(t);
  assert.equal(window.document.querySelectorAll('nav [aria-current="page"]').length, 1);
  assert.equal($('nav [aria-current="page"]').dataset.page, 'today');
  page('health', true);
  assert.equal($('nav [aria-current="page"]').dataset.page, 'health');
  assert.equal($('nav [data-page="health"]').getAttribute('aria-controls'), 'health');
  assert.equal(window.document.activeElement, $('#page-title'));
});

test('refresh and programmatic page events preserve the cursor in an unfinished form', t => {
  const { $, window, page, loaded } = setup(t);
  const input = $('#checkin [name="sleep_hours"]'); input.value = '7.5'; input.focus();
  loaded({hasProfile:true,hasActivities:false,intervalsConfigured:false});
  assert.equal(window.document.activeElement, input);
  page('today', false); assert.equal(window.document.activeElement, input);
  page('missing-section', true); assert.equal(window.document.activeElement, input);
  assert.equal(input.value, '7.5');
});

test('unknown setup flags are not interpreted as missing user data', t => {
  const { $, loaded } = setup(t);
  loaded({}); assert.equal($('#getting-started').hidden, true);
  loaded(null); assert.equal($('#getting-started').hidden, true);
});

test('a snapshot published before the deferred script starts still renders the right guidance', t => {
  const { $, window } = setup(t, {snapshot:{hasProfile:true,hasActivities:false,intervalsConfigured:true,hasGoals:false}});
  assert.equal($('#getting-started').hidden, false);
  assert.equal($('#getting-started-steps').children.length, 1);
  assert.match($('#getting-started-steps').textContent, /Intervals.icu è configurato/);
  assert.notEqual(window.document.activeElement, $('#page-title'));
});

test('unchanged refreshes keep an onboarding link focused instead of replacing it', t => {
  const { $, window, loaded } = setup(t);
  const snapshot = {hasProfile:false,hasActivities:false,intervalsConfigured:false,hasGoals:false};
  loaded(snapshot); const link = $('#getting-started-steps a'); link.focus();
  loaded(snapshot);
  assert.equal(window.document.activeElement, link);
  assert.equal($('#getting-started-steps a'), link);
});
