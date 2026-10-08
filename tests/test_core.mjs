import test from 'node:test';
import assert from 'node:assert/strict';
const storage = new Map();
globalThis.document = {querySelector:() => ({content:'portal'})};
globalThis.localStorage = {getItem:k => storage.get(k) ?? null, setItem:(k,v) => storage.set(k,v)};
const {desktopPlatform, installationHint, rememberInstallation} = await import('../web/core.js');

test('installation marker stores only non-secret fields from a confirmed managed core', () => {
  storage.clear();
  const data = {token:'secret bootstrap token', phone:'private phone', installation:{managed:true, version:1, platform:'windows', id:'install-id', api_hash:'secret', path:'ignored'}};
  assert.equal(rememberInstallation(data,'windows'),true);
  assert.deepEqual(JSON.parse(storage.get('tg-mfg-installation')), {version:1,platform:'windows',id:'install-id'});
  assert.deepEqual(installationHint('windows'),{version:1,platform:'windows',id:'install-id'});
  assert.equal(installationHint('mac'),null);
});

test('unmanaged cores or mere download clicks do not establish installation', () => {
  storage.clear();
  assert.equal(rememberInstallation({token:'token'},'mac'),false);
  assert.equal(rememberInstallation({installation:{managed:true, version:1, platform:'windows', id:'id'}},'mac'),false);
  assert.equal(storage.size,0);
  storage.set('tg-mfg-installation','corrupted data');
  assert.equal(installationHint('mac'),null);
});

test('desktop detection excludes mobile, iPad desktop mode and other systems', () => {
  assert.equal(desktopPlatform({userAgent:'Windows NT',platform:'Win32'}),'windows');
  assert.equal(desktopPlatform({userAgent:'Macintosh',platform:'MacIntel',maxTouchPoints:0}),'mac');
  for (const nav of [{userAgent:'iPhone',platform:'iPhone'}, {userAgent:'Macintosh',platform:'MacIntel',maxTouchPoints:5}, {userAgent:'Linux',platform:'Linux x86_64'}]) assert.equal(desktopPlatform(nav),'');
});
