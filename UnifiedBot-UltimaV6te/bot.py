
import os, sqlite3, pandas as pd
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

DB_PATH = os.getenv("DB_PATH", "database.db")
MOD_CHANNEL_ID = int(os.getenv("MOD_CHANNEL_ID", "0"))  # قناة مراجعة التسليمات (اختياري)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- helpers ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_user(discord_id: str, username: str):
    con = db(); cur = con.cursor()
    cur.execute("SELECT id FROM users WHERE discord_id=?", (discord_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO users (discord_id, username) VALUES (?,?)", (discord_id, username))
        con.commit()
        uid = cur.lastrowid
    else:
        uid = row["id"]
        cur.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
        con.commit()
    con.close()
    return uid

async def send_to_mods(embed: discord.Embed, view: discord.ui.View|None=None):
    if MOD_CHANNEL_ID:
        ch = bot.get_channel(MOD_CHANNEL_ID)
        if ch:
            await ch.send(embed=embed, view=view)

def log(action, user_id, details=""):
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO logs (action, user_id, details) VALUES (?,?,?)", (action, user_id, details))
    con.commit(); con.close()

# ---------- UI: Approval buttons ----------
class ApproveRejectView(discord.ui.View):
    def __init__(self, submission_id: int):
        super().__init__(timeout=0)
        self.submission_id = submission_id

    @discord.ui.button(label="قبول ✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        # mark submission approved, reward user
        con = db(); cur = con.cursor()
        cur.execute("SELECT s.id, s.user_id, u.username FROM submissions s JOIN users u ON u.id=s.user_id WHERE s.id=?", (self.submission_id,))
        row = cur.fetchone()
        if not row:
            await interaction.response.send_message("❌ التسليم غير موجود.", ephemeral=True); con.close(); return

        cur.execute("UPDATE submissions SET status='approved' WHERE id=?", (self.submission_id,))

        # pricing
        cur.execute("SELECT type, value FROM pricing WHERE role_name='default' ORDER BY id DESC LIMIT 1")
        p = cur.fetchone()
        reward_msg = ""
        if p:
            if p["type"] == "points":
                cur.execute("UPDATE users SET points = points + ? , accepted_chapters = accepted_chapters + 1 WHERE id=?", (int(p["value"]), row["user_id"]))
                reward_msg = f"+{int(p['value'])} نقطة"
            else:
                cur.execute("UPDATE users SET balance = balance + ? , accepted_chapters = accepted_chapters + 1 WHERE id=?", (float(p["value"]), row["user_id"]))
                reward_msg = f"+{float(p['value']):.2f}$"

        # rank up simple thresholds
        cur.execute("SELECT accepted_chapters FROM users WHERE id=?", (row["user_id"],))
        ac = cur.fetchone()["accepted_chapters"]
        rank = "Member"
        if ac >= 30: rank = "Legend"
        elif ac >= 15: rank = "Pro"
        cur.execute("UPDATE users SET rank=? WHERE id=?", (rank, row["user_id"]))

        con.commit(); con.close()
        log("approve", row["user_id"], f"submission#{self.submission_id} {reward_msg}")
        await interaction.response.send_message(f"✅ تم القبول. تمت مكافأة المستخدم {reward_msg}.", ephemeral=True)

    @discord.ui.button(label="رفض ❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        con = db(); cur = con.cursor()
        cur.execute("SELECT id, user_id FROM submissions WHERE id=?", (self.submission_id,))
        row = cur.fetchone()
        if not row:
            await interaction.response.send_message("❌ التسليم غير موجود.", ephemeral=True); con.close(); return
        cur.execute("UPDATE submissions SET status='rejected' WHERE id=?", (self.submission_id,))
        con.commit(); con.close()
        log("reject", row["user_id"], f"submission#{self.submission_id}")
        await interaction.response.send_message("🚫 تم الرفض.", ephemeral=True)

# ---------- events ----------
@bot.event
async def on_ready():
    print(f"✅ Bot connected as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} slash commands")
    except Exception as e:
        print("Sync error:", e)
    cleanup_submissions.start()

# ---------- Slash Commands ----------
@bot.tree.command(name="ping", description="اختبار اتصال البوت")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! البوت شغال ✅")

@bot.tree.command(name="حضور", description="تسجيل حضورك الآن")
async def حضور(interaction: discord.Interaction):
    uid = ensure_user(str(interaction.user.id), str(interaction.user))
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO attendance (user_id) VALUES (?)", (uid,))
    con.commit(); con.close()
    log("attendance", uid, "presence marked")
    await interaction.response.send_message(f"✅ تم تسجيل حضورك يا {interaction.user.mention}")

@bot.tree.command(name="الحضور", description="عرض آخر 10 حضور")
async def الحضور(interaction: discord.Interaction):
    con = db(); cur = con.cursor()
    cur.execute("""
        SELECT u.username, a.timestamp FROM attendance a
        JOIN users u ON u.id=a.user_id
        ORDER BY a.id DESC LIMIT 10
    """)
    rows = cur.fetchall(); con.close()
    if not rows:
        await interaction.response.send_message("❌ لا يوجد أي حضور مسجّل بعد.")
        return
    msg = "📋 **آخر 10 حضور:**\n" + "\n".join([f"- {r['username']} في {r['timestamp']}" for r in rows])
    await interaction.response.send_message(msg)

@bot.tree.command(name="تصدير_الحضور", description="تصدير جميع الحضور كملف CSV (للأدمن فقط)")
async def تصدير_الحضور(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ هذا الأمر متاح فقط للأدمن.", ephemeral=True); return
    con = db()
    df = pd.read_sql_query("""SELECT u.username as العضو, a.timestamp as الوقت
                              FROM attendance a JOIN users u ON u.id=a.user_id
                              ORDER BY a.id DESC""", con)
    out = "attendance_export.csv"; df.to_csv(out, index=False, encoding="utf-8-sig")
    con.close()
    await interaction.response.send_message("📂 تفضل ملف الحضور:", file=discord.File(out))

@bot.tree.command(name="تسليم", description="إرسال فصل/محتوى للمراجعة")
@app_commands.describe(المحتوى="رابط أو نص الفصل")
async def تسليم(interaction: discord.Interaction, المحتوى: str):
    uid = ensure_user(str(interaction.user.id), str(interaction.user))
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO submissions (user_id, content) VALUES (?,?)", (uid, المحتوى))
    sid = cur.lastrowid; con.commit(); con.close()
    log("submit", uid, f"submission#{sid}")

    embed = discord.Embed(title="📥 تسليم جديد", description=المحتوى, color=0x2ecc71)
    embed.add_field(name="المستخدم", value=str(interaction.user), inline=False)
    embed.add_field(name="ID التسليم", value=str(sid), inline=True)
    view = ApproveRejectView(sid)
    await interaction.response.send_message("✅ تم إرسال تسليمك للمراجعة.")
    await send_to_mods(embed, view)

@bot.tree.command(name="سحب", description="طلب سحب رصيد")
@app_commands.describe(المبلغ="المبلغ المطلوب", الطريقة="وسيلة السحب (Binance/Bybit/Credit)")
async def سحب(interaction: discord.Interaction, المبلغ: float, الطريقة: str):
    uid = ensure_user(str(interaction.user.id), str(interaction.user))
    con = db(); cur = con.cursor()
    cur.execute("SELECT balance FROM users WHERE id=?", (uid,))
    bal = cur.fetchone()["balance"]
    if المبلغ <= 0 or المبلغ > bal:
        await interaction.response.send_message("❌ مبلغ غير صالح أو رصيد غير كافٍ.")
        con.close(); return
    cur.execute("INSERT INTO withdrawals (user_id, amount, method) VALUES (?,?,?)", (uid, المبلغ, الطريقة))
    cur.execute("UPDATE users SET balance = balance - ? WHERE id=?", (المبلغ, uid))
    con.commit(); con.close()
    log("withdraw_request", uid, f"{المبلغ} via {الطريقة}")
    await interaction.response.send_message("✅ تم إنشاء طلب السحب. سيتم مراجعته قريبًا.")

@bot.tree.command(name="ملفي", description="عرض ملفك الشخصي")
async def ملفي(interaction: discord.Interaction):
    uid = ensure_user(str(interaction.user.id), str(interaction.user))
    con = db(); cur = con.cursor()
    cur.execute("SELECT username, points, balance, accepted_chapters, rank, withdraw_method FROM users WHERE id=?", (uid,))
    u = cur.fetchone(); con.close()
    emb = discord.Embed(title="👤 ملفي", color=0x3498db)
    emb.add_field(name="الاسم", value=u["username"], inline=True)
    emb.add_field(name="النقاط", value=str(u["points"]), inline=True)
    emb.add_field(name="الرصيد", value=f"{u['balance']:.2f}$", inline=True)
    emb.add_field(name="فصول مقبولة", value=str(u["accepted_chapters"]), inline=True)
    emb.add_field(name="الرتبة", value=u["rank"], inline=True)
    await interaction.response.send_message(embed=emb)

@bot.tree.command(name="ترتيب", description="عرض أفضل 10 أعضاء")
async def ترتيب(interaction: discord.Interaction):
    con = db(); cur = con.cursor()
    cur.execute("SELECT username, accepted_chapters, points, balance, rank FROM users ORDER BY accepted_chapters DESC, points DESC LIMIT 10")
    rows = cur.fetchall(); con.close()
    if not rows:
        await interaction.response.send_message("لا توجد بيانات بعد."); return
    desc = "\n".join([f"**{i+1}. {r['username']}** — فصول: {r['accepted_chapters']} | نقاط: {r['points']} | رصيد: {r['balance']:.2f}$ | {r['rank']}" for i,r in enumerate(rows)])
    await interaction.response.send_message(f"🏆 **الترتيب:**\n{desc}")

# ----- Admin pricing -----
@bot.tree.command(name="تسعير", description="تحديث نوع المكافأة وقيمتها (أدمن)")
@app_commands.describe(النوع="points أو money", القيمة="قيمة المكافأة لكل قبول")
@app_commands.checks.has_permissions(administrator=True)
async def تسعير(interaction: discord.Interaction, النوع: str, القيمة: float):
    if النوع not in ("points", "money"):
        await interaction.response.send_message("❌ النوع يجب أن يكون points أو money.", ephemeral=True); return
    con = db(); cur = con.cursor()
    cur.execute("INSERT INTO pricing (type, value, role_name, updated_at) VALUES (?,?, 'default', datetime('now'))", (النوع, القيمة))
    con.commit(); con.close()
    log("pricing_update", 0, f"{النوع}={القيمة}")
    await interaction.response.send_message(f"✅ تم تحديث التسعير: {النوع} = {القيمة}")

# ----- Stats -----
@bot.tree.command(name="احصائيات", description="إحصائيات عامة")
async def احصائيات(interaction: discord.Interaction):
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(*) as c FROM users"); users = cur.fetchone()["c"]
    cur.execute("SELECT SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) p, SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) a, SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) r FROM submissions")
    s = cur.fetchone()
    cur.execute("SELECT COUNT(*) as w FROM withdrawals"); w = cur.fetchone()["w"]
    con.close()
    await interaction.response.send_message(f"👥 المستخدمون: {users}\n📥 التسليمات: قيد المراجعة {s['p'] or 0} | مقبولة {s['a'] or 0} | مرفوضة {s['r'] or 0}\n💸 السحوبات: {w}")

# ----- background cleanup -----
@tasks.loop(hours=1)
async def cleanup_submissions():
    # Example: nothing destructive, just a placeholder for future cleanup
    pass

# ---------- run ----------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ضع DISCORD_TOKEN في المتغيرات (Secrets).")
else:
    bot.run(TOKEN)
