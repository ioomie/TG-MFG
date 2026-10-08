import test from 'node:test';
import assert from 'node:assert/strict';
import {selectMessages, score, extractHashtags, collectHashtags} from '../web/logic.js';

const data = [
  {id:1, date:'2026-10-01T00:00:00Z', text:'Design 系统', total:12, reactions:{'❤':2, '👍':10}},
  {id:2, date:'2026-10-02T00:00:00Z', text:'城市设计', total:9, reactions:{'❤':9}},
  {id:3, date:'2026-10-03T00:00:00Z', text:'<script>alert(1)</script>', total:0, reactions:{}},
  {id:4, date:'2026-10-04T00:00:00Z', text:'Design notes', total:9, reactions:{'❤':9}},
];
test('sort by total, selected heart, and newest as tie-breaker', () => {
  assert.deepEqual(selectMessages(data).map(m=>m.id), [1,4,2,3]);
  assert.deepEqual(selectMessages(data, {emoji:'❤️'}).map(m=>m.id), [4,2,1,3]);
  assert.equal(score(data[0], '❤️'), 2);
  assert.equal(data[0].id, 1);
});
test('keyword search is literal, case insensitive and works with Chinese', () => {
  assert.deepEqual(selectMessages(data,{query:' DESIGN '}).map(m=>m.id), [1,4]);
  assert.deepEqual(selectMessages(data,{query:'设计'}).map(m=>m.id), [2]);
  assert.deepEqual(selectMessages(data,{query:'<script>'}).map(m=>m.id), [3]);
});
test('hide zero applies to the selected emoji, time sorts do not mutate source', () => {
  assert.deepEqual(selectMessages(data,{emoji:'👍', hideZero:true}).map(m=>m.id), [1]);
  assert.deepEqual(selectMessages(data,{sort:'newest'}).map(m=>m.id), [4,3,2,1]);
  assert.deepEqual(selectMessages(data,{sort:'oldest'}).map(m=>m.id), [1,2,3,4]);
  assert.deepEqual(data.map(m=>m.id), [1,2,3,4]);
});

test('hashtag extraction supports Chinese, deduplication and authoritative entities', () => {
  assert.deepEqual(extractHashtags({text:'😀 #设计 #AI #ai #机器学习 C# https://example.org/#fragment'}), ['#设计','#AI','#机器学习']);
  assert.deepEqual(extractHashtags({text:'#looksLikeATag', hashtags:[]}), []);
  assert.deepEqual(extractHashtags({text:'ignored', hashtags:['#设计', '#AI', '#ai']}), ['#设计','#AI']);
});
test('hashtag and text filters retain the current reaction or time order', () => {
  const tagged = data.map((row,i) => ({...row, hashtags:i === 2 ? ['#Other'] : ['#AI', '#设计']}));
  for (const sort of ['reactions','newest','oldest']) {
    const expected = selectMessages(tagged, {emoji:'❤',sort}).filter(row => row.text.toLowerCase().includes('design'));
    const actual = selectMessages(tagged, {emoji:'❤',sort,hashtag:'#ai',query:'design'});
    assert.deepEqual(actual.map(row => row.id), expected.map(row => row.id));
  }
  assert.deepEqual(selectMessages(tagged, {hashtag:'#A'}), []);
  assert.deepEqual(selectMessages(tagged, {hashtag:'设计',hideZero:true}).map(row=>row.id), [1,4,2]);
});
test('hashtag menu counts messages once per tag', () => {
  assert.deepEqual(collectHashtags([
    {text:'',hashtags:['#AI','#ai','#设计']}, {text:'',hashtags:['#ai']}
  ]), [{key:'#ai',label:'#AI',count:2},{key:'#设计',label:'#设计',count:1}]);
});

test('blocked messages never enter search, tag menus or sorted results; restoring retains sorting', () => {
  const rows = data.map(row => ({...row, hashtags:row.id === 4 ? ['#blockedOnly'] : ['#AI'], blocked:row.id === 4}));
  for (const sort of ['reactions','newest','oldest']) {
    const expected = selectMessages(data,{emoji:'❤',sort,query:'design'}).filter(row => row.id !== 4);
    assert.deepEqual(selectMessages(rows,{emoji:'❤',sort,query:'design'}).map(row=>row.id), expected.map(row=>row.id));
  }
  assert.deepEqual(selectMessages(rows,{hashtag:'#blockedOnly'}), []);
  assert.equal(collectHashtags(rows).some(tag => tag.key === '#blockedonly'), false);
  rows[3].blocked = false;
  assert.deepEqual(selectMessages(rows,{emoji:'❤',query:'design'}).map(row=>row.id), [4,1]);
  assert.equal(collectHashtags(rows).find(tag => tag.key === '#blockedonly').count, 1);
});


test('unavailable cached messages stay out of search and hashtag menus', () => {
  const rows = data.map(row => ({...row, unavailable:row.id === 4, hashtags:row.id === 4 ? ['#unavailable'] : ['#AI']}));
  assert.equal(selectMessages(rows).some(row => row.id === 4), false);
  assert.deepEqual(selectMessages(rows,{hashtag:'#unavailable'}), []);
  assert.equal(collectHashtags(rows).some(tag => tag.key === '#unavailable'), false);
});
