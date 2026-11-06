import {NextResponse} from 'next/server';
import {query} from '../../../../lib/db';
import {requireShop, unauthorized} from '../../../../lib/auth';

export const dynamic = 'force-dynamic';

export async function DELETE(request, {params}) {
  const shopId = requireShop(request);
  if (!shopId) return unauthorized();
  const id = params.id;
  if (!/^\d+$/.test(String(id))) {
    return NextResponse.json({error: 'identifiant invalide'}, {status: 400});
  }
  const deleted = await query(
    'DELETE FROM stock_products WHERE id = $1 AND shop_id = $2 RETURNING id', [id, shopId]);
  if (!deleted.rows[0]) {
    return NextResponse.json({error: 'produit introuvable'}, {status: 404});
  }
  await query('DELETE FROM stock_expiry_alerts WHERE product_id = $1', [id]);
  return NextResponse.json({ok: true});
}
