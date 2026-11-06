import {NextResponse} from 'next/server';
import {query} from '../../../../lib/db';
import {setSession, verifyPassword} from '../../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function POST(request) {
  const data = await request.json().catch(() => ({}));
  const email = String(data.email || '').trim().toLowerCase();
  const password = String(data.password || '');

  const result = await query(
    'SELECT id, name, password_hash FROM stock_shops WHERE email = $1', [email]);
  const shop = result.rows[0];
  if (!shop || !shop.password_hash || !verifyPassword(password, shop.password_hash)) {
    return NextResponse.json({error: 'email ou mot de passe incorrect'}, {status: 401});
  }
  const response = NextResponse.json({ok: true, name: shop.name});
  setSession(response, shop.id);
  return response;
}
