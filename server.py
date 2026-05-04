import os, sqlite3, secrets, hashlib, jwt, schedule, time, threading, requests
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
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "") # Optional: Add this in Render Env Vars for real search
ALGORITHM = "HS256"

class RegisterReq(BaseModel):
    email: str
    password: str

class MovieReq(BaseModel):
    title: str
    year: int
    category: str
    content_type: str
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
    c.execute('''CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT, status TEXT, details TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS ad_leads (id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT, domain TEXT, contact TEXT, status TEXT DEFAULT 'new', created_at TEXT)''')
    
    count = c.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    if count == 0:
        now = datetime.now().isoformat()
        movies = [
            ("Night of the Living Dead", 1968, "Horror", "full_movie", "Public Domain", "", "https://archive.org/details/night_of_the_living_dead", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.jpg", "PT1h36m", "https://archive.org/download/night_of_the_living_dead/night_of_the_living_dead.mp4", now),
            ("Big Buck Bunny", 2008, "Animation", "full_movie", "CC BY", "aqz-KE-bpKQ", "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "https://i.ytimg.com/vi/aqz-KE-bpKQ/hqdefault.jpg", "PT10m", "stream_only", now)
        ]
        c.executemany('INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) VALUES (?,?,?,?,?,?,?,?,?,?,?)', movies)
    conn.commit(); conn.close()

def log_agent(task, status, details):
    conn = get_db()
    conn.execute("INSERT INTO agent_logs (task, status, details, timestamp) VALUES (?,?,?,?)", (task, status, details[:500], datetime.now().isoformat()))
    conn.commit(); conn.close()

# --- THE AGENT CLASS ---
class CinemaBot:
    @staticmethod
    def hunt_movies():
        """Searches for Creative Commons movies"""
        log_agent("hunt", "started", "Searching YouTube for CC movies...")
        try:
            # If no API key, we simulate finding a movie for demo purposes
            if not YOUTUBE_API_KEY:
                log_agent("hunt", "info", "No YouTube API Key found. Using demo mode.")
                # Demo: Add a random public domain movie if not exists
                conn = get_db()
                if not conn.execute("SELECT id FROM movies WHERE title=?", ("The General",)).fetchone():
                    now = datetime.now().isoformat()
                    conn.execute('''INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) 
                                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                                 ("The General", 1926, "Comedy", "full_movie", "Public Domain", "kFbXqJZ_7wI", "https://www.youtube.com/watch?v=kFbXqJZ_7wI", "https://i.ytimg.com/vi/kFbXqJZ_7wI/hqdefault.jpg", "PT1h17m", "stream_only", now))
                    conn.commit()
                    log_agent("hunt", "success", "Added 'The General' (Demo Mode)")
                conn.close()
                return

            # Real API Search (If Key Provided)
            url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q=creative+commons+full+movie&type=video&videoDuration=long&maxResults=5&key={YOUTUBE_API_KEY}"
            response = requests.get(url)
            data = response.json()
            
            conn = get_db(); c = conn.cursor()
            added = 0
            for item in data.get('items', []):
                vid = item['id']['videoId']
                title = item['snippet']['title']
                thumb = item['snippet']['thumbnails']['high']['url']
                
                if not c.execute("SELECT id FROM movies WHERE youtube_id=?", (vid,)).fetchone():
                    c.execute('''INSERT INTO movies (title,year,category,content_type,license_type,youtube_id,url,thumbnail,duration,direct_download,added_date) 
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                             (title, 2023, "Indie", "full_movie", "Creative Commons", vid, f"https://www.youtube.com/watch?v={vid}", thumb, "PT90m", "stream_only", datetime.now().isoformat()))
                    added += 1
            
            conn.commit(); conn.close()
            log_agent("hunt", "success", f"Found and added {added} new movies via API")

        except Exception as e:
            log_agent("hunt", "error", str(e))

    @staticmethod
    def scout_advertisers():
        """Finds potential advertisers"""
        log_agent("scout", "started", "Scouting for tech & streaming companies...")
        try:
            # Demo Leads
            leads = [
                ("StreamFast VPN", "streamfast.io", "partners@streamfast.io"),
                ("CloudCinema", "cloudcinema.net", "ads@cloudcinema.net"),
                ("PopcornTech", "popcorntech.com", "biz@popcorntech.com")
            ]
            
            conn = get_db()
            for comp, dom, mail in leads:
                if not conn.execute("SELECT id FROM ad_leads WHERE domain=?", (dom,)).fetchone():
                    conn.execute("INSERT INTO ad_leads (company, domain, contact, created_at) VALUES (?,?,?,?)", (comp, dom, mail, datetime.now().isoformat()))
            conn.commit(); conn.close()
            log_agent("scout", "success", f"Added {len(leads)} new potential advertisers")
        except Exception as e:
            log_agent("scout", "error", str(e))

    @staticmethod
    def run_daily_cycle():
        print("🤖 CinemaBot: Starting daily cycle...")
        CinemaBot.hunt_movies()
        CinemaBot.scout_advertisers()
        print("🤖 CinemaBot: Cycle complete.")

# Start Scheduler in Background
def start_scheduler():
    schedule.every().day.at("08:00").do(CinemaBot.run_daily_cycle)
    # For testing, also run every 5 minutes
    schedule.every(5).minutes.do(CinemaBot.run_daily_cycle) 
    
    while True:
        schedule.run_pending()
        time.sleep(60)

threading.Thread(target=start_scheduler, daemon=True).start()

# --- APP STARTUP ---
@app.on_event("startup")
def startup(): 
    init_db()
    log_agent("system", "startup", "Cinema-Live initialized. Agent active.")

# --- USER ENDPOINTS ---
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

# --- ADMIN & AGENT ENDPOINTS ---
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
    threading.Thread(target=CinemaBot.run_daily_cycle).start()
    return {"status": "Agent triggered manually"}

@app.get("/")
async def serve_frontend(): return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8000)
