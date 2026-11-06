import crypto from 'crypto';
import {NextResponse} from 'next/server';

export const SESSION_COOKIE = 'stock_session';
const SESSION_MAX_AGE = 60 * 60 * 24 * 30;

function secret() {
  return process.env.AUTH_SECRET || 'dev-secret-stock-local-change-me';
}

function b64url(buffer) {
  return Buffer.from(buffer).toString('base64url');
}

export function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(password, salt, 64).toString('hex');
  return `${salt}$${hash}`;
}

export function verifyPassword(password, stored) {
  try {
    const [salt, expected] = String(stored).split('$');
    const actual = crypto.scryptSync(password, salt, 64);
    const expectedBuffer = Buffer.from(expected, 'hex');
    return expectedBuffer.length === actual.length
      && crypto.timingSafeEqual(expectedBuffer, actual);
  } catch {
    return false;
  }
}

export function createToken(shopId) {
  const payload = b64url(JSON.stringify({s: shopId, e: Date.now() + SESSION_MAX_AGE * 1000}));
  const signature = crypto.createHmac('sha256', secret()).update(payload).digest('base64url');
  return `${payload}.${signature}`;
}

export function verifyToken(token) {
  try {
    const [payload, signature] = String(token).split('.');
    if (!payload || !signature) return null;
    const expected = crypto.createHmac('sha256', secret()).update(payload).digest('base64url');
    const a = Buffer.from(signature);
    const b = Buffer.from(expected);
    if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
    const data = JSON.parse(Buffer.from(payload, 'base64url').toString('utf-8'));
    if (!data.s || !data.e || data.e < Date.now()) return null;
    return data.s;
  } catch {
    return null;
  }
}

export function shopIdFromRequest(request) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  return token ? verifyToken(token) : null;
}

export function setSession(response, shopId) {
  response.cookies.set(SESSION_COOKIE, createToken(shopId), {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: SESSION_MAX_AGE
  });
}

export function clearSession(response) {
  response.cookies.set(SESSION_COOKIE, '', {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: 0
  });
}

export function unauthorized() {
  return NextResponse.json({error: 'Authentification requise.'}, {status: 401});
}

export function requireShop(request) {
  return shopIdFromRequest(request);
}
