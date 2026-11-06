import {NextResponse} from 'next/server';
import {query} from '../../../lib/db';
import {requireShop, unauthorized} from '../../../lib/auth';
import {isValidDate, isValidMonthYear, normalizeNumber, nowText} from '../../../lib/values';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const result = await query(
    'SELECT * FROM stock_products WHERE shop_id = $1 ORDER BY updated DESC', [shopId]);
  return NextResponse.json(result.rows);
}

export async function POST(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const data = await request.json().catch(() => ({}));

  const code = String(data.code || '').trim();
  if (!code) return NextResponse.json({error: 'code manquant'}, {status: 400});

  let qty;
  let price;
  let expiration;
  const expirationProvided = Object.prototype.hasOwnProperty.call(data, 'expiration_date');
  const name = String(data.name || '').trim();
  try {
    const qtyNumber = normalizeNumber(data.qty, 1);
    if (!Number.isInteger(qtyNumber) || qtyNumber < 0) throw new Error('quantité invalide');
    qty = qtyNumber;
    const rawPrice = data.price;
    price = rawPrice === null || rawPrice === undefined || String(rawPrice).trim() === ''
      ? null : normalizeNumber(rawPrice, 0);
    if (price !== null && price < 0) throw new Error('prix invalide');
    expiration = expirationProvided ? data.expiration_date : null;
    if (expiration) {
      if (typeof expiration !== 'string' || (!isValidMonthYear(expiration) && !isValidDate(expiration))) {
        throw new Error('date invalide');
      }
    }
  } catch {
    return NextResponse.json(
      {error: 'quantité, prix ou date d’expiration invalide'}, {status: 400});
  }

  const updated = nowText();
  const existing = await query(
    'SELECT * FROM stock_products WHERE code = $1 AND shop_id = $2', [code, shopId]);

  if (existing.rows[0]) {
    const current = existing.rows[0];
    const finalPrice = price === null ? Number(current.price || 0) : price;
    if (expirationProvided) {
      await query(
        `UPDATE stock_products SET name = $1, price = $2, qty = qty + $3, updated = $4,
         expiration_date = $5 WHERE id = $6`,
        [name || current.name, finalPrice, qty, updated, expiration || null, current.id]
      );
    } else {
      await query(
        'UPDATE stock_products SET name = $1, price = $2, qty = qty + $3, updated = $4 WHERE id = $5',
        [name || current.name, finalPrice, qty, updated, current.id]
      );
    }
  } else {
    await query(
      `INSERT INTO stock_products(shop_id, code, name, price, qty, updated, expiration_date)
       VALUES($1, $2, $3, $4, $5, $6, $7)`,
      [shopId, code, name, price === null ? 0 : price, qty, updated, expiration || null]
    );
  }
  return NextResponse.json({ok: true});
}
