"""
Lonely Sniper — Game Server  v2.0
===================================
Fully HTTP-based. Works on ANY cloud host (Railway, Render, Fly.io, VPS).
Uses only Python stdlib — zero pip installs needed.

DEPLOY FREE IN 5 MINUTES:
  1. Go to https://railway.app  →  New Project  →  Deploy from GitHub Gist
     OR just drag-and-drop this file at https://railway.app/new
  2. Set start command: python game_server.py
  3. Copy the public URL Railway gives you  (e.g. https://xyz.up.railway.app)
  4. Paste it in sniper.py:  SERVER_URL = "https://xyz.up.railway.app"
  5. Share that sniper.py with friends — everyone auto-connects, no IP needed!
"""
import json, time, os, re, hashlib, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict
import random, string, datetime, urllib.parse

PORT = int(os.environ.get("PORT", 8080))

_BAD = {"fuck","shit","bitch","cunt","nigger","nigga","faggot","kike","spic",
        "chink","gook","retard","rape","nazi","kys","wetback","cracker"}
def _bad(text):
    t = re.sub(r"[^a-z0-9 ]","",text.lower())
    return any(b in t for b in _BAD)

DATA_FILE = "server_data.json"
_lock = threading.Lock()

class State:
    def __init__(self):
        self.rooms = {}; self.chat = []; self.leaderboard = []
        self.players = {}; self.violations = defaultdict(int)
        self.bans = {}; self.ban_counts = defaultdict(int)
        self._seq = 0
        self.load()

    def load(self):
        if not os.path.exists(DATA_FILE): return
        try:
            d = json.load(open(DATA_FILE))
            self.leaderboard = d.get("leaderboard",[])[:200]
            self.chat = d.get("chat",[])[-200:]
            self._seq = max((m.get("id",0) for m in self.chat), default=0)
        except Exception: pass

    def save(self):
        try:
            json.dump({"leaderboard":self.leaderboard[:200],"chat":self.chat[-200:]},
                      open(DATA_FILE,"w"), indent=2)
        except Exception: pass

    def push_chat(self, frm, msg, system=False):
        self._seq += 1
        e = {"id":self._seq,"from":frm,"msg":msg,
             "ts":datetime.datetime.utcnow().strftime("%H:%M"),
             "system":system}
        self.chat.append(e)
        if len(self.chat)>200: self.chat.pop(0)
        self.save(); return e

    def since(self, sid): return [m for m in self.chat if m.get("id",0)>sid]

    def add_score(self, e):
        self.leaderboard.append(e)
        self.leaderboard.sort(key=lambda x:(-x.get("waves",0),-x.get("kills",0)))
        self.leaderboard=self.leaderboard[:200]; self.save()

    def is_banned(self, n):
        exp=self.bans.get(n,0); return time.time()<exp, max(0,exp-time.time())

    def violate(self, n):
        self.violations[n]+=1; v=self.violations[n]
        if v>=3:
            self.ban_counts[n]+=1; bc=self.ban_counts[n]
            dur=300*(2**(bc-1)); self.bans[n]=time.time()+dur
            self.violations[n]=0
            self.push_chat("SYSTEM",f"⛔ {n} banned {dur//60}min (#{bc})",True)
            return "banned",dur
        self.push_chat("SYSTEM",f"⚠ {n} violation {v}/3",True)
        return "warned",v

    def prune(self):
        now=time.time()
        for n in [k for k,d in self.players.items() if now-d["last_seen"]>300]: del self.players[n]
        for rid in [r for r,d in self.rooms.items() if not d["players"] or now-d.get("la",now)>600]: del self.rooms[rid]

S = State()

def _rid(): return "".join(random.choices(string.ascii_uppercase+string.digits,k=6))
def _ok(d=None): return {"ok":True,**(d or {})}
def _err(r): return {"ok":False,"reason":r}
def _pub(r):
    o={k:v for k,v in r.items() if k!="password_hash"}
    o["has_password"]=bool(r.get("password_hash")); return o

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _body(self):
        n=int(self.headers.get("Content-Length",0))
        return json.loads(self.rfile.read(n)) if n else {}
    def _qs(self): return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
    def _send(self,d,s=200):
        b=json.dumps(d).encode()
        self.send_response(s); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(b)); self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(b)
    def do_OPTIONS(self):
        self.send_response(200); self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type"); self.end_headers()

    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path; qs=self._qs()
        with _lock:
            if p=="/ping": self._send(_ok({"players":len(S.players),"rooms":len(S.rooms)}))
            elif p=="/rooms":
                S.prune()
                pub=[_pub(r) for r in S.rooms.values() if not r.get("started") and not r.get("private")]
                prv=[_pub(r) for r in S.rooms.values() if not r.get("started") and r.get("private")]
                self._send(_ok({"public":pub,"private":prv,"total_online":len(S.players)}))
            elif p=="/rooms/state":
                rid=qs.get("room_id",""); r=S.rooms.get(rid)
                if not r: self._send(_err("Room not found")); return
                self._send(_ok({"room":_pub(r),"chat":S.since(int(qs.get("since",0))),"seq":S._seq}))
            elif p=="/chat/poll": self._send(_ok({"messages":S.since(int(qs.get("since",0))),"seq":S._seq}))
            elif p=="/score/leaderboard": self._send(_ok({"data":S.leaderboard[:50]}))
            else: self._send({"error":"not found"},404)

    def do_POST(self):
        p=urllib.parse.urlparse(self.path).path
        try: body=self._body()
        except Exception: self._send(_err("Bad JSON")); return
        with _lock:
            if p=="/register":
                name=str(body.get("name","")).strip()[:20]
                if not name or _bad(name): self._send(_err("Invalid or offensive name")); return
                S.players[name]={"last_seen":time.time()}
                self._send(_ok({"name":name,"online":len(S.players),"seq":S._seq}))
            elif p=="/rooms/create":
                name=str(body.get("player_name","")).strip()
                if not name: self._send(_err("No name")); return
                rid=_rid(); pw=str(body.get("password","")).strip()
                private=bool(body.get("private",bool(pw)))
                room={"room_id":rid,"host":name,"max_players":min(4,max(2,int(body.get("max_players",4)))),
                      "difficulty":body.get("difficulty","normal"),"private":private,
                      "password_hash":hashlib.sha256(pw.encode()).hexdigest() if pw else "",
                      "players":{name:{"kills":0,"money":0,"wave":0}},"started":False,"la":time.time()}
                S.rooms[rid]=room
                S.push_chat("SYSTEM",f"🏠 {name} created {'private' if private else 'public'} room [{rid}]",True)
                self._send(_ok({"room":_pub(room)}))
            elif p=="/rooms/join":
                rid=str(body.get("room_id","")); name=str(body.get("player_name","")).strip()
                pw=str(body.get("password","")); r=S.rooms.get(rid)
                if not r: self._send(_err("Room not found")); return
                if r["started"]: self._send(_err("Game already started")); return
                if len(r["players"])>=r["max_players"]: self._send(_err("Room is full")); return
                if r.get("password_hash") and hashlib.sha256(pw.encode()).hexdigest()!=r["password_hash"]:
                    self._send(_err("Wrong password")); return
                r["players"][name]={"kills":0,"money":0,"wave":0}; r["la"]=time.time()
                S.push_chat("SYSTEM",f"👤 {name} joined room [{rid}]",True)
                self._send(_ok({"room":_pub(r)}))
            elif p=="/rooms/leave":
                rid=str(body.get("room_id","")); name=str(body.get("player_name",""))
                r=S.rooms.get(rid)
                if r:
                    r["players"].pop(name,None)
                    if not r["players"]: del S.rooms[rid]
                    elif r["host"]==name: r["host"]=next(iter(r["players"]))
                self._send(_ok())
            elif p=="/rooms/start":
                rid=str(body.get("room_id","")); name=str(body.get("player_name",""))
                r=S.rooms.get(rid)
                if not r: self._send(_err("Room not found")); return
                if r["host"]!=name: self._send(_err("Not host")); return
                if len(r["players"])<2: self._send(_err("Need at least 2 players")); return
                r["started"]=True; r["la"]=time.time()
                S.push_chat("SYSTEM",f"🎮 Game started in room [{rid}]",True)
                self._send(_ok({"room":_pub(r)}))
            elif p=="/rooms/sync":
                rid=str(body.get("room_id","")); name=str(body.get("player_name",""))
                r=S.rooms.get(rid)
                if r and name in r["players"]:
                    r["players"][name]={"kills":int(body.get("kills",0)),
                                        "money":int(body.get("money",0)),"wave":int(body.get("wave",0))}
                    r["la"]=time.time()
                self._send(_ok())
            elif p=="/chat/send":
                name=str(body.get("player_name","")).strip(); msg=str(body.get("msg","")).strip()[:200]
                if not name or not msg: self._send(_err("Missing data")); return
                banned,rem=S.is_banned(name)
                if banned: self._send(_err(f"Banned. {int(rem)}s left.")); return
                if _bad(msg):
                    res,val=S.violate(name)
                    self._send(_err(f"Blocked. {'Banned '+str(int(val//60))+'min' if res=='banned' else 'Warning '+str(val)+'/3'}")); return
                e=S.push_chat(name,msg); self._send(_ok({"message":e}))
            elif p=="/score/submit":
                e={"name":str(body.get("name","?"))[:20],"kills":int(body.get("kills",0)),
                   "waves":int(body.get("waves",0)),"money":int(body.get("money",0)),
                   "difficulty":str(body.get("difficulty","normal")),"mode":str(body.get("mode","apoc")),
                   "date":datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")}
                S.add_score(e); self._send(_ok())
            else: self._send({"error":"not found"},404)

def _pruner():
    while True:
        time.sleep(60)
        with _lock: S.prune()
threading.Thread(target=_pruner,daemon=True).start()

if __name__=="__main__":
    srv=HTTPServer(("0.0.0.0",PORT),H)
    print(f"[SERVER] Lonely Sniper v2.0 running on http://0.0.0.0:{PORT}")
    print(f"[SERVER] Deploy free: https://railway.app")
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\n[SERVER] Stopped.")
