
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
import sqlite3, os, requests, pandas as pd

app = FastAPI(title="Unified Discord Dashboard")

DB_PATH = os.getenv("DB_PATH", "database.db")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID","")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET","")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI","http://localhost:5000/oauth/callback")
WHITELIST_IDS = [x.strip() for x in os.getenv("WHITELIST_IDS","").split(",") if x.strip()]

def render(body: str) -> HTMLResponse:
    html = f"""
    <html lang="ar" dir="rtl">
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>لوحة التحكم</title>
    <style>
      body {{ font-family: Arial, sans-serif; padding: 20px; background:#f8fafc; }}
      .card {{ background:#fff; max-width:1100px; margin:auto; padding:20px; border-radius:14px; box-shadow:0 10px 20px rgba(0,0,0,.06);}}
      table {{ width:100%; border-collapse:collapse; }}
      th,td {{ padding:10px; border-bottom:1px solid #eee; text-align:right; }}
      th {{ background:#f3f4f6; }}
      a.btn {{ padding:8px 12px; border:1px solid #334155; border-radius:8px; text-decoration:none; color:#111827; }}
      .muted {{ color:#6b7280; font-size:14px }}
      .grid {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(260px,1fr)); gap:16px; }}
      .stat {{ background:#111827; color:#fff; padding:16px; border-radius:12px; }}
    </style></head>
    <body><div class="card">{body}</div></body></html>
    """
    return HTMLResponse(html)

def q(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()
    con.close()
    return rows

@app.get("/", response_class=HTMLResponse)
def home():
    login_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify"
    )
    body = f"""
    <h1>✅ لوحة التحكم تعمل</h1>
    <p class="muted">سجّل الدخول لمشاهدة البيانات.</p>
    <a class="btn" href="{login_url}">تسجيل الدخول عبر Discord</a>
    <hr/>
    <div class="grid">
      <div class="stat"><b>المستخدمون</b><br/>{q('SELECT COUNT(*) FROM users')[0][0]}</div>
      <div class="stat"><b>التسليمات</b><br/>{q('SELECT COUNT(*) FROM submissions')[0][0]}</div>
      <div class="stat"><b>السحوبات</b><br/>{q('SELECT COUNT(*) FROM withdrawals')[0][0]}</div>
      <div class="stat"><b>الحضور</b><br/>{q('SELECT COUNT(*) FROM attendance')[0][0]}</div>
    </div>
    <p class="muted">التحميل السريع: <a class="btn" href="/export/users">Users CSV</a> | <a class="btn" href="/export/submissions">Submissions CSV</a> | <a class="btn" href="/export/withdrawals">Withdrawals CSV</a> | <a class="btn" href="/export/attendance">Attendance CSV</a> | <a class="btn" href="/export/logs">Logs CSV</a></p>
    """
    return render(body)

@app.get("/login")
def login():
    return RedirectResponse(
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify"
    )

@app.get("/oauth/callback")
def oauth_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error":"no code"}, status_code=400)
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify"
    }
    token = requests.post("https://discord.com/api/oauth2/token", data=data, headers={"Content-Type":"application/x-www-form-urlencoded"}).json()
    if "access_token" not in token:
        return JSONResponse(token, status_code=400)
    me = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token['access_token']}"}).json()
    uid = me.get("id", "")
    if WHITELIST_IDS and uid not in WHITELIST_IDS:
        return render("<h2>🚫 غير مصرح بالدخول (ليست ضمن القائمة البيضاء)</h2>")
    resp = RedirectResponse("/dashboard")
    resp.set_cookie("uid", uid, httponly=True, samesite="lax")
    resp.set_cookie("uname", me.get("username",""), httponly=False, samesite="lax")
    return resp

def require_session(req: Request):
    uid = req.cookies.get("uid")
    if not uid: return False, "الرجاء تسجيل الدخول."
    if WHITELIST_IDS and uid not in WHITELIST_IDS: return False, "🚫 غير مصرح بالدخول."
    return True, uid

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    ok, msg = require_session(request)
    if not ok: return render(f"<h2>{msg}</h2><a class='btn' href='/login'>تسجيل الدخول</a>")
    sections = []
    for title, sql in [
        ("👥 المستخدمون", "SELECT id, username, points, balance, accepted_chapters, rank FROM users ORDER BY id DESC LIMIT 20"),
        ("📥 التسليمات (أحدث 20)", "SELECT id, user_id, content, status, created_at FROM submissions ORDER BY id DESC LIMIT 20"),
        ("💸 السحوبات (أحدث 20)", "SELECT id, user_id, amount, method, status, created_at FROM withdrawals ORDER BY id DESC LIMIT 20"),
        ("🕒 الحضور (أحدث 20)", "SELECT id, user_id, timestamp FROM attendance ORDER BY id DESC LIMIT 20"),
        ("📝 السجلات (أحدث 20)", "SELECT id, action, user_id, details, created_at FROM logs ORDER BY id DESC LIMIT 20"),
    ]:
        rows = q(sql)
        headers = [] ; data_rows = []
        if rows:
            colcount = len(rows[0])
            headers = [desc[0] for desc in sqlite3.connect(DB_PATH).execute(sql.replace("LIMIT 20"," LIMIT 1")).description or []]
            data_rows = rows
        table = "<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>"
        for r in data_rows:
            table += "<tr>" + "".join(f"<td>{v}</td>" for v in r) + "</tr>"
        table += "</tbody></table>"
        sections.append(f"<h2>{title}</h2>{table}")
    body = "<h1>لوحة التحكم</h1>" + "".join(sections) + "<hr/><p class='muted'>التصدير: " \
           "<a class='btn' href='/export/users'>Users</a> " \
           "<a class='btn' href='/export/submissions'>Submissions</a> " \
           "<a class='btn' href='/export/withdrawals'>Withdrawals</a> " \
           "<a class='btn' href='/export/attendance'>Attendance</a> " \
           "<a class='btn' href='/export/logs'>Logs</a></p>"
    return render(body)

def export_csv(filename: str, df: pd.DataFrame):
    out = f"{filename}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return FileResponse(out, filename=out)

@app.get("/export/users")
def export_users():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM users", con); con.close()
    return export_csv("users", df)

@app.get("/export/submissions")
def export_submissions():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM submissions", con); con.close()
    return export_csv("submissions", df)

@app.get("/export/withdrawals")
def export_withdrawals():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM withdrawals", con); con.close()
    return export_csv("withdrawals", df)

@app.get("/export/attendance")
def export_attendance():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM attendance", con); con.close()
    return export_csv("attendance", df)

@app.get("/export/logs")
def export_logs():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM logs", con); con.close()
    return export_csv("logs", df)
