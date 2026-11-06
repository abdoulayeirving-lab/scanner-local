import {NextResponse} from 'next/server';
import {requireShop, setSession, verifyPairToken} from '../../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function POST(request) {
  const data = await request.json().catch(() => ({}));
  const token = String(data.token || '');
  const shopId = token ? verifyPairToken(token) : null;
  if (!shopId) {
    return NextResponse.json({error: 'Lien de scan expiré ou invalide'}, {status: 401});
  }
  const response = NextResponse.json({ok: true});
  setSession(response, shopId);
  return response;
}
