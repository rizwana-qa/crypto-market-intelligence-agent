/**
 * Crypto Watch Agent: Google Apps Script web app.
 *
 * Three actions, all sent as a POST with a JSON body to  <web app URL>?key=<KEY>
 *   history   returns the agent's own recent decisions from the Agent tab (the agent's memory)
 *   save      adds one row to the Agent tab and, when alert is true, sends an email
 *   report    emails the executive brief: a short HTML summary in the body and the full HTML page as an attachment
 *
 * Deploy: Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone.
 * After EVERY change: Deploy > Manage deployments > edit > Version: New version > Deploy.
 *
 * Script Properties (Project Settings > Script properties). Nothing secret is stored in this file:
 *   KEY             a long random string (at least 16 characters); the same string goes in the SHEET_URL secret
 *   SPREADSHEET_ID  the id of the Google Sheet (the part of its URL between /d/ and /edit)
 *   EMAIL_TO        optional; defaults to the account that owns this script
 *
 * Every reply is JSON: {success: true, ...} or {success: false, error: "CODE"}. Replies never contain the key.
 * Cells are written as text, so a message that starts with "=" is never run as a formula.
 */

const SHEET_NAME = 'Agent';
const HEADERS = ['Time (UTC)', 'Version', 'Run ID', 'Alert', 'Coins', 'Message', 'Sources', 'Reasoning', 'Blocked reason'];
const MAX_BODY_CHARS = 20000;
const MAX_REPORT_CHARS = 600000;   // the executive brief page, sent only with action "report"
const MAX_SUMMARY_CHARS = 30000;
const MAX_TEXT_CHARS = 1000;
const MAX_HISTORY_ROWS = 20;
const MIN_KEY_LENGTH = 16;

function doGet() {
  return reply_({ success: false, error: 'POST_REQUIRED' });
}

function doPost(e) {
  try {
    const props = PropertiesService.getScriptProperties();
    const key = String(props.getProperty('KEY') || '').trim();
    const supplied = e && e.parameter ? String(e.parameter.key || '') : '';
    if (key.length < MIN_KEY_LENGTH || supplied !== key) return reply_({ success: false, error: 'UNAUTHORIZED' });

    const contents = e.postData ? e.postData.contents : '';
    if (typeof contents !== 'string' || contents.length === 0 || contents.length > MAX_REPORT_CHARS) return reply_({ success: false, error: 'INVALID_REQUEST' });
    let request;
    try {
      request = JSON.parse(contents);
    } catch (ignored) {
      return reply_({ success: false, error: 'INVALID_REQUEST' });
    }
    if (request === null || typeof request !== 'object' || Array.isArray(request)) return reply_({ success: false, error: 'INVALID_REQUEST' });
    if (request.action !== 'report' && contents.length > MAX_BODY_CHARS) return reply_({ success: false, error: 'INVALID_REQUEST' });

    if (request.action === 'history') return reply_(history_(request, props));
    if (request.action === 'save') return reply_(save_(request, props));
    if (request.action === 'report') return reply_(report_(request, props));
    return reply_({ success: false, error: 'INVALID_ACTION' });
  } catch (err) {
    console.error('doPost failed: ' + (err && err.name ? err.name : 'Error'));   // never log the request or the key
    return reply_({ success: false, error: 'INTERNAL_ERROR' });
  }
}

function reply_(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}

function clean_(value) {
  return String(value === null || value === undefined ? '' : value).slice(0, MAX_TEXT_CHARS);
}

function sheet_(props) {
  const id = String(props.getProperty('SPREADSHEET_ID') || '').trim();
  const spreadsheet = SpreadsheetApp.openById(id);
  // The tab name is matched without regard to letter case, so "agent" and "Agent" are the same tab.
  const wanted = SHEET_NAME.toLowerCase();
  let sheet = spreadsheet.getSheets().filter(function (s) { return s.getName().trim().toLowerCase() === wanted; })[0];
  if (!sheet) sheet = spreadsheet.insertSheet(SHEET_NAME);
  if (sheet.getLastRow() === 0) sheet.appendRow(HEADERS);
  return sheet;
}

// The agent's memory: the last `limit` rows, oldest first.
function history_(request, props) {
  const limit = Math.min(Math.max(Number(request.limit) || 5, 1), MAX_HISTORY_ROWS);
  const sheet = sheet_(props);
  const last = sheet.getLastRow();
  if (last < 2) return { success: true, decisions: [] };
  const first = Math.max(2, last - limit + 1);
  const rows = sheet.getRange(first, 1, last - first + 1, HEADERS.length).getValues();
  const decisions = rows.map(function (row) {
    const decision = {
      time: String(row[0]),
      alert: String(row[3]).toUpperCase() === 'TRUE',
      coins: String(row[4]).split(',').map(function (s) { return s.trim(); }).filter(Boolean),
      message: String(row[5]),
      sources: String(row[6]).split('|').map(function (s) { return s.trim(); }).filter(Boolean),
    };
    if (request.detail === true) {   // only the executive brief asks for these; the agent's memory stays small
      decision.version = String(row[1]);
      decision.run_id = String(row[2]);
      decision.reasoning = String(row[7]);
      decision.blocked_reason = String(row[8]);
    }
    return decision;
  });
  return { success: true, decisions: decisions };
}

// One row per decision, and an email when there is an alert.
function save_(request, props) {
  if (typeof request.alert !== 'boolean' || !Array.isArray(request.coins) || !Array.isArray(request.sources)) {
    return { success: false, error: 'INVALID_PAYLOAD' };
  }
  const row = [
    clean_(request.time), clean_(request.version), clean_(request.run_id), request.alert ? 'TRUE' : 'FALSE',
    request.coins.map(clean_).join(', '), clean_(request.message), request.sources.map(clean_).join(' | '),
    clean_(request.reasoning), clean_(request.blocked_reason),
  ];
  const sheet = sheet_(props);
  const rowNumber = sheet.getLastRow() + 1;
  sheet.getRange(rowNumber, 1, 1, HEADERS.length).setNumberFormat('@').setValues([row]);
  SpreadsheetApp.flush();

  let emailed = false;
  if (request.alert) {
    const to = String(props.getProperty('EMAIL_TO') || '').trim() || Session.getEffectiveUser().getEmail();
    MailApp.sendEmail({
      to: to,
      subject: 'Crypto watch alert: ' + row[4],
      body: row[5] + '\n\nCoins: ' + row[4] + '\nSources: ' + row[6] + '\nRun: ' + row[2] + ' (' + row[1] + ')\n\n' +
            'This project is for education only and is not financial advice.',
      name: 'Crypto Watch Agent',
    });
    emailed = true;
  }
  return { success: true, row: rowNumber, emailed: emailed };
}

// The executive brief: a short HTML summary in the email body and the full page as an attachment.
function report_(request, props) {
  const page = request.html;
  const summary = request.summary_html;
  if (typeof page !== 'string' || page.length === 0 || page.length > MAX_REPORT_CHARS ||
      typeof summary !== 'string' || summary.length === 0 || summary.length > MAX_SUMMARY_CHARS) {
    return { success: false, error: 'INVALID_PAYLOAD' };
  }
  const to = String(props.getProperty('EMAIL_TO') || '').trim() || Session.getEffectiveUser().getEmail();
  MailApp.sendEmail({
    to: to,
    subject: clean_(request.subject) || 'Crypto watch executive brief',
    body: 'Open this email in an HTML-capable mail program, and open the attached HTML file in a browser for the charts. ' +
          'This project is for education only and is not financial advice.',
    htmlBody: summary,
    attachments: [Utilities.newBlob(page, 'text/html', 'crypto-watch-executive-brief.html')],
    name: 'Crypto Watch Agent',
  });
  return { success: true, emailed: true };
}

// Run this ONCE from the editor (Run > authorizeOnce) to grant the Sheet and email permissions.
function authorizeOnce() {
  const props = PropertiesService.getScriptProperties();
  sheet_(props);
  MailApp.getRemainingDailyQuota();
  console.log('Authorized. Sheet reachable: ' + SHEET_NAME);
}
