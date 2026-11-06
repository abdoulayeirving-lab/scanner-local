import {NextResponse} from 'next/server';
import {query} from '../../../../lib/db';

export const dynamic = 'force-dynamic';
export const maxDuration = 60;

const ALERT_DAYS = 7;

function daysLeft(dateText) {
  const parts = String(dateText).split('-').map(Number);
  const year = parts[0];
  const month = parts[1];
  const day = parts.length > 2 ? parts[2] : 1;
  const expiry = Date.UTC(year, month - 1, day);
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((expiry - today) / 86400000);
}

export async function GET(request) {
  const secret = process.env.CRON_SECRET;
  if (secret) {
    const authorization = request.headers.get('authorization') || '';
    if (authorization !== `Bearer ${secret}`) {
      return NextResponse.json({error: 'non autorisé'}, {status: 401});
    }
  }

  const publicKey = process.env.VAPID_PUBLIC_KEY;
  const privateKey = process.env.VAPID_PRIVATE_KEY;
  if (!publicKey || !privateKey) {
    return NextResponse.json({ok: false, reason: 'VAPID non configuré'});
  }

  const webpush = (await import('web-push')).default;
  webpush.setVapidDetails(
    process.env.VAPID_SUBJECT || 'mailto:admin@example.com', publicKey, privateKey);

  const products = await query(
    `SELECT id, shop_id, code, name, expiration_date FROM stock_products
     WHERE qty > 0 AND expiration_date IS NOT NULL AND expiration_date <> ''`);
  const subscriptions = await query(
    'SELECT endpoint, subscription_json, shop_id FROM stock_push_subscriptions');

  const byShop = new Map();
  for (const row of subscriptions.rows) {
    if (!byShop.has(row.shop_id)) byShop.set(row.shop_id, []);
    byShop.get(row.shop_id).push(row);
  }

  let sent = 0;
  for (const product of products.rows) {
    const remaining = daysLeft(product.expiration_date);
    if (!Number.isFinite(remaining) || remaining > ALERT_DAYS) continue;
    const kind = remaining < 0 ? 'expired' : 'expiring';
    const already = await query(
      `SELECT 1 FROM stock_expiry_alerts
       WHERE product_id = $1 AND expiration_date = $2 AND kind = $3`,
      [product.id, product.expiration_date, kind]);
    if (already.rows[0]) continue;

    const title = kind === 'expired' ? 'Produit expiré' : 'Expiration proche';
    const body = kind === 'expired'
      ? `${product.name || product.code} a expiré le ${product.expiration_date}.`
      : `${product.name || product.code} expire dans ${remaining} jour(s), le ${product.expiration_date}.`;
    const payload = JSON.stringify({
      title, body, tag: `expiration-${product.id}`, url: '/'
    });

    let delivered = false;
    for (const subscription of byShop.get(product.shop_id) || []) {
      try {
        await webpush.sendNotification(JSON.parse(subscription.subscription_json), payload);
        delivered = true;
        sent += 1;
      } catch (error) {
        if (error.statusCode === 404 || error.statusCode === 410) {
          await query('DELETE FROM stock_push_subscriptions WHERE endpoint = $1',
            [subscription.endpoint]);
        } else {
          console.error('Envoi push impossible', error.statusCode || error.message);
        }
      }
    }
    if (delivered) {
      await query(
        `INSERT INTO stock_expiry_alerts(product_id, expiration_date, kind, sent_at)
         VALUES($1, $2, $3, $4) ON CONFLICT DO NOTHING`,
        [product.id, product.expiration_date, kind, new Date().toISOString()]
      );
    }
  }

  return NextResponse.json({ok: true, sent});
}
