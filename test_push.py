#!/usr/bin/env python3
"""Envoie une notification de test a tous les appareils abonnes (dont l'app est fermee)."""
import sqlite3, json, sys
from pywebpush import webpush, WebPushException

c = sqlite3.connect("produits.db")
rows = c.execute("select endpoint, subscription_json from push_subscriptions").fetchall()
if not rows:
    print("Aucun appareil abonne. Activez d'abord les alertes sur l'appareil.")
    sys.exit(1)
payload = json.dumps({
    "title": "Test — Stock",
    "body": "Si vous voyez ceci, les notifications arrivent meme app fermee.",
    "tag": "test-stock",
    "url": "/"
}, ensure_ascii=False)
sent = 0
for ep, sub in rows:
    try:
        r = webpush(subscription_info=json.loads(sub), data=payload,
                    vapid_private_key="push_private.pem",
                    vapid_claims={"sub": "mailto:admin@example.com"})
        print("OK", r.status_code, ep[:70])
        sent += 1
    except WebPushException as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        print("ECHEC", status, e)
print(f"{sent}/{len(rows)} envoi(s) accepte(s) par le service Push.")
