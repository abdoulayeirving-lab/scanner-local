import {NextResponse} from 'next/server';
import {googleEnabled} from '../../../../lib/google';

export const dynamic = 'force-dynamic';

export async function GET() {
  return NextResponse.json({enabled: googleEnabled()});
}
