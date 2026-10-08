import {t, locale, onLanguageChange} from './i18n.js';
export function normalizeEmoji(value) { return value.replace(/\ufe0f/g, ''); }
export function score(message, emoji) {
  return emoji ? (message.reactions[normalizeEmoji(emoji)] || 0) : message.total;
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
export function selectMessages(messages, {query = '', emoji = '', sort = 'reactions', hideZero = false, hashtag = ''} = {}) {
  const keyword = query.trim().toLocaleLowerCase();
  const tag = normalizeHashtag(hashtag);
  return messages.filter(message => !message.blocked && !message.unavailable && (!keyword || message.text.toLocaleLowerCase().includes(keyword)) &&
    (!tag || extractHashtags(message).some(value => normalizeHashtag(value) === tag)) &&
    (!hideZero || score(message, emoji) > 0)).sort((a, b) => {
      const time = Date.parse(b.date) - Date.parse(a.date);
      if (sort === 'oldest') return -time || a.id - b.id;
      if (sort === 'newest') return time || b.id - a.id;
      return score(b, emoji) - score(a, emoji) || time || b.id - a.id;
    });
}
export function emojiLabel(value) {
  if (value.startsWith('custom:')) return t("Custom reaction #{0}", [value.slice(7)]);
  return value === '❤' ? '❤️' : value;
}
