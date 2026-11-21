// Offline test for the run ID check in apps_script/Code.gs (fix D-10). Plain Node, no packages, no network.
//
// Apps Script cannot run here, so this loads Code.gs with the vm module and a small in-file mock of SpreadsheetApp,
// LockService, ContentService, MailApp, PropertiesService and Session. The real Apps Script runtime was NOT run.
//
//   node tests/test_codegs_idempotency.js
//
// Prints one JSON object {results: [{name, ok, detail}]} and exits 1 when any case fails.
// tests/test_fix_d10.py runs this file and turns every case into a unittest sub test.

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CODE = fs.readFileSync(path.join(__dirname, '..', 'apps_script', 'Code.gs'), 'utf8');
const KEY = 'k'.repeat(24);

// ---- mock of the Apps Script services ------------------------------------------------------------------------

function makeWorld() {
  const world = { sheets: [], mails: [], events: [], lockHeld: false, lockCalls: 0, failNextSetValues: false };

  function makeSheet(name) {
    const sheet = { name: name, rows: [] };   // rows: array of arrays, 0-based; sheet row n is rows[n - 1]
    sheet.getName = function () { return name; };
    sheet.getLastRow = function () { return sheet.rows.length; };
    sheet.appendRow = function (row) { sheet.rows.push(row.slice()); world.events.push('write:' + name); };
    sheet.setFrozenRows = function () {};
    sheet.getRange = function (row, col, numRows, numCols) {
      numRows = numRows || 1;
      numCols = numCols || 1;
      world.events.push('range:' + name + ':' + row + ',' + col + ',' + numRows + ',' + numCols);
      const range = { text: false };
      range.setNumberFormat = function (format) { range.text = format === '@'; return range; };
      range.setFontWeight = function () { return range; };
      range.getValues = function () {
        world.events.push('read:' + name);
        const out = [];
        for (let r = 0; r < numRows; r++) {
          const line = [];
          for (let c = 0; c < numCols; c++) {
            const have = sheet.rows[row - 1 + r];
            line.push(have && have[col - 1 + c] !== undefined ? have[col - 1 + c] : '');
          }
          out.push(line);
        }
        return out;
      };
      range.setValues = function (values) {
        if (world.failNextSetValues) { world.failNextSetValues = false; throw new Error('write failed'); }
        world.events.push('write:' + name);
        values.forEach(function (line, r) {
          const target = sheet.rows[row - 1 + r] || (sheet.rows[row - 1 + r] = []);
          line.forEach(function (value, c) {
            // Google Sheets turns text that looks like a number into a number, unless the cell format is plain text.
            target[col - 1 + c] = (!range.text && typeof value === 'string' && /^\d+$/.test(value)) ? Number(value) : value;
          });
        });
        return range;
      };
      return range;
    };
    return sheet;
  }

  const spreadsheet = {
    getSheets: function () { return world.sheets; },
    getSheetByName: function (n) { return world.sheets.filter(function (s) { return s.name === n; })[0] || null; },
    insertSheet: function (n) { const s = makeSheet(n); world.sheets.push(s); return s; },
  };

  const props = { KEY: KEY, SPREADSHEET_ID: 'sheet-id', EMAIL_TO: 'owner@example.com' };

  const sandbox = {
    console: { error: function () {}, log: function () {} },
    SpreadsheetApp: { openById: function () { return spreadsheet; }, flush: function () { world.events.push('flush'); } },
    PropertiesService: { getScriptProperties: function () { return { getProperty: function (k) { return props[k] === undefined ? null : props[k]; } }; } },
    ContentService: {
      MimeType: { JSON: 'JSON' },
      createTextOutput: function (text) { return { getContent: function () { return text; }, setMimeType: function () { return this; } }; },
    },
    MailApp: { sendEmail: function (message) { world.mails.push(message); world.events.push('mail'); } },
    Session: { getEffectiveUser: function () { return { getEmail: function () { return 'owner@example.com'; } }; } },
    Utilities: { newBlob: function () { return {}; } },
    LockService: {
      getScriptLock: function () {
        return {
          waitLock: function () {
            if (world.lockHeld) throw new Error('lock already held');
            if (world.whileWaiting) {   // another call finishes while this one waits for the lock
              const other = world.whileWaiting;
              world.whileWaiting = null;
              other();
            }
            world.lockHeld = true; world.lockCalls++; world.events.push('lock');
          },
          releaseLock: function () { world.lockHeld = false; world.events.push('unlock'); },
        };
      },
    },
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(CODE, context, { filename: 'Code.gs' });
  world.postWithKey = function (key, body) {
    const raw = typeof body === 'string' ? body : JSON.stringify(body);
    context.__e = { parameter: { key: key }, postData: { contents: raw } };
    return JSON.parse(vm.runInContext('doPost(__e).getContent()', context));
  };
  world.post = function (body) { return world.postWithKey(KEY, body); };
  world.sheet = function (name) { return world.sheets.filter(function (s) { return s.name === name; })[0]; };
  world.dataRows = function (name) { const s = world.sheet(name); return s ? s.rows.length - 1 : 0; };
  return world;
}

// ---- payload helpers -----------------------------------------------------------------------------------------

function savePayload(extra) {
  return Object.assign({
    action: 'save', time: '2026-11-21T06:00:00Z', version: 'agent', run_id: '1001', alert: true,
    coins: ['BTC'], message: 'BTC moved', sources: ['Kraken'], reasoning: 'why', blocked_reason: '',
  }, extra || {});
}

const MARKET_WIDTH = 24;
function marketRow(runId, coin) {
  const row = new Array(MARKET_WIDTH).fill('');
  row[0] = '2026-11-21T06:00:00Z'; row[1] = runId; row[2] = 'agent'; row[3] = coin; row[5] = 100.5;
  return row;
}
function appendPayload(tab, rows) { return { action: 'append', tab: tab, rows: rows }; }

// ---- cases ---------------------------------------------------------------------------------------------------

const cases = [];
function test(name, fn) { cases.push({ name: name, fn: fn }); }
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) throw new Error(what + ': expected ' + e + ' but got ' + a);
}

// save_ (Agent tab)

test('save: the first save writes a row and sends the alert email', function () {
  const w = makeWorld();
  const r = w.post(savePayload());
  eq(r.success, true, 'success');
  eq(r.duplicate, undefined, 'duplicate flag');
  eq(r.row, 2, 'row number');
  eq(r.emailed, true, 'emailed');
  eq(w.dataRows('Agent'), 1, 'data rows');
  eq(w.mails.length, 1, 'emails sent');
});

test('save: a second save with the same run ID writes nothing and sends no email', function () {
  const w = makeWorld();
  w.post(savePayload());
  const rowsBefore = JSON.stringify(w.sheet('Agent').rows);
  const r = w.post(savePayload({ message: 'BTC moved again' }));
  eq(r, { success: true, duplicate: true }, 'reply');
  eq(w.dataRows('Agent'), 1, 'data rows');
  eq(JSON.stringify(w.sheet('Agent').rows), rowsBefore, 'sheet content');
  eq(w.mails.length, 1, 'emails sent');
});

test('save: a duplicate with alert false also writes nothing', function () {
  const w = makeWorld();
  w.post(savePayload({ alert: false }));
  const r = w.post(savePayload({ alert: false }));
  eq(r, { success: true, duplicate: true }, 'reply');
  eq(w.dataRows('Agent'), 1, 'data rows');
  eq(w.mails.length, 0, 'emails sent');
});

test('save: a different run ID writes a new row and sends its email', function () {
  const w = makeWorld();
  w.post(savePayload({ run_id: '1001' }));
  const r = w.post(savePayload({ run_id: '1002' }));
  eq(r.success, true, 'success');
  eq(r.duplicate, undefined, 'duplicate flag');
  eq(r.row, 3, 'row number');
  eq(w.dataRows('Agent'), 2, 'data rows');
  eq(w.mails.length, 2, 'emails sent');
});

test('save: a payload with no run ID always writes (field missing)', function () {
  const w = makeWorld();
  const payload = savePayload();
  delete payload.run_id;
  w.post(payload);
  const r = w.post(payload);
  eq(r.duplicate, undefined, 'duplicate flag');
  eq(w.dataRows('Agent'), 2, 'data rows');
  eq(w.mails.length, 2, 'emails sent');
  eq(w.lockCalls, 0, 'lock calls (the old path takes no lock)');
});

test('save: an empty run ID or the local default always writes', function () {
  ['', '   ', 'local'].forEach(function (id) {
    const w = makeWorld();
    w.post(savePayload({ run_id: id }));
    const r = w.post(savePayload({ run_id: id }));
    eq(r.duplicate, undefined, 'duplicate flag for ' + JSON.stringify(id));
    eq(w.dataRows('Agent'), 2, 'data rows for ' + JSON.stringify(id));
    eq(w.mails.length, 2, 'emails sent for ' + JSON.stringify(id));
  });
});

test('save: the check looks only in the Run ID column (the same text in another column is not a duplicate)', function () {
  const w = makeWorld();
  w.post(savePayload({ run_id: '1001', message: '2002', version: '2002' }));
  const r = w.post(savePayload({ run_id: '2002' }));
  eq(r.duplicate, undefined, 'duplicate flag');
  eq(w.dataRows('Agent'), 2, 'data rows');
});

test('save: the check reads one column (column 3, Run ID), not the whole sheet', function () {
  const w = makeWorld();
  w.post(savePayload({ run_id: '1001' }));
  w.post(savePayload({ run_id: '1002' }));
  w.events.length = 0;
  w.post(savePayload({ run_id: '1002' }));
  const reads = w.events.filter(function (e) { return e.indexOf('range:Agent:') === 0; });
  eq(reads, ['range:Agent:2,3,2,1'], 'ranges read');
});

test('save: a bad payload is still refused and takes no lock', function () {
  const w = makeWorld();
  const r = w.post(savePayload({ alert: 'yes' }));
  eq(r, { success: false, error: 'INVALID_PAYLOAD' }, 'reply');
  eq(w.lockCalls, 0, 'lock calls');
  eq(w.dataRows('Agent'), 0, 'data rows');
});

test('save: the key check is unchanged (a wrong key writes nothing)', function () {
  const w = makeWorld();
  const r = w.postWithKey('wrong', savePayload());
  eq(r, { success: false, error: 'UNAUTHORIZED' }, 'reply');
  eq(w.sheets.length, 0, 'sheets created');
  eq(w.mails.length, 0, 'emails sent');
});

// the lock

test('save: the lock is taken before the check and released after the write and the email', function () {
  const w = makeWorld();
  w.post(savePayload());
  const order = w.events.filter(function (e) { return ['lock', 'unlock', 'mail', 'flush'].indexOf(e) >= 0 || e.indexOf('read:') === 0 || e.indexOf('write:Agent') === 0; });
  // header write happens inside sheet_() when the tab is new; the check reads the Run ID column only when rows exist
  eq(order[0], 'lock', 'first event');
  eq(order[order.length - 1], 'unlock', 'last event');
  eq(order.indexOf('mail') < order.lastIndexOf('unlock'), true, 'email is sent while the lock is held');
  eq(order.indexOf('flush') < order.lastIndexOf('unlock'), true, 'flush happens before the lock is released');
  eq(w.lockHeld, false, 'lock still held');
});

test('save: the lock is released when the write fails', function () {
  const w = makeWorld();
  w.post(savePayload({ run_id: '1001' }));
  w.failNextSetValues = true;
  const r = w.post(savePayload({ run_id: '1002' }));
  eq(r, { success: false, error: 'INTERNAL_ERROR' }, 'reply');
  eq(w.lockHeld, false, 'lock still held');
  const again = w.post(savePayload({ run_id: '1002' }));
  eq(again.success, true, 'retry succeeds');
  eq(again.duplicate, undefined, 'retry is not a duplicate, nothing was written');
  eq(w.dataRows('Agent'), 2, 'data rows');
});

test('save: two calls with the same run ID at once write one row (the check runs after the lock is won)', function () {
  // Single threaded stand-in: call B asks for the lock, and call A finishes its whole save while B waits.
  // B must then find A's row. A check done before the lock would miss it and write a second row.
  const w = makeWorld();
  w.whileWaiting = function () { w.post(savePayload({ run_id: '3003' })); };
  const r = w.post(savePayload({ run_id: '3003' }));
  eq(r, { success: true, duplicate: true }, 'reply of the call that waited');
  eq(w.dataRows('Agent'), 1, 'data rows');
  eq(w.mails.length, 1, 'emails sent');
});

// append_ (extra tabs)

test('append: the first append for a run writes all its rows', function () {
  const w = makeWorld();
  const r = w.post(appendPayload('market_data', [marketRow('1001', 'BTC'), marketRow('1001', 'ETH')]));
  eq(r, { success: true, added: 2, first_row: 2 }, 'reply');
  eq(w.dataRows('market_data'), 2, 'data rows');
});

test('append: a second append with the same run ID writes nothing', function () {
  const w = makeWorld();
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC'), marketRow('1001', 'ETH')]));
  const before = JSON.stringify(w.sheet('market_data').rows);
  const r = w.post(appendPayload('market_data', [marketRow('1001', 'BTC'), marketRow('1001', 'ETH')]));
  eq(r, { success: true, duplicate: true }, 'reply');
  eq(JSON.stringify(w.sheet('market_data').rows), before, 'sheet content');
});

test('append: the duplicate check survives Sheets turning a numeric run ID into a number', function () {
  const w = makeWorld();
  w.post(appendPayload('market_data', [marketRow('29551234567', 'BTC')]));
  eq(typeof w.sheet('market_data').rows[1][1], 'number', 'the mock stored the run ID as a number, like Sheets does');
  const r = w.post(appendPayload('market_data', [marketRow('29551234567', 'BTC')]));
  eq(r, { success: true, duplicate: true }, 'reply');
  eq(w.dataRows('market_data'), 1, 'data rows');
});

test('append: a different run ID writes', function () {
  const w = makeWorld();
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC')]));
  const r = w.post(appendPayload('market_data', [marketRow('1002', 'BTC')]));
  eq(r, { success: true, added: 1, first_row: 3 }, 'reply');
  eq(w.dataRows('market_data'), 2, 'data rows');
});

test('append: rows with no run ID (empty or local) always write', function () {
  ['', 'local'].forEach(function (id) {
    const w = makeWorld();
    w.post(appendPayload('market_data', [marketRow(id, 'BTC')]));
    const r = w.post(appendPayload('market_data', [marketRow(id, 'BTC')]));
    eq(r.duplicate, undefined, 'duplicate flag for ' + JSON.stringify(id));
    eq(w.dataRows('market_data'), 2, 'data rows for ' + JSON.stringify(id));
    eq(w.lockCalls, 0, 'lock calls for ' + JSON.stringify(id));
  });
});

test('append: the same run ID in a different tab is not a duplicate', function () {
  const w = makeWorld();
  w.post(savePayload({ run_id: '1001' }));
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC')]));
  const cross = new Array(8).fill('');
  cross[1] = '1001';
  const r = w.post(appendPayload('cross_asset', [cross]));
  eq(r.duplicate, undefined, 'duplicate flag');
  eq(w.dataRows('market_data'), 1, 'market_data rows');
  eq(w.dataRows('cross_asset'), 1, 'cross_asset rows');
  eq(w.dataRows('Agent'), 1, 'Agent rows');
});

test('append: the other extra tabs also use the Run ID column (scenarios, context_data)', function () {
  const w = makeWorld();
  [['scenarios', 13], ['context_data', 25]].forEach(function (pair) {
    const row = new Array(pair[1]).fill('');
    row[1] = '1001';
    w.post(appendPayload(pair[0], [row]));
    const r = w.post(appendPayload(pair[0], [row]));
    eq(r, { success: true, duplicate: true }, pair[0] + ' reply');
    eq(w.dataRows(pair[0]), 1, pair[0] + ' rows');
  });
});

test('append: the check reads one column (column 2, Run ID), not the whole sheet', function () {
  const w = makeWorld();
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC'), marketRow('1001', 'ETH')]));
  w.events.length = 0;
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC'), marketRow('1001', 'ETH')]));
  const reads = w.events.filter(function (e) { return e.indexOf('range:market_data:') === 0; });
  eq(reads, ['range:market_data:2,2,2,1'], 'ranges read');
});

test('append: a new tab is created on the first call and the check does not trip on its header row', function () {
  const w = makeWorld();
  const r = w.post(appendPayload('market_data', [marketRow('Run ID', 'BTC')]));
  eq(r.success, true, 'success');
  eq(r.duplicate, undefined, 'duplicate flag');
  eq(w.sheet('market_data').rows[0][1], 'Run ID', 'header row');
  eq(w.dataRows('market_data'), 1, 'data rows');
});

test('append: the lock is taken before the check and released after the write', function () {
  const w = makeWorld();
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC')]));
  const order = w.events.filter(function (e) { return e === 'lock' || e === 'unlock' || e === 'flush' || e.indexOf('write:') === 0; });
  eq(order[0], 'lock', 'first event');
  eq(order[order.length - 1], 'unlock', 'last event');
  eq(order.indexOf('flush') < order.lastIndexOf('unlock'), true, 'flush happens before the lock is released');
  eq(w.lockHeld, false, 'lock still held');
});

test('append: two calls with the same run ID at once write one set of rows', function () {
  const w = makeWorld();
  w.whileWaiting = function () { w.post(appendPayload('market_data', [marketRow('4004', 'BTC')])); };
  const r = w.post(appendPayload('market_data', [marketRow('4004', 'BTC')]));
  eq(r, { success: true, duplicate: true }, 'reply of the call that waited');
  eq(w.dataRows('market_data'), 1, 'data rows');
});

test('append: the lock is released when the write fails, and a retry writes', function () {
  const w = makeWorld();
  w.post(appendPayload('market_data', [marketRow('1001', 'BTC')]));
  w.failNextSetValues = true;
  const r = w.post(appendPayload('market_data', [marketRow('1002', 'BTC')]));
  eq(r, { success: false, error: 'INTERNAL_ERROR' }, 'reply');
  eq(w.lockHeld, false, 'lock still held');
  const again = w.post(appendPayload('market_data', [marketRow('1002', 'BTC')]));
  eq(again.success, true, 'retry succeeds');
  eq(again.duplicate, undefined, 'retry is not a duplicate');
  eq(w.dataRows('market_data'), 2, 'data rows');
});

test('append: a bad payload is still refused and takes no lock', function () {
  const w = makeWorld();
  const r = w.post(appendPayload('market_data', [[1, 2, 3]]));
  eq(r, { success: false, error: 'INVALID_PAYLOAD' }, 'reply');
  eq(w.lockCalls, 0, 'lock calls');
  const bad = w.post(appendPayload('not_a_tab', [marketRow('1001', 'BTC')]));
  eq(bad, { success: false, error: 'INVALID_PAYLOAD' }, 'reply for an unknown tab');
});

// ---- run ------------------------------------------------------------------------------------------------------

const results = cases.map(function (c) {
  try {
    c.fn();
    return { name: c.name, ok: true, detail: '' };
  } catch (err) {
    return { name: c.name, ok: false, detail: String(err && err.message ? err.message : err) };
  }
});
process.stdout.write(JSON.stringify({ results: results }));
process.exit(results.every(function (r) { return r.ok; }) ? 0 : 1);
