import {t, locale, onLanguageChange, englishError} from './i18n.js';
import {selectMessages, score, emojiLabel, collectHashtags, extractHashtags, normalizeHashtag} from './logic.js';
import {prepareCore, isPortal, localFetch, showCorePanel} from './core.js';
import {setupDiagnostics} from './diagnostics.js';

const $ = id => document.getElementById(id);
let token = document.querySelector('meta[name="app-token"]').content;
const apiOrigin = isPortal ? document.querySelector('meta[name="core-origin"]').content : '';
setupDiagnostics(apiOrigin, () => token);
const localManagedCore = !isPortal && document.querySelector('meta[name="managed-core"]')?.content === 'true';
const fmt = {format: value => new Intl.NumberFormat(locale()).format(value)};
let channels = [], messages = [], page = 1, scannedChannel = '';
let polling = null, running = false, busy = false;
let currentScanId = null;
let blockedMessages = [], viewingBlocked = false, blockingSupported = false, resultsChannelId = '';
let speedSupported = false, appliedInterval = 1;
let storageSupported = false, stage = 'disconnected', savedChannels = [], storagePrefs = {save_messages:false, retain_login:false};
let restorePolling = null;
let lastScan = null, lastState = null;
const channelCounts = new Map();
const pageSize = 30;

async function api(path, data) {
  let response;
  try {response = await localFetch(`${apiOrigin}/api/${path}`, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'X-App-Token': token, ...(data === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(data === undefined ? {} : {body: JSON.stringify(data)}),
    cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(55000),
  });} catch (error) {
    if (isPortal) {
      clearTimeout(polling); running = false;
      showCorePanel("The core connection was interrupted. Click “Start core” to reconnect, or check again.");
    }
    throw new Error("The website cannot reach the local core API. Open Connection diagnostics, or visit http://127.0.0.1:8765 directly.");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(englishError(result.error));
  return result;
}

function notice(message, info = false) {
  $('notice').textContent = message;
  $('notice').classList.toggle('info', info);
  $('notice').hidden = !message;
}

function controls() {
  document.querySelectorAll('#loginPanel input, #loginPanel select, #loginPanel button').forEach(el => {el.disabled = busy;});
  $('disconnect').disabled = busy;
  $('stopCore').disabled = busy;
  $('testProxy').disabled = busy;
  $('saveMessages').disabled = $('retainLogin').disabled = busy || running || stage === 'restoring' || !storageSupported;
  $('refreshMessages').disabled = busy || running || stage !== 'ready' || !messages.length || !storageSupported;
  $('savedChannel').disabled = busy || running || !savedChannels.length;
  $('openSaved').disabled = busy || running || !$('savedChannel').value;
  $('retryLogin').disabled = busy || stage === 'restoring';
  for (const id of ['fetch', 'queryCount', 'reloadChannels', 'channelSearch', 'channelSelect', 'limit', 'startDate', 'endDate']) {
    $(id).disabled = busy || running || (['fetch', 'queryCount'].includes(id) && !$('channelSelect').value);
  }
  $('cancel').hidden = !running;
  $('fetch').hidden = running;
  $('toggleBlocked').disabled = busy || running;
  $('scanSpeed').disabled = busy || !speedSupported;
  document.querySelectorAll('.block-message').forEach(button => {button.disabled = busy || running || !blockingSupported;});
}

async function action(task, propagate = false) {
  if (busy) return;
  busy = true; controls(); notice('');
  try {return await task();} catch (error) {notice(error.message); if (propagate) throw error;} finally {busy = false; controls();}
}

function setStage(value) {
  stage = value;
  const ready = stage === 'ready';
  $('loginPanel').hidden = ready;
  $('channelPanel').hidden = !ready;
  $('credentialsForm').hidden = stage !== 'disconnected';
  $('phoneForm').hidden = stage !== 'phone';
  $('codeForm').hidden = stage !== 'code';
  $('passwordForm').hidden = stage !== 'password';
  $('disconnect').hidden = stage === 'disconnected';
  $('statusDot').classList.toggle('connected', ready);
  $('connectionStatus').textContent = ready ? t("Telegram signed in · Local session") : stage === 'disconnected' ? t("Telegram disconnected") : stage === 'restoring' ? t("Restoring saved login…") : t("Telegram connected · Sign-in required");
  const input = {phone: 'phone', code: 'code', password: 'password'}[stage];
  if (input) requestAnimationFrame(() => $(input).focus());
  controls();
}

function clearSensitive() {
  for (const id of ['apiId', 'apiHash', 'phone', 'code', 'password']) $(id).value = '';
}

$('credentialsForm').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const result = await api('connect', {
      api_id: $('apiId').value.trim(), api_hash: $('apiHash').value.trim(),
      proxy_enabled: $('proxyEnabled').checked, proxy_type: $('proxyType').value,
      proxy_host: $('proxyHost').value.trim(), proxy_port: $('proxyPort').value,
    });
    $('apiId').value = $('apiHash').value = '';
    setStage(result.stage);
  });
});
$('testProxy').addEventListener('click', () => action(async () => {
  $('debugStatus').textContent = t("Testing the proxy protocol and Telegram TCP tunnel…");
  try {
    const result = await api('test-proxy', {proxy_enabled:$('proxyEnabled').checked,
      proxy_type:$('proxyType').value, proxy_host:$('proxyHost').value.trim(), proxy_port:$('proxyPort').value});
    $('debugStatus').textContent = t(result.message);
  } catch (error) {
    $('debugStatus').textContent = error.message;
    throw error;
  }
}));
$('stopCore').addEventListener('click', () => action(async () => {
  const result = await api('shutdown', {});
  clearTimeout(polling); running = false;
  channels = []; channelCounts.clear(); scannedChannel = ''; currentScanId = null;
  blockedMessages = []; blockingSupported = false;
  clearSensitive(); clearResults(); token = '';
  $('channelSearch').value = ''; $('channelSelect').replaceChildren();
  $('resultsTitle').textContent = t("Message explorer"); $('loadedBadge').textContent = t("Waiting for messages");
  $('resultSummary').textContent = t("Text and hashtag filters search fetched messages in the current sort order.");
  showCorePanel(result.warning || t("The core has stopped. Opted-in messages and login remain on this computer. Click “Start core” to restore them."));
  if (localManagedCore) {
    $('startCore').hidden = false;
    $('retryCore').hidden = true; $('openLocalCore').hidden = true; $('coreSteps').hidden = true;
    $('corePlatformNote').textContent = t("Click “Start core” and allow the system launcher to open. Refresh this page after it starts.");
  }
}));
$('phoneForm').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => setStage((await api('send-code', {phone: $('phone').value})).stage));
});
$('resendCode').addEventListener('click', () => action(async () => {
  const result = await api('send-code', {phone: $('phone').value});
  $('code').value = ''; setStage(result.stage);
  notice(t("A new code was requested. Check your official Telegram app."), true);
}));
for (const [form, field] of [['codeForm', 'code'], ['passwordForm', 'password']]) {
  $(form).addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      const value = $(field).value; $(field).value = '';
      const result = await api('sign-in', {[field]: value});
      setStage(result.stage);
      if (result.stage === 'ready') {clearSensitive(); await updateState(await api('status')); await loadChannels(); await restoreResults();}
    });
  });
}

function renderChannels() {
  const query = $('channelSearch').value.trim().toLocaleLowerCase();
  const selected = $('channelSelect').value;
  const visible = channels.filter(c => `${c.title} ${c.username}`.toLocaleLowerCase().includes(query));
  $('channelSelect').replaceChildren();
  for (const channel of visible) {
    const option = document.createElement('option');
    option.value = channel.id;
    option.textContent = channel.title;
    option.title = channel.username ? `${channel.title} · @${channel.username}` : channel.title;
    $('channelSelect').append(option);
  }
  if (visible.some(c => c.id === selected)) $('channelSelect').value = selected;
  else if (visible.length) $('channelSelect').value = visible[0].id;
  $('channelCount').textContent = t("Joined {0} channels{1}", [fmt.format(channels.length), query ? t(" · {0} matches", [visible.length]) : '']);
  if (!channels.length) $('channelCount').textContent = t("This account has no joined broadcast channels.");
  renderChannelCount();
  controls();
}

function renderChannelCount() {
  const count = channelCounts.get($('channelSelect').value);
  $('channelTotal').textContent = count ? t("Total messages: {0}{1} (history visible to this account)", [count.inexact ? t("about ") : '', fmt.format(count.count)]) : t("Count channel messages first, without fetching the entire history.");
  $('queryCount').textContent = count ? t("Refresh message count") : t("Count messages");
}
$('queryCount').addEventListener('click', () => action(async () => {
  const key = $('channelSelect').value;
  $('channelTotal').textContent = t("Counting channel messages…");
  try {
    const count = await api('channel-count', {channel_id:key});
    channelCounts.set(count.channel_id, count); renderChannelCount();
  } catch (error) {
    renderChannelCount(); throw error;
  }
}));

async function loadChannels() {
  $('channelCount').textContent = t("Loading channels…");
  try {channels = (await api('channels')).channels; renderChannels();}
  catch (error) {$('channelCount').textContent = "Channels could not be loaded. Use the refresh button to try again."; throw error;}
}
$('channelSearch').addEventListener('input', renderChannels);
$('channelSelect').addEventListener('change', () => {renderChannelCount(); controls();});
$('reloadChannels').addEventListener('click', () => action(loadChannels));

function dateBoundary(value, after = false) {
  if (!value) return null;
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day + (after ? 1 : 0)).toISOString();
}

function clearResults() {
  lastScan = null;
  messages = []; page = 1;
  resultsChannelId = '';
  $('scanReport').hidden = true; $('snapshotStatus').textContent = '';
  viewingBlocked = false;
  $('resultToolbar').hidden = false; $('blockedHelp').hidden = true;
  $('toggleBlocked').textContent = t("Blocked box ({0})", [fmt.format(blockedMessages.length)]);
  $('toggleBlocked').setAttribute('aria-pressed', 'false');
  $('messageSearch').value = '';
  $('emojiSelect').value = '';
  $('hideZero').checked = false;
  $('sortSelect').value = 'reactions';
  $('hashtagSelect').replaceChildren(new Option(t("All hashtags"), ''));
  for (const id of ['messageSearch', 'emojiSelect', 'sortSelect', 'hashtagSelect', 'hideZero']) $(id).disabled = true;
  $('messageList').replaceChildren(); $('pagination').hidden = true;
  $('emptyState').hidden = false;
  $('emptyState').querySelector('h3').textContent = t("Good reads are waiting");
  $('emptyState').querySelector('p').textContent = t("Connect your account and choose a channel. Fetched messages will appear here, sorted by reactions.");
}

$('fetch').addEventListener('click', () => action(async () => {
  const channel = channels.find(c => c.id === $('channelSelect').value);
  if (!channel) throw new Error("Please select a channel first.");
  const result = await api('scan', {
    channel_id: channel.id, limit: Number($('limit').value),
    ...(speedSupported ? {request_interval:Number($('scanSpeed').value)} : {}),
    start: dateBoundary($('startDate').value), end: dateBoundary($('endDate').value, true),
  });
  currentScanId = result.scan_id;
  clearTimeout(polling);
  clearResults();
  scannedChannel = channel.title;
  $('resultsTitle').dataset.userContent = '';
  $('resultsTitle').textContent = channel.title;
  $('resultSummary').textContent = t("Fetching messages. Sort and search when fetching finishes or stops.");
  running = true; controls();
  showProgress(result); schedulePoll();
}));

function showProgress(scan) {
  lastScan = scan;
  showReport(scan);
  if (typeof scan.request_interval === 'number') {
    appliedInterval = scan.request_interval;
    if (!busy) $('scanSpeed').value = String(appliedInterval);
  }
  const speed = typeof scan.request_interval === 'number' ? t(" · Batch interval {0}s", [scan.request_interval]) : '';
  $('scanProgress').textContent = t("{0} {1} records · {2} messages fetched{3}", [scan.status === 'running' ? t("Reading") : t("Read"), fmt.format(scan.checked), fmt.format(scan.count), speed]);
  $('loadedBadge').textContent = scan.status === 'running' ? t("Fetching · {0} messages", [fmt.format(scan.count)]) : t("Fetched {0} messages", [fmt.format(scan.count)]);
}

$('scanSpeed').addEventListener('change', () => {
  if (!running) return;
  const scanId = currentScanId, interval = Number($('scanSpeed').value);
  action(async () => {
    try {
      const scan = await api('scan-speed', {scan_id:scanId, request_interval:interval});
      if (scanId !== currentScanId) return;
      showProgress(scan);
      notice(t("Batch interval changed to {0}s for subsequent requests. The current wait will finish normally.", [interval]), true);
    } catch (error) {
      $('scanSpeed').value = String(appliedInterval);
      throw error;
    }
  });
});

function schedulePoll() {clearTimeout(polling); polling = setTimeout(poll, 1000);}
let pollFailures = 0;
async function poll() {
  const scanId = currentScanId;
  try {
    const scan = await api('scan');
    if (scanId !== currentScanId) return;
    pollFailures = 0; showProgress(scan);
    if (scan.status === 'running') {schedulePoll(); return;}
    await finish(scan);
  } catch (error) {
    if (scanId !== currentScanId) return;
    if (++pollFailures < 3) {schedulePoll(); return;}
    notice(`${error.message} Refresh the page to recover the task status.`);
    $('cancel').disabled = false;
  }
}

async function finish(scan) {
  if (scan.scan_id !== currentScanId) return;
  clearTimeout(polling);
  const result = await api('results');
  if (scan.scan_id !== currentScanId || result.scan.scan_id !== currentScanId) return;
  messages = result.messages.map(message => ({...message, hashtags:extractHashtags(message)})); running = false; controls();
  blockingSupported = Array.isArray(result.blocked_messages);
  blockedMessages = result.blocked_messages || [];
  resultsChannelId = result.scan.channel_id || '';
  showProgress(scan); populateEmojis(); populateHashtags();
  for (const id of ['messageSearch', 'emojiSelect', 'sortSelect', 'hashtagSelect', 'hideZero']) $(id).disabled = false;
  if (storageSupported) await updateState(await api('status'));
  if (scan.status === 'error') notice(`${englishError(scan.error)} Kept ${fmt.format(messages.length)} successfully fetched messages.`);
  else if (scan.status === 'cancelled') notice(t("Fetching stopped. Kept {0} messages.", [fmt.format(messages.length)]), true);
  render();
  controls();
}

$('cancel').addEventListener('click', async () => {
  $('cancel').disabled = true;
  try {await finish(await api('cancel', {}));} catch (error) {notice(error.message);}
  finally {$('cancel').disabled = false;}
});

$('disconnect').addEventListener('click', () => action(async () => {
  clearTimeout(polling);
  let error;
  try {await api('disconnect', {});} catch (exc) {error = exc;}
  const state = await api('status');
  if (state.stage === 'disconnected') {
    currentScanId = null;
    blockedMessages = []; blockingSupported = false;
    running = false; channels = []; channelCounts.clear(); clearSensitive(); clearResults();
    $('channelSearch').value = ''; $('channelSelect').replaceChildren();
    $('resultsTitle').textContent = t("Message explorer"); $('loadedBadge').textContent = t("Waiting for messages");
    $('resultSummary').textContent = t("Text and hashtag filters search fetched messages in the current sort order.");
    await updateState(state);
  }
  if (error) throw error;
  notice(t("Account disconnected. Saved login, messages and blocked records were cleared from this computer."), true);
}));

function populateEmojis() {
  const keys = new Set(['❤', '👍', '🔥']);
  for (const message of messages) for (const key of Object.keys(message.reactions)) keys.add(key);
  $('emojiSelect').replaceChildren();
  const all = document.createElement('option'); all.value = ''; all.textContent = t("All reactions"); $('emojiSelect').append(all);
  for (const key of keys) {
    const option = document.createElement('option'); option.value = key; option.textContent = emojiLabel(key);
    $('emojiSelect').append(option);
  }
}

function populateHashtags() {
  const selected = $('hashtagSelect').value;
  const selectedLabel = $('hashtagSelect').selectedOptions[0]?.textContent.split(' · ')[0];
  const tags = collectHashtags(messages);
  $('hashtagSelect').replaceChildren(new Option(tags.length ? t("All hashtags") : t("No hashtags"), ''));
  for (const tag of tags) $('hashtagSelect').append(new Option(t("{0} · {1} messages", [tag.label, fmt.format(tag.count)]), tag.key));
  if (tags.some(tag => tag.key === selected)) $('hashtagSelect').value = selected;
  else if (selected) {
    // Keep an active filter even when its final matching message has been blocked.
    $('hashtagSelect').append(new Option(t("{0} · 0 messages", [selectedLabel]), selected));
    $('hashtagSelect').value = selected;
  }
}

$('toggleBlocked').addEventListener('click', () => {
  viewingBlocked = !viewingBlocked; page = 1; render();
});

function changeBlock(message, blocked) {
  return action(async () => {
    const channelId = viewingBlocked ? message.channel_id : resultsChannelId;
    const result = await api('message-block', {channel_id:channelId, message_id:message.id, blocked});
    blockedMessages = blockedMessages.filter(row => row.channel_id !== result.channel_id || row.id !== result.message_id);
    if (result.message) blockedMessages.push(result.message);
    if (result.channel_id === resultsChannelId) {
      messages = messages.map(row => row.id === result.message_id ? {...row, blocked:result.blocked} : row);
    }
    populateHashtags(); render();
  });
}


function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function card(message, rank, emoji) {
  const article = node('article', 'message-card');
  article.dataset.messageId = message.id;
  article.dataset.channelId = message.channel_id || resultsChannelId;
  const top = node('div', 'card-top');
  const time = node('time', '', new Date(message.date).toLocaleString(locale(), {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}));
  time.dateTime = message.date;
  const link = node('a', '', t("Original ↗"));
  // Only links formed by this local server are accepted. Never render message HTML.
  if (/^https:\/\/t\.me\/(?:[A-Za-z0-9_]+|c\/\d+)\/\d+$/.test(message.link)) link.href = message.link;
  link.target = '_blank'; link.rel = 'noopener noreferrer';
  top.append(node('span', 'rank', fmt.format(rank)), time, link); article.append(top);
  if (message.unavailable) article.append(node('p', 'field-help', t("Telegram returned this message as unavailable. It is excluded from normal search.")));
  if (message.checked_at) article.append(node('p', 'field-help', t("Last checked: {0}", [new Date(message.checked_at).toLocaleString(locale())])));
  if (message.previous_text !== undefined) {
    const details = node('details', 'previous-message');
    details.append(node('summary', '', t("View previously saved text")), node('p', 'message-text expanded', message.previous_text));
    article.append(details);
  }
  if (viewingBlocked) article.append(node('p', 'field-help', t("From: {0}", [message.channel_title])));
  if (message.text) {
    const text = node('p', 'message-text', message.text); article.append(text);
    if (message.text.length > 220 || message.text.split('\n').length > 5) {
      const button = node('button', 'expand-button', t("Expand ↓"));
      button.addEventListener('click', () => {const expanded = text.classList.toggle('expanded'); button.textContent = expanded ? t("Collapse ↑") : t("Expand ↓");});
      article.append(button);
    }
  } else article.append(node('p', 'media-note', message.media ? t("This message contains media. Open the original to view it.") : t("This message has no text.")));
  const hashtags = extractHashtags(message);
  if (hashtags.length && !viewingBlocked) {
    const tags = node('div', 'hashtag-chips');
    for (const tag of hashtags) {
      const button = node('button', `hashtag-chip${normalizeHashtag(tag) === $('hashtagSelect').value ? ' selected' : ''}`, tag);
      button.title = t("Filter by {0} in the current sort order", [tag]);
      button.addEventListener('click', () => {$('hashtagSelect').value = normalizeHashtag(tag); page = 1; render();});
      tags.append(button);
    }
    article.append(tags);
  }
  const bottom = node('div', 'card-bottom'), chips = node('div', 'reaction-chips');
  const reactions = Object.entries(message.reactions).sort((a,b) => b[1] - a[1]);
  for (const [key, count] of reactions) {
    chips.append(node('span', `reaction-chip${key === emoji ? ' selected' : ''}`, `${emojiLabel(key)} ${fmt.format(count)}`));
  }
  if (!reactions.length) chips.append(node('span', 'reaction-chip', t("No reactions")));
  const total = node('div', 'score-label'); total.append(node('strong', '', fmt.format(score(message, emoji))), node('span', '', emoji ? emojiLabel(emoji) : t("Total reactions")));
  bottom.append(chips, total); article.append(bottom);
  const actions = node('div', 'message-actions');
  const blockButton = node('button', 'secondary block-message', viewingBlocked ? t("Restore message") : t("Move to blocked box"));
  blockButton.disabled = busy || running || !blockingSupported;
  blockButton.title = blockingSupported ? (viewingBlocked ? t("Restoring includes this message in the list and search again") : t("Blocked messages are excluded from the list and search")) : t("The blocked box requires core 1.3.0. Download and install an updated core.");
  blockButton.addEventListener('click', () => changeBlock(message, !viewingBlocked));
  actions.append(blockButton); article.append(actions); return article;
}

function render() {
  const emoji = viewingBlocked ? '' : $('emojiSelect').value;
  const filtered = viewingBlocked ? [...blockedMessages].reverse() : selectMessages(messages, {query: $('messageSearch').value, emoji,
    sort: $('sortSelect').value, hashtag: $('hashtagSelect').value, hideZero: $('hideZero').checked});
  const blockedCount = messages.filter(message => message.blocked).length;
  $('resultToolbar').hidden = viewingBlocked;
  $('blockedHelp').hidden = !viewingBlocked;
  $('toggleBlocked').textContent = viewingBlocked ? t("Back to messages") : t("Blocked box ({0})", [fmt.format(blockedMessages.length)]);
  $('toggleBlocked').setAttribute('aria-pressed', String(viewingBlocked));
  $('resultsTitle').toggleAttribute('data-user-content', !viewingBlocked && Boolean(scannedChannel));
  $('resultsTitle').textContent = viewingBlocked ? t("Blocked box") : (scannedChannel || t("Message explorer"));
  $('loadedBadge').textContent = viewingBlocked ? t("Blocked {0} messages", [fmt.format(blockedMessages.length)]) : messages.length ? t("Fetched {0} messages", [fmt.format(messages.length)]) : t("Waiting for messages");
  $('resultsArea').setAttribute('aria-label', viewingBlocked ? t("Blocked box") : t("Message explorer"));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize)); page = Math.min(page, pages);
  const start = (page - 1) * pageSize;
  $('messageList').replaceChildren(...filtered.slice(start, start + pageSize).map((message, i) => card(message, start + i + 1, emoji)));
  $('emptyState').hidden = filtered.length > 0;
  if (!filtered.length) {
    $('emptyState').querySelector('h3').textContent = viewingBlocked ? t("The blocked box is empty") : messages.length ? t("No matching messages") : t("No messages to display yet");
    $('emptyState').querySelector('p').textContent = viewingBlocked ? t("Click “Move to blocked box” on a message to exclude it from search.") : messages.length ? t("Try another keyword or hashtag, or restore a message from the blocked box.") : t("Try a different date range or fetch more messages.");
  }
  const tagLabel = $('hashtagSelect').value ? t(" · Hashtag {0}", [$('hashtagSelect').selectedOptions[0].textContent.split(' · ')[0]]) : '';
  $('resultSummary').textContent = viewingBlocked ? t("{0} blocked messages across saved and previously fetched channels, most recently blocked first. {1}", [fmt.format(blockedMessages.length), messages.length && !blockingSupported ? t("Download and install core 1.3.0 or later to use the blocked box.") : '']) : t("{0} · {1} messages in snapshot ({2} unavailable), {3} blocked, {4} matches{5}. Blocked messages are excluded from search. Paid Stars do not affect sorting.", [scannedChannel, fmt.format(messages.length), fmt.format(messages.filter(row => row.unavailable).length), fmt.format(blockedCount), fmt.format(filtered.length), tagLabel]);
  $('pagination').hidden = filtered.length <= pageSize;
  $('pageInfo').textContent = `${page} / ${pages}`;
  $('previous').disabled = page === 1; $('next').disabled = page === pages;
}

let searchTimer;
$('messageSearch').addEventListener('input', () => {clearTimeout(searchTimer); searchTimer = setTimeout(() => {page = 1; render();}, 150);});
for (const id of ['emojiSelect', 'sortSelect', 'hashtagSelect', 'hideZero']) $(id).addEventListener('change', () => {page = 1; render();});
for (const [id, step] of [['previous', -1], ['next', 1]]) $(id).addEventListener('click', () => {page += step; render(); $('resultsTitle').scrollIntoView({behavior:'smooth', block:'start'});});

function showReport(scan) {
  const report = $('scanReport');
  if (scan.status === 'running' || !scan.stop_reason) {report.hidden = true; return;}
  const stop = {limit_reached:t("Reading limit reached"), history_exhausted:t("Readable history ended"), date_boundary:t("Start date reached"),
    cancelled:t("Stopped manually"), error:"An error occurred; partial results kept", refresh_complete:t("Update check complete")}[scan.stop_reason] || scan.stop_reason;
  if (scan.kind === 'refresh') {
    const c = scan.changes || {};
    report.textContent = t("{0} · Checked {1} messages: {2} text/hashtag changes, {3} reaction changes, {4} other changes, {5} deleted or unavailable, {6} unconfirmed (previous snapshots kept). Unchecked messages are also kept if the task stops or fails.", [stop, fmt.format(scan.checked), c.text || 0, c.reactions || 0, c.other || 0, c.unavailable || 0, c.unconfirmed || 0]);
  } else {
    const skipped = scan.skipped || {};
    const names = {service:t("Channel service records"), outside_dates:t("Outside the date range"), unsupported:t("Unsupported record types"), duplicate:t("Duplicate messages")};
    const reasons = Object.entries(skipped).filter(([,count]) => count > 0).map(([key,count]) => t("{0}: {1}", [names[key] || key, count]));
    report.textContent = t("{0} · Requested {1}, read {2} records, {3} displayable messages. {4}Records not returned by Telegram cannot be explained. Gaps in message IDs do not indicate lost messages in this fetch.", [stop, scan.limit ? t("up to {0} records", [fmt.format(scan.limit)]) : t("all history"), fmt.format(scan.checked), fmt.format(scan.count), reasons.length ? t("Skipped: {0}. ", [reasons.join('；')]) : t("No skipped records were observed. ")]);
  }
  report.hidden = false;
  $('snapshotStatus').textContent = `${scan.restored ? t("Local snapshot restored · ") : ''}${scan.checked_at ? t("Checked on ") + new Date(scan.checked_at).toLocaleString(locale()) : scan.fetched_at ? t("Fetched on ") + new Date(scan.fetched_at).toLocaleString(locale()) : ''}`;
}

async function updateState(state) {
  lastState = state;
  setStage(state.stage);
  storagePrefs = {save_messages:state.save_messages === true, retain_login:state.retain_login === true};
  $('saveMessages').checked = storagePrefs.save_messages;
  $('retainLogin').checked = storagePrefs.retain_login;
  savedChannels = state.cached_channels || [];
  $('disconnect').hidden = state.stage === 'disconnected' && !state.retain_login && !savedChannels.length;
  const selected = $('savedChannel').value;
  $('savedChannel').replaceChildren(new Option(savedChannels.length ? t("Choose a saved channel") : t("No saved messages"), ''));
  for (const c of savedChannels) $('savedChannel').append(new Option(t("{0} · {1} messages", [c.title, fmt.format(c.count)]), c.id));
  if (savedChannels.some(c => c.id === selected)) $('savedChannel').value = selected;
  else if (savedChannels.length) $('savedChannel').value = savedChannels[0].id;
  $('storageStatus').textContent = !storageSupported ? t("This feature requires core 1.5.0. Download and install an updated core.") : (state.persistence_error ? englishError(state.persistence_error) : '') ||
    (state.stage === 'restoring' ? t("Restoring login. Saved messages are still available offline.") : t("Data is saved only by the local core, never in browser storage or on the website server."));
  $('retryLogin').hidden = !storageSupported || !state.retain_login || !['disconnected','restoring'].includes(state.stage);
  controls();
}

for (const id of ['saveMessages', 'retainLogin']) $(id).addEventListener('change', () => action(async () => {
  try {
    const state = await api('storage', {save_messages:$('saveMessages').checked, retain_login:$('retainLogin').checked});
    await updateState(state);
    notice(id === 'saveMessages' ? (state.save_messages ? t("Message saving enabled. Snapshots and blocked records are saved after fetching, stopping or checking updates.") : t("Saved messages were deleted from disk. The current in-memory list remains available.")) : (state.retain_login ? t("Login retention enabled. Successful login is saved securely and restored on the next start.") : t("Saved login deleted. The current session remains active.")), true);
  } catch (error) {
    $('saveMessages').checked = storagePrefs.save_messages;
    $('retainLogin').checked = storagePrefs.retain_login;
    throw error;
  }
}));
$('savedChannel').addEventListener('change', controls);
$('openSaved').addEventListener('click', () => action(async () => {
  const result = await api('cache-open', {channel_id:$('savedChannel').value});
  scannedChannel = result.scan.channel_title || t("Channel"); currentScanId = result.scan.scan_id;
  if (channels.some(c => c.id === result.scan.channel_id)) {$('channelSelect').value = result.scan.channel_id; renderChannelCount();}
  await finish(result.scan);
}));
$('refreshMessages').addEventListener('click', () => action(async () => {
  const scan = await api('refresh-messages', {scan_id:currentScanId, channel_id:resultsChannelId, request_interval:Number($('scanSpeed').value)});
  currentScanId = scan.scan_id; running = true; showProgress(scan); schedulePoll();
}));

async function restoreResults() {
  const scan = await api('scan');
  if (scan.status === 'idle') return;
  if (typeof scan.request_interval === 'number') $('scanSpeed').value = String(scan.request_interval);
  currentScanId = scan.scan_id;
  scannedChannel = scan.channel_title || channels.find(c => c.id === scan.channel_id)?.title || t("Channel");
  if (scan.status === 'running') {running = true; showProgress(scan); schedulePoll();}
  else await finish(scan);
}

function scheduleRestore() {
  clearTimeout(restorePolling);
  restorePolling = setTimeout(async () => {
    try {
      const state = await api('status'); await updateState(state);
      if (state.stage === 'restoring') {scheduleRestore(); return;}
      if (state.stage === 'ready') {
        await loadChannels();
        if (state.cached_channels?.length) await restoreResults();
      }
      if (state.persistence_error) notice(englishError(state.persistence_error));
    } catch (error) {notice(error.message);}
  }, 1000);
}
$('retryLogin').addEventListener('click', () => action(async () => {
  await updateState(await api('restore-login', {})); scheduleRestore();
}));

async function init(propagate = false, bootstrap = null) {
  return action(async () => {
    bootstrap ||= await api('bootstrap');
    speedSupported = bootstrap.capabilities?.scan_speed === true;
    $('scanSpeedHelp').textContent = speedSupported ? t("Up to 100 records per batch. Speed depends on the network and returned records. You can adjust the interval while fetching; the current wait will finish normally.") : t("Speed controls require core 1.4.0. Download and install an updated core from Install & download.");
    const state = await api('status');
    storageSupported = bootstrap.capabilities?.local_storage === true && state.storage_supported === true;
    await updateState(state);
    if (state.stage === 'ready') await loadChannels();
    if (state.stage === 'ready' || state.cached_channels?.length) await restoreResults();
    if (state.stage === 'restoring') scheduleRestore();
    if (state.persistence_error) notice(englishError(state.persistence_error));
    return state;
  }, propagate);
}

onLanguageChange(() => {
  const expanded = new Map([...document.querySelectorAll('.message-card')].map(card => [
    `${card.dataset.channelId}/${card.dataset.messageId}`,
    {text:card.querySelector('.message-text:not(.previous-message .message-text)')?.classList.contains('expanded'), previous:card.querySelector('.previous-message')?.open},
  ]));
  if (channels.length) renderChannels();
  if (lastState) updateState({...lastState, stage});
  if (messages.length || viewingBlocked || resultsChannelId) {
    populateEmojis(); populateHashtags(); render();
  }
  for (const card of document.querySelectorAll('.message-card')) {
    const state = expanded.get(`${card.dataset.channelId}/${card.dataset.messageId}`);
    if (state?.text) {
      card.querySelector('.message-text:not(.previous-message .message-text)')?.classList.add('expanded');
      const button = card.querySelector('.expand-button'); if (button) button.textContent = t("Collapse ↑");
    }
    if (state?.previous && card.querySelector('.previous-message')) card.querySelector('.previous-message').open = true;
  }
  if (lastScan) showProgress(lastScan);
});

if (isPortal) {
  await prepareCore(async bootstrap => {token = bootstrap.token; return init(true, bootstrap);});
} else {
  $('stopCore').hidden = !localManagedCore;
  init();
}
