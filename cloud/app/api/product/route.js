import {NextResponse} from 'next/server';
import {query} from '../../../lib/db';
import {requireShop, unauthorized} from '../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const code = new URL(request.url).searchParams.get('code') || '';
  const result = await query(
    'SELECT * FROM stock_products WHERE code = $1 AND shop_id = $2', [code, shopId]);
  return NextResponse.json(result.rows[0] || {});
}
