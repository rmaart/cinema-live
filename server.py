import os, sqlite3, secrets, hashlib, jwt, schedule, time, threading, requests, re
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Cinema-Live API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_DIR = "/data" if os.path.exists("/data") else "."
DB_PATH = os.path.join(DB_DIR, "cinemalive.db")
os.makedirs(DB_DIR, exist_ok=True)

SECRET_KEY = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "cinema-admin-2025")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
ALGORITHM = "HS256"

class RegisterReq(BaseModel):
    email: str
    password: str

class SmartLinkReq(BaseModel):
    url: str
    content_type: str # 'full_movie' or 'trailer'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dict_from_row(row):
    if row is None: return None
    return {key: row[key] for key in row.keys()}

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, password_hash TEXT, tokens INTEGER DEFAULT 5, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS movies (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, year INTEGER, category TEXT, content_type TEXT, license_type TEXT, status TEXT DEFAULT 'approved', youtube_id TEXT, url TEXT, thumbnail TEXT, duration TEXT, direct_download TEXT, added_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, status TEXT, details TEXT, timestamp TEXT)''')
    
    count = c.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    if count == 0:
        now = datetime.now().isoformat()
        c.executemany('INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)', [
            ("Big Buck Bunny", 2008, "Animation", "full_movie", "CC BY", "aqz-KE-bpKQ", "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg", "PT10m", "stream_only", now),
            ("Night of the Living Dead", 1968, "Horror", "full_movie", "Public Domain", "", "https://archive.org/details/night_of_the_living_dead", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.jpg", "PT1h36m", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.mp4", now)
        ])
    conn.commit(); conn.close()

def log_agent(task, status, details):
    conn = get_db()
    conn.execute("INSERT INTO agent_logs (task, status, details, timestamp) VALUES (?,?,?,?)", (task, status, details[:500], datetime.now().isoformat()))
    conn.commit(); conn.close()

# --- SMART AI LOGIC ---
def analyze_link(link: str):
    """Analyzes a link and returns metadata"""
    data = {"title": "Unknown Movie", "thumbnail": "", "youtube_id": "", "direct_download": "", "url": link}
    
    # 1. YouTube Detection
    yt_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", link)
    if yt_match:
        vid = yt_match.group(1)
        data["youtube_id"] = vid
        data["thumbnail"] = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        data["direct_download"] = "stream_only"
        
        # Try to fetch title from oEmbed (No API Key needed!)
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
            resp = requests.get(oembed_url, timeout=3)
            if resp.status_code == 200:
                data["title"] = resp.json()["title"]
        except: pass
        
        return data

    # 2. Archive.org Detection
    if "archive.org" in link:
        data["direct_download"] = link # Fallback, user might need to find MP4
        data["thumbnail"] = "https://archive.org/images/glogo.png"
        # Simple title extraction from URL slug
        slug = link.split("/")[-1]
        data["title"] = slug.replace("_", " ").title()
        return data

    # 3. Direct MP4 Link
    if link.endswith(".mp4"):
        data["direct_download"] = link
        data["thumbnail"] = "https://via.placeholder.com/300x160?text=MP4+Video"
        data["title"] = link.split("/")[-1].replace(".mp4", "").replace("-", " ").title()
        return data

    return data

@app.on_event("startup")
def startup(): init_db()

@app.post("/api/auth/register")
def register(req: RegisterReq):
    if len(req.password) < 4: raise HTTPException(400, "Password too short")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO users (email,password_hash,tokens,created_at) VALUES (?,?,?,?)", (req.email, hashlib.sha256(req.password.encode()).hexdigest(), 5, datetime.now().isoformat()))
        uid = c.lastrowid; conn.commit()
        token = jwt.encode({"user_id": uid, "exp": datetime.utcnow() + timedelta(days=30)}, SECRET_KEY, ALGORITHM)
        return {"token": f"Bearer {token}", "tokens": 5}
    except sqlite3.IntegrityError: raise HTTPException(400, "Email already registered")
    finally: conn.close()

@app.get("/api/movies/full")
def get_full(): 
    conn = get_db()
    rows = conn.execute("SELECT * FROM movies WHERE content_type='full_movie' AND status='approved' ORDER BY id DESC").fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

@app.get("/api/movies/trailers")
def get_trailers(): 
    conn = get_db()
    rows = conn.execute("SELECT * FROM movies WHERE content_type='trailer' AND status='approved' ORDER BY id DESC").fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

@app.post("/api/movies/download/{mid}")
def dl(mid:int, uid:int=Depends(lambda h: jwt.decode(h.split()[1], SECRET_KEY, algorithms=[ALGORITHM])["user_id"] if h and h.startswith("Bearer ") else (_ for _ in ()).throw(HTTPException(401)))):
    conn=get_db()
    m=conn.execute("SELECT direct_download FROM movies WHERE id=?",(mid,)).fetchone()
    u=conn.execute("SELECT tokens FROM users WHERE id=?",(uid,)).fetchone()
    conn.close()
    if not m or m[0]=="stream_only": raise HTTPException(403,"Streaming only")
    if u[0] < 1: raise HTTPException(402,"Need 1 Token")
    return {"url": m[0]}

# --- ADMIN & AI ENDPOINTS ---

@app.post("/api/admin/smart-analyze")
def smart_analyze(req: SmartLinkReq, secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    result = analyze_link(req.url)
    result["content_type"] = req.content_type
    return result

@app.post("/api/admin/smart-add")
def smart_add(req: SmartLinkReq, secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    
    # Analyze first
    meta = analyze_link(req.url)
    
    conn = get_db(); c = conn.cursor()
    now = datetime.now().isoformat()
    
    c.execute('''INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) 
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
              (meta["title"], 2024, "AI-Added", req.content_type, "Auto", meta["youtube_id"], req.url, meta["thumbnail"], "PT0M", meta["direct_download"], now))
    
    conn.commit(); conn.close()
    log_agent("ai-add", "success", f"Added '{meta['title']}' via Smart Link")
    return {"status": "added", "title": meta["title"]}

@app.get("/api/admin/logs")
def get_logs(secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    conn = get_db()
    logs = conn.execute("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return [dict_from_row(l) for l in logs]

@app.post("/api/admin/trigger-agent")
def trigger_agent(secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    log_agent("manual", "triggered", "Agent manually triggered by admin")
    return {"status": "triggered"}

@app.get("/")
async def serve_frontend(): return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8000)
