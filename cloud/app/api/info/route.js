import {NextResponse} from 'next/server';
import {requireShop, unauthorized, createPairToken} from '../../../lib/auth';
import {appOrigin} from '../../../lib/google';

export const dynamic = 'force-dynamic';

// Token de jumelage (1h) : le QR du dashboard embarque une URL /scan?pair=TOKEN
// pour que le téléphone se connecte automatiquement à la boutique du PC.
const pairCache = new Map();
const PAIR_TTL_MS = 60 * 1000;

export async function GET(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const origin = appOrigin(request);
  const cached = pairCache.get(shopId);
  const now = Date.now();
  let url;
  if (cached && cached.expires > now) {
    url = cached.pair.url;
  } else {
    const token = createPairToken(shopId);
    url = `${origin}/scan?pair=${token}`;
    pairCache.set(shopId, {pair: {token, url}, expires: now + PAIR_TTL_MS});
  }
  return NextResponse.json({
    scan_url: `${origin}/scan`,
    pair: {url, token: url.split('pair=')[1]}
  });
}
