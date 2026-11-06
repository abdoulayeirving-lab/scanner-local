export function normalizeNumber(value, fallback = 0) {
  if (value === null || value === undefined) return fallback;
  let text = String(value).trim().replace(/\s/g, '');
  if (!text) return fallback;
  if (text.includes(',') && text.includes('.')) {
    if (text.lastIndexOf(',') > text.lastIndexOf('.')) {
      text = text.replace(/\./g, '').replace(',', '.');
    } else {
      text = text.replace(/,/g, '');
    }
  } else if (text.includes(',')) {
    text = text.replace(',', '.');
  }
  const number = Number(text);
  if (!Number.isFinite(number)) throw new Error('nombre invalide');
  return number;
}

export function isValidDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day;
}

export function nowText() {
  return new Date().toISOString().slice(0, 19).replace('T', ' ');
}
