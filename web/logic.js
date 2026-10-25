import {t, locale} from './i18n.js';
export function normalizeEmoji(value) { return value.replace(/\ufe0f/g, ''); }
function selections(value, normalize) {
  return [...new Set((Array.isArray(value) ? value : [value]).map(normalize).filter(Boolean))];
}
function reactionScore(message, keys) {
  return keys.length ? keys.reduce((total, key) => total + (message.reactions[key] || 0), 0) : message.total;
}
export function score(message, emoji = '') {
  return reactionScore(message, selections(emoji, normalizeEmoji));
}
export function normalizeHashtag(value) {
  const tag = value.trim().normalize('NFC').toLocaleLowerCase();
  return tag && !tag.startsWith('#') ? `#${tag}` : tag;
}
export function extractHashtags(message) {
  // Telegram entities are authoritative. The fallback supports older in-memory rows.
  const tags = Array.isArray(message.hashtags) ? message.hashtags :
    [...message.text.matchAll(/(?<![\p{L}\p{N}\p{M}_/#])#[\p{L}\p{N}\p{M}_]+/gu)].map(match => match[0]);
  const unique = new Map();
  for (const tag of tags) {
    const key = normalizeHashtag(tag);
    if (key && !unique.has(key)) unique.set(key, tag);
  }
  return [...unique.values()];
}
export function collectHashtags(messages) {
  const tags = new Map();
  for (const message of messages.filter(message => !message.blocked && !message.unavailable)) for (const label of extractHashtags(message)) {
    const key = normalizeHashtag(label);
    const entry = tags.get(key) || {key, label, count:0};
    entry.count++; tags.set(key, entry);
  }
  return [...tags.values()].sort((a,b) => b.count - a.count || a.label.localeCompare(b.label, locale()));
}
export function selectMessages(messages, {query = '', emoji = '', sort = 'reactions', hideZero = false, hashtag = '',
  excludeEmojis = false, excludeHashtags = false} = {}) {
  const keyword = query.trim().toLocaleLowerCase();
  const emojis = selections(emoji, normalizeEmoji), tags = selections(hashtag, normalizeHashtag);
  // Excluded reactions cannot contribute to a score: rank the remaining rows by total.
  const ranking = excludeEmojis ? [] : emojis;
  return messages.filter(message => {
    if (message.blocked || message.unavailable || (keyword && !message.text.toLocaleLowerCase().includes(keyword))) return false;
    if (excludeEmojis && emojis.some(key => (message.reactions[key] || 0) > 0)) return false;
    if (tags.length) {
      const matches = extractHashtags(message).some(value => tags.includes(normalizeHashtag(value)));
      if (excludeHashtags ? matches : !matches) return false;
    }
    return !hideZero || reactionScore(message, ranking) > 0;
  }).sort((a, b) => {
    const time = Date.parse(b.date) - Date.parse(a.date);
    if (sort === 'oldest') return -time || a.id - b.id;
    if (sort === 'newest') return time || b.id - a.id;
    return reactionScore(b, ranking) - reactionScore(a, ranking) || time || b.id - a.id;
  });
}
export function emojiLabel(value) {
  if (value.startsWith('custom:')) return t("Custom reaction #{0}", [value.slice(7)]);
  return value === '❤' ? '❤️' : value;
}

// Keep page navigation bounded even for very large local snapshots.
export function paginationItems(current, total) {
  const start = Math.max(1, Math.min(current - 2, total - 4));
  const numbers = new Set([1, total]);
  for (let value = start; value <= Math.min(total, start + 4); value++) numbers.add(value);
  const items = [];
  for (const value of [...numbers].sort((a, b) => a - b)) {
    if (items.length && value - items.at(-1) > 1) items.push(null);
    items.push(value);
  }
  return items;
}
