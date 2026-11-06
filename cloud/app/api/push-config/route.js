import {NextResponse} from 'next/server';
import {requireShop, unauthorized} from '../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  if (!requireShop(request)) return unauthorized();
  const publicKey = process.env.VAPID_PUBLIC_KEY || '';
  const privateKey = process.env.VAPID_PRIVATE_KEY || '';
  if (!publicKey || !privateKey) {
    return NextResponse.json(
      {enabled: false, reason: 'Notifications Web Push non configurées sur le serveur'},
      {status: 503});
  }
  return NextResponse.json({enabled: true, public_key: publicKey});
}
