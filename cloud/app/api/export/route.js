import {query} from '../../../lib/db';
import {requireShop, unauthorized} from '../../../lib/auth';

export const dynamic = 'force-dynamic';

function csvCell(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export async function GET(request) {
  if (!requireShop(request)) return unauthorized();
  const shopId = requireShop(request);
  const result = await query(
    `SELECT code, name, price, qty, expiration_date, updated
     FROM stock_products WHERE shop_id = $1`, [shopId]);
  const lines = ['code;nom;prix;quantite;date_expiration;maj'];
  for (const row of result.rows) {
    lines.push([row.code, row.name, row.price, row.qty, row.expiration_date, row.updated]
      .map(csvCell).join(';'));
  }
  return new Response('\ufeff' + lines.join('\r\n') + '\r\n', {
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': 'attachment; filename=produits.csv'
    }
  });
}
