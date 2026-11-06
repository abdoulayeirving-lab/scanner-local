import {Pool} from 'pg';

const globalForPool = globalThis;

function connectionString() {
  return process.env.STOCK_POSTGRES_URL
    || process.env.STOCK_DATABASE_URL
    || process.env.POSTGRES_URL
    || process.env.DATABASE_URL
    || '';
}

function getPool() {
  if (!globalForPool.__stockPool) {
    const url = connectionString();
    if (!url) {
      throw new Error('Base de données non configurée (STOCK_POSTGRES_URL manquant).');
    }
    const sslRequired = /sslmode=(require|verify-ca|verify-full)/.test(url) || /neon\.tech/.test(url);
    globalForPool.__stockPool = new Pool({
      connectionString: url,
      ssl: sslRequired ? {rejectUnauthorized: false} : false,
      max: 5
    });
  }
  return globalForPool.__stockPool;
}

const STATEMENTS = [
  `CREATE TABLE IF NOT EXISTS stock_shops(
     id SERIAL PRIMARY KEY,
     name TEXT NOT NULL,
     email TEXT UNIQUE,
     password_hash TEXT,
     google_sub TEXT UNIQUE,
     created TIMESTAMPTZ NOT NULL DEFAULT now()
   )`,
  `CREATE TABLE IF NOT EXISTS stock_products(
     id SERIAL PRIMARY KEY,
     shop_id INTEGER NOT NULL REFERENCES stock_shops(id) ON DELETE CASCADE,
     code TEXT NOT NULL,
     name TEXT,
     price DOUBLE PRECISION DEFAULT 0,
     qty INTEGER DEFAULT 0,
     updated TEXT,
     expiration_date TEXT,
     UNIQUE(shop_id, code)
   )`,
  `CREATE TABLE IF NOT EXISTS stock_push_subscriptions(
     endpoint TEXT PRIMARY KEY,
     subscription_json TEXT NOT NULL,
     created TEXT NOT NULL,
     shop_id INTEGER NOT NULL REFERENCES stock_shops(id) ON DELETE CASCADE
   )`,
  `CREATE TABLE IF NOT EXISTS stock_expiry_alerts(
     product_id INTEGER NOT NULL,
     expiration_date TEXT NOT NULL,
     kind TEXT NOT NULL,
     sent_at TEXT NOT NULL,
     PRIMARY KEY(product_id, expiration_date, kind)
   )`
];

let schemaReady = null;

export async function query(text, params) {
  if (!schemaReady) {
    schemaReady = (async () => {
      const pool = getPool();
      for (const statement of STATEMENTS) {
        await pool.query(statement);
      }
    })().catch(error => {
      schemaReady = null;
      throw error;
    });
  }
  await schemaReady;
  return getPool().query(text, params);
}
