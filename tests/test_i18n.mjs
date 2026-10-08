import test from 'node:test';
import assert from 'node:assert/strict';
import {t, setLanguage, getLanguage, locale, englishError} from '../web/i18n.js';
import {emojiLabel} from '../web/logic.js';

test('English default, persistent Chinese preference and explicit English reset', () => {
  assert.equal(getLanguage(), 'en');
  assert.equal(t('Message explorer'), 'Message explorer');
  const store = new Map();
  globalThis.localStorage = {setItem:(key,value) => store.set(key,value), getItem:key => store.get(key)};
  setLanguage('zh-CN');
  assert.equal(t('Message explorer'), '消息排行榜');
  assert.equal(emojiLabel('custom:123'), '自定义表情 #123');
  assert.equal(store.get('tg-mfg-language'), 'zh-CN');
  assert.equal(locale(), 'zh-CN');
  setLanguage('de'); assert.equal(getLanguage(), 'zh-CN');
  setLanguage('en');
  assert.equal(emojiLabel('custom:123'), 'Custom reaction #123');
  assert.equal(locale(), 'en-US');
});
test('parameters and message content are verbatim; errors stay English in Chinese mode', () => {
  setLanguage('zh-CN');
  assert.equal(t('From: {0}', ['消息排行榜']), '来自：消息排行榜');
  assert.equal(t('From: {0}', ['<img src=x>']), '来自：<img src=x>');
  assert.equal(englishError('Cross-site requests are not allowed.'), 'Cross-site requests are not allowed.');
  assert.match(englishError('不允许跨站请求。'), /Update the local core/);
  assert.match(englishError(null), /Request failed/);
  setLanguage('en');
});
