import crypto from 'crypto';
import {NextResponse} from 'next/server';
import {appOrigin, googleAuthUrl, googleEnabled} from '../../../../lib/google';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  const origin = appOrigin(request);
  if (!googleEnabled()) {
    return NextResponse.redirect(`${origin}/login?error=google-non-configure`);
  }
  const state = crypto.randomBytes(16).toString('base64url');
  const response = NextResponse.redirect(googleAuthUrl(origin, state));
  response.cookies.set('stock_oauth_state', state, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: 600
  });
  return response;
}
