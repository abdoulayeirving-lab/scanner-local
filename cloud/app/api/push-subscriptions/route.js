import {NextResponse} from 'next/server';
import {query} from '../../../lib/db';
import {requireShop, unauthorized} from '../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function POST(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const data = await request.json().catch(() => ({}));
  const subscription = data.subscription || {};
  const endpoint = subscription.endpoint;
  const keys = subscription.keys || {};
  if (!endpoint || !/^https:\/\//.test(endpoint) || !keys.p256dh || !keys.auth) {
    return NextResponse.json({error: 'abonnement Web Push invalide'}, {status: 400});
  }
  await query(
    `INSERT INTO stock_push_subscriptions(endpoint, subscription_json, created, shop_id)
     VALUES($1, $2, $3, $4)
     ON CONFLICT (endpoint) DO UPDATE
     SET subscription_json = EXCLUDED.subscription_json, shop_id = EXCLUDED.shop_id`,
    [endpoint, JSON.stringify(subscription), new Date().toISOString(), shopId]
  );
  return NextResponse.json({ok: true});
}

export async function DELETE(request) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const data = await request.json().catch(() => ({}));
  if (!data.endpoint) {
    return NextResponse.json({error: 'abonnement invalide'}, {status: 400});
  }
  await query(
    'DELETE FROM stock_push_subscriptions WHERE endpoint = $1 AND shop_id = $2',
    [data.endpoint, shopId]);
  return NextResponse.json({ok: true});
}
