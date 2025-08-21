# UnifiedBot-Ultimate

بوت ديسكورد + لوحة تحكم FastAPI + قاعدة بيانات SQLite.

## المتغيرات (Secrets)
- DISCORD_TOKEN
- DISCORD_CLIENT_ID
- DISCORD_CLIENT_SECRET
- DISCORD_REDIRECT_URI  (مثال: https://your-app.onrender.com/oauth/callback)
- WHITELIST_IDS         (IDs مفصولة بفواصل)
- MOD_CHANNEL_ID        (ID قناة مراجعة التسليمات)

## تشغيل محلي
pip install -r requirements.txt
python bot.py
uvicorn app.main:app --host 0.0.0.0 --port 5000

## Render
- الملف render.yaml و Procfile جهزين.
- اربط GitHub → Deploy.

## Replit
- الملف .replit جاهز للتشغيل المتوازي (البوت + الداشبورد).
