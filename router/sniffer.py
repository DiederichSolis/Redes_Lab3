# sniffer.py
import json
import redis

r = redis.Redis(
    host='lab3.redesuvg.cloud', port=6379,
    username='default', password='UVGRedis2025',
    decode_responses=True
)
p = r.pubsub()
p.psubscribe('sec10.group7.*')
print("🔎 Escuchando Redis… (psubscribe 'sec10.group7.*')")

for msg in p.listen():
    if msg.get('type') == 'pmessage':
        ch = msg.get('channel')
        data = msg.get('data')
        try:
            obj = json.loads(data)
            print(f"\n📨 {ch} →")
            print(json.dumps(obj, indent=2, ensure_ascii=False))
        except Exception:
            print(f"\n📨 {ch} → {data}")
