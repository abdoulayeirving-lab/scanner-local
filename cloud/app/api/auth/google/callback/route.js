import {NextResponse} from 'next/server';
import {query} from '../../../../../lib/db';
import {setSession} from '../../../../../lib/auth';
import {appOrigin, exchangeCode, fetchProfile, googleEnabled} from '../../../../../lib/google';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  const origin = appOrigin(request);
  const url = new URL(request.url);
  const state = url.searchParams.get('state');
  const code = url.searchParams.get('code');
  const cookieState = request.cookies.get('stock_oauth_state')?.value;

  if (url.searchParams.get('error') || !state || !cookieState || state !== cookieState) {
    return NextResponse.redirect(`${origin}/login?error=google-refuse`);
  }
  if (!code || !googleEnabled()) {
    return NextResponse.redirect(`${origin}/login?error=google-non-configure`);
  }

  try {
    const tokens = await exchangeCode(origin, code);
    const profile = await fetchProfile(tokens.access_token);
    const googleSub = String(profile.sub || '');
    const email = String(profile.email || '').trim().toLowerCase();
    const name = String(profile.name || profile.given_name || email.split('@')[0]);
    if (!googleSub || !email) throw new Error('profil Google incomplet');

    const existing = await query(
      'SELECT id FROM stock_shops WHERE google_sub = $1 OR email = $2 LIMIT 1',
      [googleSub, email]
    );
    let shopId;
    if (existing.rows[0]) {
      shopId = existing.rows[0].id;
      await query(
        `UPDATE stock_shops SET google_sub = $1
         WHERE id = $2 AND (google_sub IS NULL OR google_sub = '')`,
        [googleSub, shopId]
      );
    } else {
      const inserted = await query(
        'INSERT INTO stock_shops(name, email, google_sub) VALUES($1, $2, $3) RETURNING id',
        [name || 'Ma boutique', email, googleSub]
      );
      shopId = inserted.rows[0].id;
    }

    const response = NextResponse.redirect(`${origin}/`);
    setSession(response, shopId);
    response.cookies.set('stock_oauth_state', '', {path: '/', maxAge: 0});
    return response;
  } catch (error) {
    console.error(error);
    return NextResponse.redirect(`${origin}/login?error=google-echec`);
  }
}
