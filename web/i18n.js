import {chinese} from './translations.js';

const storageKey = 'tg-mfg-language';
let language = 'en';
try {if (globalThis.localStorage?.getItem(storageKey) === 'zh-CN') language = 'zh-CN';} catch {}
const listeners = new Set();
const rendered = new Map();
const authored = new Map();
export function englishError(value) {
  return typeof value === 'string' && value && !/[\u3400-\u9fff]/u.test(value)
    ? value : 'Request failed. Update the local core if it is out of date, then try again.';
}
export function getLanguage() {return language;}
export function locale() {return language === 'zh-CN' ? 'zh-CN' : 'en-US';}
function format(template, values) {
  return template.replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)] ?? ''));
}
export function t(key, values = []) {
  const en = format(key, values), zh = format(chinese[key] || key, values);
  // Only exact, explicitly authored UI strings are registered. Message text is never inspected.
  rendered.set(en, {en, zh}); rendered.set(zh, {en, zh});
  if (rendered.size > 4000) {for (let i = 0; i < 1000; i++) rendered.delete(rendered.keys().next().value);}
  return language === 'zh-CN' ? zh : en;
}
for (const [en, zh] of Object.entries(chinese)) {
  if (!/\{\d+\}/.test(en)) {authored.set(en, {en, zh}); authored.set(zh, {en, zh});}
}
export function onLanguageChange(listener) {listeners.add(listener); return () => listeners.delete(listener);}
function translateDOM() {
  if (!globalThis.document?.documentElement) return;
  document.documentElement.lang = language;
  const protectedContent = '.message-text, .hashtag-chip, #channelSelect, [data-user-content], #languageSelect';
  const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement?.closest(protectedContent + ', script, style')) continue;
    const pair = rendered.get(node.nodeValue) || authored.get(node.nodeValue);
    if (pair) node.nodeValue = language === 'zh-CN' ? pair.zh : pair.en;
  }
  for (const el of document.querySelectorAll('[placeholder], [title], [aria-label]')) {
    if (el.closest(protectedContent)) continue;
    for (const attr of ['placeholder', 'title', 'aria-label']) {
      const pair = rendered.get(el.getAttribute(attr)) || authored.get(el.getAttribute(attr));
      if (pair) el.setAttribute(attr, language === 'zh-CN' ? pair.zh : pair.en);
    }
  }
  const select = document.getElementById('languageSelect');
  if (select) select.value = language;
}
export function setLanguage(value) {
  if (!['en', 'zh-CN'].includes(value)) return;
  language = value;
  try {globalThis.localStorage?.setItem(storageKey, language);} catch {}
  translateDOM();
  for (const listener of listeners) listener();
}
if (globalThis.document?.documentElement) {
  translateDOM();
  document.getElementById('languageSelect')?.addEventListener('change', event => setLanguage(event.target.value));
}
