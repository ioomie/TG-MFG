import {t, locale, onLanguageChange} from './i18n.js';
import {recordRequest} from './diagnostics.js';
const $ = id => document.getElementById(id);
export const isPortal = document.querySelector('meta[name="core-mode"]')?.content === 'portal';
const installationKey = 'tg-mfg-installation';
let coreReady = false;

export function installationHint(platform) {
  try {
    const record = JSON.parse(localStorage.getItem(installationKey));
    return record?.version === 1 && record.platform === platform && typeof record.id === 'string' ? record : null;
  } catch {return null;}
}

export function rememberInstallation(data, platform) {
  const receipt = data.installation;
  if (!receipt || receipt.managed !== true || receipt.version !== 1 || receipt.platform !== platform || typeof receipt.id !== 'string') return false;
  // Never store the bootstrap token, paths, Telegram keys, phone number or account session.
  try {localStorage.setItem(installationKey, JSON.stringify({version: 1, platform, id: receipt.id}));} catch {}
  return true;
}

export function showCorePanel(message) {
  coreReady = false;
  $('workspace').hidden = true;
  $('corePanel').hidden = false;
  $('stopCore').hidden = true;
  $('returnWorkspace').hidden = true;
  $('connectionStatus').textContent = t("Core disconnected");
  $('statusDot').classList.remove('connected');
  if (message) $('coreStatus').textContent = message;
}

export function desktopPlatform(nav = navigator) {
  const agent = nav.userAgent || '';
  if (/Android|iPhone|iPad|iPod/i.test(agent) || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1)) return '';
  const platform = nav.userAgentData?.platform || nav.platform || '';
  if (/Win/i.test(platform)) return 'windows';
  if (/Mac/i.test(platform)) return 'mac';
  return '';
}

export async function localFetch(url, options = {}) {
  const start = performance.now();
  try {
    const response = await fetch(url, {...options, ...(isPortal ? {mode: 'cors', targetAddressSpace: 'loopback'} : {})});
    recordRequest(url,options.method || 'GET',response.status,performance.now()-start);
    return response;
  } catch (error) {
    recordRequest(url,options.method || 'GET',0,performance.now()-start,error);
    throw error;
  }
}

export async function prepareCore(connected) {
  const platform = desktopPlatform();
  const origin = document.querySelector('meta[name="core-origin"]').content;
  let managing = location.hash === '#install';
  $('workspace').hidden = true;
  $('corePanel').hidden = false;
  $('openLocalCore').href = origin;
  if (!platform) {
    $('coreTitle').textContent = t("Mac and Windows only");
    $('coreStatus').textContent = t("Open this website on a Mac or Windows computer. Core downloads are not available for phones, tablets or other operating systems.");
    $('coreActions').hidden = true;
    $('coreSteps').hidden = true;
    return;
  }
  $('downloadCore').hidden = false;
  $('manageCore').hidden = false;
  $('downloadCore').textContent = platform === 'windows' ? t("Download Windows installer (x64)") : t("Download Mac installer");
  $('downloadCore').href = `/downloads/${platform}?origin=${encodeURIComponent(location.origin)}`;
  $('coreLaunchStep').textContent = platform === 'windows' ? t("First, double-click install.bat to install in your user directory and start the core.") : t("First, double-click install.command to install in your user directory and start the core.");
  $('corePlatformNote').textContent = platform === 'windows' ? t("The Windows package includes its runtime. Supports Intel / AMD 64-bit Windows 10 / 11.") : t("The Mac core supports Intel and Apple Silicon. Python 3.9+ is required; dependencies are installed on first start.");
  const hint = installationHint(platform);
  $('startCore').hidden = false;
  $('startCore').firstChild.textContent = hint ? t("Start core ") : t("Already installed? Start core ");
  $('coreInstallNote').textContent = hint
    ? t("This browser remembers the installation marker. Click “Start core” when it is stopped. Reinstall if launching fails or its files were deleted.")
    : t("After the first installation connects, this website remembers an installation marker. Start the core here next time, without finding a script or running it at boot.");
  let launching = false;
  let connectedOnce = false;
  let checking = false;
  function installationView() {
    managing = true;
    history.replaceState(null, '', '#install');
    $('workspace').hidden = true;
    $('corePanel').hidden = false;
    $('coreTitle').textContent = t("Install & download");
    $('returnWorkspace').hidden = !coreReady;
    if (coreReady) $('coreStatus').textContent = t("The local core is connected. You can download and reinstall it. The installer stops the old core and restores opted-in local data. Check the connection again after installation.");
  }
  $('manageCore').addEventListener('click', installationView);
  $('returnWorkspace').addEventListener('click', () => {
    if (!coreReady) return;
    managing = false;
    history.replaceState(null, '', location.pathname + location.search);
    $('corePanel').hidden = true;
    $('workspace').hidden = false;
    $('returnWorkspace').hidden = true;
    $('coreTitle').textContent = t("Connect local core");
  });
  window.addEventListener('hashchange', () => {
    if (location.hash === '#install') installationView();
    else if (managing && coreReady) $('returnWorkspace').click();
  });
  if (managing) installationView();
  async function check(quiet = false) {
    if (checking) return;
    checking = true;
    $('retryCore').disabled = true;
    if (!quiet) $('coreStatus').textContent = t("Checking the local core…");
    try {
      const response = await localFetch(`${origin}/api/bootstrap`, {
        cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(quiet ? 1500 : 6000),
      });
      if (response.status === 403) throw new Error("The core does not allow this website. Close the old core, then download and start the package provided by this website.");
      if (!response.ok) throw new Error("The local core returned an unexpected response. Restart the core.");
      const data = await response.json();
      if (data.application !== 'TG-MFG' || data.protocol !== 1 || typeof data.token !== 'string' || data.token.length < 32) {
        throw new Error("Another service or an incompatible core was detected. Check the core version and port.");
      }
      // Initialization checks the authenticated API; a bootstrap response alone is insufficient.
      await connected(data);
      const managed = rememberInstallation(data, platform);
      $('stopCore').hidden = !managed;
      if (managed) {
        $('startCore').firstChild.textContent = t("Start core ");
        $('coreInstallNote').textContent = t("Installed in a fixed user directory. This browser stores only an installation marker. Start the core from this page next time.");
      }
      connectedOnce = true;
      coreReady = true;
      if (managing) installationView();
      else {
        $('corePanel').hidden = true;
        $('workspace').hidden = false;
      }
    } catch (error) {
      connectedOnce = false;
      coreReady = false;
      $('returnWorkspace').hidden = true;
      $('workspace').hidden = true;
      $('corePanel').hidden = false;
      $('stopCore').hidden = true;
      $('connectionStatus').textContent = "Core API check failed";
      if (!quiet) $('coreStatus').textContent = error.name === 'TypeError' || error.name === 'TimeoutError' || error.name === 'AbortError'
        ? "Could not connect to the local core. Start it if already downloaded, or download it below. If it is running, check the browser’s local network permission or open the local page directly."
        : error.message;
    } finally {
      checking = false;
      $('retryCore').disabled = false;
    }
  }
  $('retryCore').addEventListener('click', () => check());
  $('startCore').addEventListener('click', async event => {
    // A user click lets the browser ask the OS to open the registered launcher.
    // No URL parameters carry paths, credentials, commands or the website origin.
    if (launching) {event.preventDefault(); return;}
    launching = true;
    connectedOnce = false;
    coreReady = false;
    $('returnWorkspace').hidden = true;
    $('coreStatus').textContent = t("Requesting the TG-MFG launcher. Allow the browser to open it when prompted. This page will check the connection automatically.");
    for (let attempt = 0; attempt < 15 && !connectedOnce; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      await check(true);
    }
    if (!connectedOnce) $('coreStatus').textContent = "Still disconnected. Allow TG-MFG to open, or run the installer first. Reinstall if the core files were deleted, then check the connection again.";
    launching = false;
  });
  await check();
  // A saved marker permits one best-effort wake-up. Browsers may still require a user click.
  // The OS locates the registered helper; the website never opens an arbitrary file path.
  if (hint && !connectedOnce && !managing) $('startCore').click();
}
