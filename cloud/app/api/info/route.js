import {NextResponse} from 'next/server';
import {requireShop, unauthorized} from '../../../lib/auth';
import {appOrigin} from '../../../lib/google';

export const dynamic = 'force-dynamic';

export async function GET(request) {
  if (!requireShop(request)) return unauthorized();
  return NextResponse.json({scan_url: `${appOrigin(request)}/scan`});
}
