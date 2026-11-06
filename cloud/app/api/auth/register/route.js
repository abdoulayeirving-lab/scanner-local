import {NextResponse} from 'next/server';
import {query} from '../../../../lib/db';
import {hashPassword, setSession} from '../../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function POST(request) {
  const data = await request.json().catch(() => ({}));
  const email = String(data.email || '').trim().toLowerCase();
  const password = String(data.password || '');
  const shopName = String(data.shop_name || '').trim();

  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email) || email.length > 254) {
    return NextResponse.json({error: 'adresse email invalide'}, {status: 400});
  }
  if (password.length < 6) {
    return NextResponse.json({error: 'mot de passe : 6 caractères minimum'}, {status: 400});
  }

  try {
    const result = await query(
      'INSERT INTO stock_shops(name, email, password_hash) VALUES($1, $2, $3) RETURNING id, name',
      [shopName || email.split('@')[0], email, hashPassword(password)]
    );
    const response = NextResponse.json({ok: true, name: result.rows[0].name});
    setSession(response, result.rows[0].id);
    return response;
  } catch (error) {
    if (error.code === '23505') {
      return NextResponse.json({error: 'un compte existe déjà avec cet email'}, {status: 409});
    }
    console.error(error);
    return NextResponse.json({error: 'Impossible de créer le compte.'}, {status: 500});
  }
}
