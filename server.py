import os, sqlite3, secrets, hashlib, jwt, schedule, time, threading
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Cinema-Live API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = os.getenv("DB_PATH", "/data/cinemalive.db" if os.path.exists("/data") else "cinemalive.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
SECRET_KEY = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "cinema-admin-2025")
ALGORITHM = "HS256"

# --- Models for Strict Validation ---
class RegisterReq(BaseModel):
    email: str
    password: str

class LoginReq(BaseModel):
    email: str
    password: str

def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, password_hash TEXT, trial_start TEXT, tokens INTEGER DEFAULT 5, created_at TEXT);
    CREATE TABLE IF NOT EXISTS token_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount INTEGER, reason TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS movies (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, year INTEGER, category TEXT, content_type TEXT, license_type TEXT, status TEXT DEFAULT 'approved', youtube_id TEXT, url TEXT, thumbnail TEXT, duration TEXT, direct_download TEXT, added_date TEXT);
    CREATE TABLE IF NOT EXISTS ads (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, creative_url TEXT, target_category TEXT, status TEXT DEFAULT 'active', interaction_type TEXT DEFAULT 'hold', duration_sec INTEGER DEFAULT 5, token_reward INTEGER DEFAULT 3, views INTEGER DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS ad_interactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, ad_id INTEGER, completed BOOLEAN, timestamp TEXT);
    ''')
    try: c.execute("INSERT OR IGNORE INTO users (email, password_hash, trial_start, tokens, created_at) VALUES ('demo@cinema.live', 'demo', ?, 99, ?)", (datetime.now().isoformat(), datetime.now().isoformat()))
    except: pass
    
    # Seed Movies if empty
    if c.execute("SELECT COUNT(*) FROM movies").fetchone()[0] == 0:
        now = datetime.now().isoformat()
        c.executemany('INSERT INTO movies (title,year,category,content_type,license_type,status,youtube_id,url,thumbnail,duration,direct_download,added_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', [
            ("Night of the Living Dead", 1968, "Horror", "full_movie", "Public Domain", "approved", "", "https://archive.org/details/night_of_the_living_dead", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.jpg", "PT1h36m", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.mp4", now),
            ("Big Buck Bunny", 2008, "Animation", "full_movie", "CC BY", "approved", "aqz-KE-bpKQ", "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg", "PT10m", "stream_only", now)
        ])
    conn.commit(); conn.close()

def log(task, status, details): pass # Simplified for brevity

def verify_token(auth_header: str = Header(None)):
    if not auth_header or not auth_header.startswith("Bearer "): raise HTTPException(401, "Missing token")
    try: return jwt.decode(auth_header.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])["user_id"]
    except: raise HTTPException(401, "Invalid token")

def hash_pwd(p): return hashlib.sha256(p.encode()).hexdigest()

@app.on_event("startup")
def startup(): init_db()

# --- Auth Endpoints (Email Based) ---
@app.post("/api/auth/register")
def register(req: RegisterReq):
    if len(req.password) < 4: raise HTTPException(400, "Password too short")
    if "@" not in req.email: raise HTTPException(400, "Invalid email")
    
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute("INSERT INTO users (email,password_hash,trial_start,tokens,created_at) VALUES (?,?,?,?,?)", 
                  (req.email, hash_pwd(req.password), datetime.now().isoformat(), 5, datetime.now().isoformat()))
        uid = c.lastrowid
        conn.commit()
        token = jwt.encode({"user_id": uid, "exp": datetime.utcnow() + timedelta(days=30)}, SECRET_KEY, ALGORITHM)
        return {"token": f"Bearer {token}", "tokens": 5, "trial": True}
    except sqlite3.IntegrityError: raise HTTPException(400, "Email already registered")
    finally: conn.close()

@app.post("/api/auth/login")
def login(req: LoginReq):
    conn = sqlite3.connect(DB_PATH)
    u = conn.execute("SELECT id,password_hash,tokens FROM users WHERE email=?", (req.email,)).fetchone()
    conn.close()
    if not u or u[1] != hash_pwd(req.password): raise HTTPException(401, "Invalid credentials")
    return {"token": f"Bearer {jwt.encode({'user_id':u[0],'exp':datetime.utcnow()+timedelta(days=30)},SECRET_KEY,ALGORITHM)}", "tokens": u[2]}

@app.get("/api/user/status")
def status(uid:int=Depends(verify_token)):
    conn=sqlite3.connect(DB_PATH); r=conn.execute("SELECT tokens FROM users WHERE id=?",(uid,)).fetchone(); conn.close()
    return {"tokens": r[0] if r else 0}

# --- Media Endpoints ---
@app.get("/api/movies/full")
def get_full(): 
    return [dict(r) for r in sqlite3.connect(DB_PATH).execute("SELECT * FROM movies WHERE content_type='full_movie' AND status='approved' ORDER BY added_date DESC LIMIT 20").fetchall()]

@app.get("/api/movies/trailers")
def get_trailers(): 
    return [dict(r) for r in sqlite3.connect(DB_PATH).execute("SELECT * FROM movies WHERE content_type='trailer' AND status='approved' ORDER BY added_date DESC LIMIT 20").fetchall()]

# --- Download Endpoint (Requires Auth) ---
@app.post("/api/movies/download/{mid}")
def dl(mid:int, uid:int=Depends(verify_token)):
    conn=sqlite3.connect(DB_PATH)
    m=conn.execute("SELECT direct_download,license_type FROM movies WHERE id=?",(mid,)).fetchone()
    u=conn.execute("SELECT tokens FROM users WHERE id=?",(uid,)).fetchone()
    
    if not m or m[0]=="stream_only": 
        conn.close(); raise HTTPException(403,"Streaming only")
    
    if u[0] < 1:
        conn.close(); raise HTTPException(402,"Need 1 Token to download")
        
    # Deduct Token
    conn.execute("UPDATE users SET tokens=tokens-1 WHERE id=?",(uid,))
    conn.execute("INSERT INTO token_ledger (user_id,amount,reason,created_at) VALUES (?,?,?,?)",(uid,-1,"Download",datetime.now().isoformat()))
    conn.commit(); conn.close()
    
    return {"url":m[0],"note":m[1]}

@app.get("/")
async def serve_frontend(): return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn; port=int(os.getenv("PORT", 8000)); uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
