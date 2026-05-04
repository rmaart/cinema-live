import os, sqlite3, secrets, hashlib, jwt
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
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "cinema-admin-2025") # Your Admin Password
ALGORITHM = "HS256"

class RegisterReq(BaseModel):
    email: str
    password: str

class MovieReq(BaseModel):
    title: str
    year: int
    category: str
    content_type: str # 'full_movie' or 'trailer'
    license_type: str
    youtube_id: str = ""
    url: str = ""
    thumbnail: str = ""
    duration: str = ""
    direct_download: str = ""

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
    
    count = c.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    if count == 0:
        now = datetime.now().isoformat()
        movies = [
            ("Night of the Living Dead", 1968, "Horror", "full_movie", "Public Domain", "", "https://archive.org/details/night_of_the_living_dead", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.jpg", "PT1h36m", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.mp4", now),
            ("Big Buck Bunny", 2008, "Animation", "full_movie", "CC BY", "aqz-KE-bpKQ", "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg", "PT10m", "stream_only", now),
            ("Sintel", 2010, "Fantasy", "trailer", "CC BY", "eRsGyueVLvQ", "https://www.youtube.com/watch?v=eRsGyueVLvQ", "https://i.ytimg.com/vi/eRsGyueVLvQ/hqdefault.jpg", "PT15m", "stream_only", now)
        ]
        c.executemany('INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)', movies)
    conn.commit(); conn.close()

def verify_token(auth_header: str = Header(None)):
    if not auth_header or not auth_header.startswith("Bearer "): raise HTTPException(401, "Missing token")
    try: return jwt.decode(auth_header.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])["user_id"]
    except: raise HTTPException(401, "Invalid token")

def hash_pwd(p): return hashlib.sha256(p.encode()).hexdigest()

@app.on_event("startup")
def startup(): init_db()

@app.post("/api/auth/register")
def register(req: RegisterReq):
    if len(req.password) < 4: raise HTTPException(400, "Password too short")
    conn = get_db(); c = conn.cursor()
    try:
        c.execute("INSERT INTO users (email,password_hash,tokens,created_at) VALUES (?,?,?,?)", (req.email, hash_pwd(req.password), 5, datetime.now().isoformat()))
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
def dl(mid:int, uid:int=Depends(verify_token)):
    conn=get_db()
    m=conn.execute("SELECT direct_download FROM movies WHERE id=?",(mid,)).fetchone()
    u=conn.execute("SELECT tokens FROM users WHERE id=?",(uid,)).fetchone()
    conn.close()
    if not m or m[0]=="stream_only": raise HTTPException(403,"Streaming only")
    if u[0] < 1: raise HTTPException(402,"Need 1 Token")
    return {"url": m[0]}

# --- ADMIN ENDPOINTS ---

@app.get("/api/admin/movies")
def admin_get_movies(secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    conn = get_db()
    rows = conn.execute("SELECT * FROM movies ORDER BY id DESC").fetchall()
    conn.close()
    return [dict_from_row(r) for r in rows]

@app.post("/api/admin/movies")
def admin_add_movie(movie: MovieReq, secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    conn = get_db(); c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('''INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
              (movie.title, movie.year, movie.category, movie.content_type, movie.license_type, movie.youtube_id, movie.url, movie.thumbnail, movie.duration, movie.direct_download, now))
    conn.commit(); conn.close()
    return {"status": "added"}

@app.delete("/api/admin/movies/{mid}")
def admin_delete_movie(mid: int, secret: str = Header(None)):
    if secret != ADMIN_SECRET: raise HTTPException(403, "Unauthorized")
    conn = get_db()
    conn.execute("DELETE FROM movies WHERE id=?", (mid,))
    conn.commit(); conn.close()
    return {"status": "deleted"}

@app.get("/")
async def serve_frontend(): return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8000)
