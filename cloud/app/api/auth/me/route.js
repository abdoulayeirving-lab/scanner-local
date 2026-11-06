import {NextResponse} from 'next/server';
import {query} from '../../../../lib/db';
import {requireShop, unauthorized} from '../../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const result = await query('SELECT name, email FROM stock_shops WHERE id = $1', [shopId]);
  const shop = result.rows[0];
  if (!shop) return unauthorized();
  return NextResponse.json({authenticated: true, name: shop.name, email: shop.email});
}
