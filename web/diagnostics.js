import {t, locale, onLanguageChange} from './i18n.js';
const $ = id => document.getElementById(id);
const events = [];
let snapshot = null;
let getToken = () => '';
let coreOrigin = '';
let debugEnabled = false;
const routes = new Set(['bootstrap','status','connect','send-code','sign-in','disconnect','shutdown','channels','scan','results','cancel','channel-count','diagnostics','debug','test-proxy']);
export function recordRequest(url, method, status, elapsed, error) {
  const operation = new URL(url, location.origin).pathname.replace(/^\/api\//,'');
  if (!routes.has(operation)) return;
  const item = {time:new Date().toISOString(),operation,method,status,duration_ms:Math.round(elapsed),
    ...(error ? {error_type:/^[A-Za-z]+Error$/.test(error.name) ? error.name : 'Error'} : {})};
  events.push(item);
  if (events.length > 200) events.shift();
  render();
}
function render() {
  if (!$('debugOutput')) return;
  $('debugOutput').textContent = events.slice(-12).map(e => `${e.time.slice(11,19)} ${e.method} ${e.operation} ${e.status || e.error_type || 'network error'} · ${e.duration_ms}ms`).join('\n') || t("Waiting for connection requests…");
}
export function setupDiagnostics(origin, tokenGetter) {
  coreOrigin = origin; getToken = tokenGetter;
  $('showDebug').addEventListener('click', () => {$('debugPanel').hidden = !$('debugPanel').hidden; render();});
  async function request(path, data) {
    const response = await fetch(`${coreOrigin}/api/${path}`, {method:data ? 'POST' : 'GET',
      headers:{'X-App-Token':getToken(),...(data ? {'Content-Type':'application/json'} : {})},
      ...(data ? {body:JSON.stringify(data)} : {}), cache:'no-store',credentials:'omit',
      mode:'cors', targetAddressSpace:'loopback',signal:AbortSignal.timeout(6000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  $('enableDebug').addEventListener('click', async () => {
    try {
      const result = await request('debug',{enabled:!debugEnabled});
      debugEnabled = result.enabled === true;
      $('enableDebug').textContent = debugEnabled ? t("Disable core debug log") : t("Enable core debug log");
      $('debugStatus').textContent = debugEnabled ? t("Debug logging enabled. The core writes debug.log in its installation directory. Retry the failed action, then export diagnostics.") : t("Core logging disabled. Browser diagnostics can still be exported.");
    } catch {
      $('debugStatus').textContent = "Could not enable core logging. Update an older core, or run debug.bat from the Windows package if website access is blocked. Browser diagnostics can still be exported.";
    }
  });
  $('exportDebug').addEventListener('click', async () => {
    let available = false;
    try {
      snapshot = await request('diagnostics'); available = true;
      debugEnabled = snapshot.debug_enabled === true;
      $('enableDebug').textContent = debugEnabled ? t("Disable core debug log") : t("Enable core debug log");
    } catch {snapshot = null;}
    const report = {application:'TG-MFG diagnostics',frontend_version:'1.6.0',website:location.origin,
      browser:navigator.userAgent,secure_context:window.isSecureContext,core_report_available:available,
      browser_events:[...events],core:snapshot};
    // Inputs, request bodies, tokens, phone numbers and message contents are never collected.
    const blob = new Blob([JSON.stringify(report,null,2)],{type:'application/json'});
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = 'TG-MFG-diagnostics.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
    $('debugStatus').textContent = available ? t("Exported redacted browser and core diagnostics.") : "Exported browser diagnostics. The core is unreachable or too old.";
  });
}
