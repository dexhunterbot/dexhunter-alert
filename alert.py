"""
DexHunter alert bot -> Telegram
- Big buy/sell fo9 MIN_ADA (w limites khassa l kol token)
- MCAP + Chart f kol alert
- 🔥 ila kanu 3 big buys wla kter f nafs token f 10 d9ay9
- Wallet: notif kol ±10% mn entry, m3a profit/loss
- Commands f Telegram: /price /wallet /min /entry /help
- Résumé kol lil
"""
import html
import json
import math
import re
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("TZ_NAME", "Africa/Casablanca"))
except Exception:
    TZ = timezone.utc

API = "https://api-us.dexhunterv3.app"
PUB = "https://dhapi.io"
KOIOS = "https://api.koios.rest/api/v1"
KEY = os.environ.get("DEXHUNTER_KEY", "").strip()
TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT = os.environ.get("TG_CHAT_ID", "").strip()
WALLET = os.environ.get("WALLET_ADDR", "").strip()
DISCORD = os.environ.get("DISCORD_WEBHOOK", "").strip()
MIN_ADA = float(os.environ.get("MIN_ADA", "10000"))
POLL_SEC = int(os.environ.get("POLL_SEC", "30"))
RUN_MINUTES = int(os.environ.get("RUN_MINUTES", "0"))
LOOKBACK_MIN = int(os.environ.get("LOOKBACK_MIN", "75"))
BIG_MOVE = float(os.environ.get("BIG_MOVE_PCT", "10"))
CLUSTER_N = int(os.environ.get("CLUSTER_N", "3"))
CLUSTER_MIN = int(os.environ.get("CLUSTER_MIN", "10"))
STREAK_N = int(os.environ.get("STREAK_N", "5"))
STREAK_WINDOW_SEC = int(os.environ.get("STREAK_WINDOW_SEC", "120"))
STREAK_COOLDOWN_MIN = int(os.environ.get("STREAK_COOLDOWN_MIN", "30"))
WALLET_STEP = float(os.environ.get("STEP_PCT", "10"))
WALLET_EVERY = int(os.environ.get("WALLET_EVERY_SEC", "120"))
WALLET_MIN_VALUE = float(os.environ.get("MIN_VALUE_ADA", "20"))
SUMMARY_HOUR = int(os.environ.get("SUMMARY_HOUR", "23"))
TEST = os.environ.get("TEST_MODE", "false").lower() == "true"
COMMANDS = os.environ.get("COMMANDS", "1") != "0"  # 0 = bla commands (ila bot mchtarak)
SEEN_FILE = "seen.json"
STATE_FILE = "state.json"

ADA_IDS = {"", "ada", "lovelace", "000000000000000000000000000000000000000000000000000000006c6f76656c616365"}
H = {"X-Partner-Id": KEY, "Content-Type": "application/json", "Accept": "application/json"}
TS_FIELDS = ("submission_time", "created_at", "createdAt", "update_time", "last_update")
S = requests.Session()


# =============== helpers ===============
def num(v):
    try:
        return float(v)
    except Exception:
        return None


def g(o, *names, default=None):
    for n in names:
        if isinstance(o, dict) and o.get(n) not in (None, ""):
            return o[n]
    return default


def is_ada(tid):
    return (tid or "").lower() in ADA_IDS


def to_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def ada_amount(v):
    v = num(v)
    if v is None:
        return 0.0
    return v / 1_000_000 if v > 1e11 else v


def kfmt(x):
    if x is None:
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"{int(x / div * 100 + (1e-6 if x > 0 else -1e-6)) / 100:g}{suf}"
    return f"{x:,.0f}" if abs(x) >= 10 else f"{x:.2g}"


def pfmt(p):
    if p is None:
        return "?"
    if p >= 1:
        return f"{p:,.2f}"
    d = -math.floor(math.log10(p)) + 2 if p > 0 else 2
    return f"{p:.{d}f}"


def pct(x):
    return "?" if x is None else f"{x:+.1f}%"


def now_local():
    return datetime.now(TZ)


# =============== telegram ===============
def html_to_md(text):
    t = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"[\2](<\1>)", text)
    t = re.sub(r"</?b>", "**", t)
    t = re.sub(r"</?i>", "*", t)
    t = re.sub(r"</?code>", "`", t)
    return t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def discord(text):
    if not DISCORD:
        return
    for _ in range(2):
        try:
            r = S.post(DISCORD, json={"content": html_to_md(text)[:1990], "flags": 4,
                                      "allowed_mentions": {"parse": []}}, timeout=20)
            if r.status_code == 429:
                time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 10))
                continue
            if not r.ok:
                print("Discord error:", r.status_code, r.text[:200])
            return
        except Exception as e:
            print("Discord exception:", e)
            return


def tg(text, chat=None, mirror=True):
    if mirror:
        discord(text)
    try:
        r = S.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                   json={"chat_id": chat or TG_CHAT, "text": text, "parse_mode": "HTML",
                         "disable_web_page_preview": True}, timeout=20)
        if not r.ok:
            print("Telegram error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram exception:", e)


def tg_updates(offset, timeout):
    try:
        r = S.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                  params={"offset": offset, "timeout": int(timeout), "allowed_updates": '["message"]'},
                  timeout=timeout + 15)
        if r.ok:
            return r.json().get("result", [])
        print("getUpdates error:", r.status_code, r.text[:200])
        time.sleep(min(timeout, 5))
    except Exception as e:
        print("getUpdates exception:", e)
        time.sleep(min(timeout, 5))
    return []


# =============== token data ===============
_names = {}


def token_name(tid):
    if tid in _names:
        return _names[tid]
    name = tid[:10]
    try:
        n2 = bytes.fromhex(tid[56:]).decode("utf-8").strip("\x00")
        if n2 and n2.isprintable():
            name = n2
    except Exception:
        pass
    for url, hdr in ((f"{API}/swap/token/{tid}", H), (f"{PUB}/swap/token/{tid}", {})):
        try:
            r = S.get(url, headers=hdr, timeout=15)
            if r.ok:
                name = g(r.json(), "ticker", "token_ascii", default=name)
                break
        except Exception:
            pass
    _names[tid] = name or tid[:10]
    return _names[tid]


_dex = {}


def dexscreener(tids):
    """{tid: {"url","mcap_ada","price","h1","h24","liq_ada"}} (cache 60s)."""
    out, need = {}, []
    for t in tids:
        c = _dex.get(t)
        if c and time.time() - c[0] < 60:
            out[t] = c[1]
        else:
            need.append(t)
    for i in range(0, len(need), 30):
        chunk = need[i:i + 30]
        best = {}
        try:
            r = S.get("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk), timeout=20)
            pairs = (r.json().get("pairs") or []) if r.ok else []
        except Exception as e:
            print("DexScreener error:", e)
            pairs = []
        for p in pairs:
            if p.get("chainId") != "cardano":
                continue
            t = (p.get("baseToken") or {}).get("address")
            if t not in chunk:
                continue
            liq = num((p.get("liquidity") or {}).get("usd")) or 0
            if t not in best or liq > best[t][0]:
                best[t] = (liq, p)
        for t in chunk:
            info = None
            if t in best:
                p = best[t][1]
                pn, pu = num(p.get("priceNative")), num(p.get("priceUsd"))
                mc = num(p.get("marketCap")) or num(p.get("fdv"))
                rate = (pn / pu) if pn and pu else None  # ADA l kol $
                pc = p.get("priceChange") or {}
                info = {"url": p.get("url"), "price": pn,
                        "mcap_ada": mc * rate if mc and rate else None,
                        "h1": num(pc.get("h1")), "h24": num(pc.get("h24")),
                        "liq_ada": num((p.get("liquidity") or {}).get("quote"))}
            _dex[t] = (time.time(), info)
            out[t] = info
    return out


def dex1(tid):
    return dexscreener([tid]).get(tid)


def find_token(q):
    """Qelleb 3la token b ticker (SNEK) wla b CA."""
    q = q.strip().lstrip("$")
    if len(q) >= 56 and all(c in "0123456789abcdefABCDEF" for c in q):
        return q.lower(), token_name(q.lower())
    try:
        r = S.get(f"{PUB}/swap/tokens", params={"query": q}, timeout=15)
        lst = r.json() if r.ok else []
    except Exception:
        lst = []
    if not isinstance(lst, list) or not lst:
        return None, None
    qu = q.upper()
    exact = [t for t in lst if str(t.get("ticker", "")).upper() == qu]
    pick = (sorted(exact, key=lambda t: not t.get("is_verified")) or lst)[0]
    tid = pick.get("token_id")
    _names[tid] = pick.get("ticker") or pick.get("token_ascii") or tid[:10]
    return tid, _names[tid]


_pools = {}


def pool_ada(tid):
    c = _pools.get(tid)
    if c and time.time() - c[0] < 30:
        return c[1]
    total = 0.0
    for url, hdr in ((f"{API}/stats/pools/ADA/{tid}", H), (f"{PUB}/stats/pools/ADA/{tid}", {})):
        try:
            r = S.get(url, headers=hdr, timeout=15)
            if r.ok and isinstance(r.json(), list):
                for p in r.json():
                    if p.get("pool_id"):
                        total += num(p.get("token_1_amount")) or 0
                break
        except Exception:
            pass
    _pools[tid] = (time.time(), total)
    return total or None


def price_move(kind, ada, tok, o):
    v = num(g(o, "price_impact", "priceImpact", "impact"))
    if v is not None:
        return abs(v * 100 if abs(v) < 1 else v)
    R = pool_ada(tok)
    if not R or not ada:
        return None
    if kind == "buy":
        before = max(R - ada, R * 0.05)
        return min(((R / before) ** 2 - 1) * 100, 999)
    before = R + ada
    return (1 - (R / before) ** 2) * 100


def price_ada(tid):
    for url, hdr in ((f"{API}/swap/averagePrice/ADA/{tid}", H), (f"{PUB}/swap/averagePrice/ADA/{tid}", {})):
        try:
            r = S.get(url, headers=hdr, timeout=20)
            if r.ok:
                d = r.json()
                p = num(d.get("price_ba")) if isinstance(d, dict) else None
                if p:
                    return p
        except Exception:
            pass
    return None


# =============== state ===============
def parse_pairs(txt):
    out = {}
    for part in (txt or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            v = num(v)
            if v:
                out[k.strip().upper().lstrip("$")] = v
    return out


ENV_MINS = parse_pairs(os.environ.get("TOKEN_MIN", ""))
ENV_ENTRY = parse_pairs(os.environ.get("ENTRY_MCAP", ""))

state = {"min_over": {}, "default_min": None, "entry_over": {}, "wallet": {},
         "tg_offset": 0, "day": {}, "last_summary": ""}


def load_state():
    try:
        state.update(json.load(open(STATE_FILE)))
    except Exception:
        pass


ZLOCK = threading.RLock()


def save_state():
    try:
        with ZLOCK:
            data = json.dumps(state)
        open(STATE_FILE, "w").write(data)
    except Exception as e:
        print("Save state error:", e)


def default_min():
    return state.get("default_min") or MIN_ADA


def mins():
    m = dict(ENV_MINS)
    for k, v in state["min_over"].items():
        if v:
            m[k] = v
        else:
            m.pop(k, None)
    return m


def entries():
    e = dict(ENV_ENTRY)
    e.update(state["entry_over"])
    return e


def day_rec():
    today = now_local().strftime("%Y-%m-%d")
    d = state.get("day") or {}
    if d.get("date") != today:
        d = {"date": today, "trades": {}, "wallet_start": None}
        state["day"] = d
    return d


# =============== big buys / sells ===============
def payload(page):
    return {"page": page, "perPage": 100,
            "filters": [{"filterType": "STATUS", "values": ["COMPLETE"]}],
            "orderSorts": "STARTTIME", "sortDirection": "DESC"}


def extract_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("orders", "data", "items", "results"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def fetch_since(since_ts, max_pages=15):
    out = []
    for page in range(max_pages):
        try:
            r = S.post(API + "/swap/globalOrders", headers=H, json=payload(page), timeout=30)
        except Exception as e:
            print("Request error:", e)
            break
        if not r.ok:
            print(f"globalOrders page {page}: HTTP {r.status_code} {r.text[:300]}")
            break
        lst = extract_list(r.json())
        if not lst:
            break
        out.extend(lst)
        tss = [t for t in (to_ts(g(x, *TS_FIELDS)) for x in lst) if t]
        if not tss or min(tss) < since_ts:
            break
        time.sleep(0.3)
    return out


clusters = {}     # tid -> deque[(ts, ada)]
cluster_sent = {}  # tid -> ts


def check_cluster(tid, name, ts, ada, info):
    dq = clusters.setdefault(tid, deque())
    dq.append((ts, ada))
    win = CLUSTER_MIN * 60
    while dq and ts - dq[0][0] > win:
        dq.popleft()
    if len(dq) >= CLUSTER_N and time.time() - cluster_sent.get(tid, 0) > 30 * 60:
        cluster_sent[tid] = time.time()
        total = sum(a for _, a in dq)
        lines = [f"\U0001F525 <b>{name}: {len(dq)} big buys f {CLUSTER_MIN} min</b>",
                 f"\U0001F4B5 Total: {kfmt(total)} ADA"]
        if info and info.get("mcap_ada"):
            lines.append(f"\U0001F4CA MCAP: {kfmt(info['mcap_ada'])} ADA")
        lines.append("\U0001F517 " + links(tid, info))
        tg("\n".join(lines))


streak_seen = set()
streak_sent = {}  # tid -> ts


def check_streak(tid, kind, ts, ada, quiet):
    """5 buys (wla 5 sells) mtab3in f nafs token f 9el mn STREAK_WINDOW_SEC, ay 7jm."""
    sk = state.setdefault("streaks", {})
    st = sk.get(tid)
    if not st or st["kind"] != kind:
        st = sk[tid] = {"kind": kind, "tr": []}
    st["tr"].append([ts, ada])
    st["tr"] = st["tr"][-STREAK_N:]
    tr = st["tr"]
    if (len(tr) == STREAK_N and tr[-1][0] - tr[0][0] <= STREAK_WINDOW_SEC and not quiet
            and time.time() - streak_sent.get(tid, 0) > STREAK_COOLDOWN_MIN * 60):
        streak_sent[tid] = time.time()
        name = token_name(tid)
        info = dex1(tid)
        secs = int(tr[-1][0] - tr[0][0])
        dur = f"{secs // 60} min {secs % 60} s" if secs >= 60 else f"{max(secs, 1)} s"
        total = sum(x[1] for x in tr)
        if kind == "buy":
            head = f"\U0001F680\U0001F7E2 <b>{name}: {STREAK_N} buys mtab3in f {dur}</b>"
        else:
            head = f"\U0001F6A8\U0001F534 <b>{name}: {STREAK_N} sells mtab3in f {dur}</b>"
        lines = [head, f"\U0001F4B5 Total: {kfmt(total)} ADA"]
        if info and info.get("mcap_ada"):
            lines.append(f"\U0001F4CA MCAP: {kfmt(info['mcap_ada'])} ADA")
        lines.append("\U0001F517 " + links(tid, info))
        tg("\n".join(lines))


def links(tid, info, tx=None):
    out = []
    if info and info.get("url"):
        out.append(f'<a href="{info["url"]}">Chart</a>')
    out.append(f'<a href="https://app.dexhunter.io/swap?tokenIdSell=&tokenIdBuy={tid}">DexHunter</a>')
    if tx and len(str(tx)) == 64:
        out.append(f'<a href="https://cardanoscan.io/transaction/{tx}">Transaction</a>')
    return " · ".join(out)


def process(orders, since, seen, quiet=False):
    alerts = 0
    lim = mins()
    dmin = default_min()
    low = min([dmin] + list(lim.values()))
    events = []
    all_tr = []
    for o in orders:
        tin = g(o, "token_id_in", "tokenIdIn", "token_in", default="")
        tout = g(o, "token_id_out", "tokenIdOut", "token_out", default="")
        if is_ada(tout) and not is_ada(tin):
            kind = "sell"
            ada = ada_amount(g(o, "actual_out_amount", "amount_out", "expected_out_amount", default=0))
        elif is_ada(tin) and not is_ada(tout):
            kind = "buy"
            ada = ada_amount(g(o, "amount_in", "amountIn", default=0))
        else:
            continue
        ts = to_ts(g(o, *TS_FIELDS))
        if ts and ts < since:
            continue
        tx = g(o, "tx_hash", "txHash", "_id", "id", default="")
        uid = str(tx or json.dumps(o, sort_keys=True))
        if uid not in streak_seen:
            streak_seen.add(uid)
            all_tr.append((ts or time.time(), tin if kind == "sell" else tout, kind, ada))
        if ada < low:
            continue
        if uid in seen:
            continue
        tok = tin if kind == "sell" else tout
        name = token_name(tok)
        need = lim.get(str(name).upper().lstrip("$"), dmin)
        if ada < need:
            continue
        seen.add(uid)
        qty = num(g(o, "amount_in", "amountIn") if kind == "sell" else
                  g(o, "actual_out_amount", "amount_out", "expected_out_amount"))
        price = (ada / qty) if qty else None
        impact = price_move(kind, ada, tok, o)
        big = impact is not None and impact >= BIG_MOVE
        info = dex1(tok)
        if kind == "sell":
            icon = "\U0001F7E8" if big else "\U0001F534"
            lines = [f"{icon} <b>BIG SELL {name} {kfmt(ada)} ADA</b>"]
        else:
            icon = "\U0001F7E8" if big else "\U0001F7E2"
            lines = [f"{icon} <b>BIG BUY {name} {kfmt(ada)} ADA</b>"]
        lines.append(f"\U0001F4B0 Price: {pfmt(price)} ADA" if price else "\U0001F4B0 Price: ?")
        lines.append(f"\U0001F4E6 {kfmt(qty)} {name}" if qty else f"\U0001F4E6 ? {name}")
        if info and info.get("mcap_ada"):
            lines.append(f"\U0001F4CA MCAP: {kfmt(info['mcap_ada'])} ADA")
        if impact is not None:
            sign = "-" if kind == "sell" else "+"
            arrow = "\U0001F4C9" if kind == "sell" else "\U0001F4C8"
            lines.append(f"{arrow} Impact: {sign}{abs(impact):.2f}%")
        lines.append("\U0001F517 " + links(tok, info, tx))
        tg("\n".join(lines))
        alerts += 1
        # l'résumé dyal nhar
        d = day_rec()["trades"].setdefault(tok, {"n": name, "buy": 0, "sell": 0, "nb": 0, "ns": 0})
        d["buy" if kind == "buy" else "sell"] += ada
        d["nb" if kind == "buy" else "ns"] += 1
        events.append((ts or time.time(), tok, name, kind, ada, info))
    # b tartib dyal l'wa9t (l9dim 9bel)
    for ts, tok, name, kind, ada, info in sorted(events, key=lambda e: e[0]):
        if kind == "buy":
            check_cluster(tok, name, ts, ada, info)
    # ay buy/sell (ay 7jm) l'streaks
    for ts, tok, kind, ada in sorted(all_tr, key=lambda e: e[0]):
        check_streak(tok, kind, ts, ada, quiet)
    if len(streak_seen) > 50000:
        streak_seen.clear()
    return alerts


# =============== wallet ===============
_stake = [None]
_supply = {}


KOIOS_TOKEN = os.environ.get("KOIOS_TOKEN", "").strip()
_KH = {"Authorization": f"Bearer {KOIOS_TOKEN}"} if KOIOS_TOKEN else {}
_kwait = [0.0, 0]


def _koios_call(method, path, **kw):
    if time.time() < _kwait[0]:
        return []
    try:
        r = S.request(method, f"{KOIOS}/{path}", headers=_KH, timeout=30, **kw)
        if r.status_code == 429:
            _kwait[1] += 1
            _kwait[0] = time.time() + min(300, 20 * _kwait[1])
            print(f"Koios 429 (limit) - n7bes {min(300, 20 * _kwait[1])}s")
            return []
        _kwait[1] = 0
        return r.json() if r.ok else []
    except Exception as e:
        print("Koios error:", e)
        return []


def koios(path, body):
    return _koios_call("POST", path, json=body)


def holdings():
    if not WALLET:
        return {}
    if not _stake[0]:
        ai = koios("address_info", {"_addresses": [WALLET]})
        _stake[0] = (ai[0].get("stake_address") if ai else None) or ""
    rows = (koios("account_assets", {"_stake_addresses": [_stake[0]]}) if _stake[0]
            else koios("address_assets", {"_addresses": [WALLET]}))
    out = {}
    for a in rows or []:
        tid = a.get("policy_id", "") + a.get("asset_name", "")
        dec = int(a.get("decimals") or 0)
        q = (num(a.get("quantity")) or 0) / (10 ** dec)
        try:
            name = bytes.fromhex(a.get("asset_name", "")).decode("utf-8")
            if not name or not name.isprintable():
                raise ValueError
        except Exception:
            name = tid[:10]
        out[tid] = {"name": name, "qty": q}
    return out


def supply(tid):
    if tid in _supply:
        return _supply[tid]
    rows = koios("asset_info", {"_asset_list": [[tid[:56], tid[56:]]]})
    s = None
    if rows:
        a = rows[0]
        dec = int((a.get("token_registry_metadata") or {}).get("decimals") or 0)
        s = (num(a.get("total_supply")) or 0) / (10 ** dec)
    _supply[tid] = s
    return s


def wallet_snapshot():
    """[{tid,name,qty,price,value,mcap,entry,pnl,gain}]"""
    hold = holdings()
    ent = entries()
    ws = state["wallet"]
    out = []
    for tid, h in hold.items():
        p = price_ada(tid)
        if not p:
            continue
        value = p * h["qty"]
        if value < WALLET_MIN_VALUE:
            continue
        sup = supply(tid)
        if not sup:
            continue
        mcap = p * sup
        st = ws.get(tid)
        man = ent.get(h["name"].upper())
        if st is None:
            st = ws[tid] = {"entry": man or mcap, "level": 0, "src": "manual" if man else "daba"}
        elif man and abs(st["entry"] - man) > 1e-9:
            st.update(entry=man, level=0, src="manual")
        gain = (mcap / st["entry"] - 1) * 100
        cost = h["qty"] * st["entry"] / sup
        out.append({"tid": tid, "name": h["name"], "qty": h["qty"], "price": p, "value": value,
                    "mcap": mcap, "entry": st["entry"], "pnl": value - cost, "gain": gain})
    for tid in list(ws):
        if tid not in hold:
            del ws[tid]
    return out


def check_wallet(first):
    if not WALLET:
        return
    snap = wallet_snapshot()
    d = day_rec()
    if d.get("wallet_start") is None:
        d["wallet_start"] = sum(x["value"] for x in snap)
    for x in snap:
        st = state["wallet"][x["tid"]]
        old = level = st["level"]
        while x["gain"] >= (level + 1) * WALLET_STEP:
            level += 1
        while x["gain"] <= (level - 1) * WALLET_STEP:
            level -= 1
        st["level"] = level
        if level != old and level != 0 and not first:
            head = (f"\U0001F680 <b>{x['name']} +{x['gain']:.0f}%</b>" if x["gain"] > 0
                    else f"\U0001F53B <b>{x['name']} {x['gain']:.0f}%</b>")
            pl = (f"\U0001F7E2 Profit: +{kfmt(x['pnl'])} ADA" if x["pnl"] >= 0
                  else f"\U0001F534 Loss: -{kfmt(-x['pnl'])} ADA")
            tg(f"{head}\n\U0001F4CA MCAP: {kfmt(x['entry'])} ➜ {kfmt(x['mcap'])} ADA\n"
               f"\U0001F4BC 3ndk: {kfmt(x['qty'])} {x['name']} ≈ {kfmt(x['value'])} ADA\n{pl}\n"
               f"\U0001F517 {links(x['tid'], dex1(x['tid']))}")


def wallet_text():
    if not WALLET:
        return "Ma kaynach adresse dyal wallet (WALLET_ADDR)."
    snap = sorted(wallet_snapshot(), key=lambda x: -x["value"])
    if not snap:
        return "Ma l9it 7tta token f wallet."
    lines = ["\U0001F4BC <b>Wallet dyalk</b>"]
    tv = tp = 0
    for x in snap:
        tv += x["value"]
        tp += x["pnl"]
        icon = "\U0001F7E2" if x["pnl"] >= 0 else "\U0001F534"
        lines.append(f"{icon} <b>{x['name']}</b>: {kfmt(x['value'])} ADA ({pct(x['gain'])}) "
                     f"{'+' if x['pnl'] >= 0 else '-'}{kfmt(abs(x['pnl']))} ADA")
    lines.append(f"\n\U0001F4B0 Total: <b>{kfmt(tv)} ADA</b>")
    lines.append(f"{'🟢 Profit' if tp >= 0 else '🔴 Loss'}: {'+' if tp >= 0 else '-'}{kfmt(abs(tp))} ADA")
    lines.append("<i>PnL mn l'entry (ila ma 3titinich entry, mn nhar bda l'bot kaychoufo)</i>")
    return "\n".join(lines)


# =============== commands ===============
HELP = ("\U0001F916 <b>Commands</b>\n"
        "/price SNEK — prix, MCAP, 1h/24h, chart\n"
        "/wallet — ch7al 3ndk w profit/loss\n"
        "/min — chouf l'limites\n"
        "/min 15000 — limite 3adiya l ga3 tokens\n"
        "/min SNEK 30000 — limite khassa l token\n"
        "/min SNEK off — 7ayd limite khassa\n"
        "/entry SOLADA 2070000 — MCAP fach chriti (l wallet)\n"
        "/summary — résumé dyal nhar daba")


def cmd_price(arg):
    if not arg:
        return "Ktb b7al: /price SNEK"
    tid, name = find_token(arg)
    if not tid:
        return f"Ma l9itch token smito {arg}."
    info = dex1(tid) or {}
    p = info.get("price") or price_ada(tid)
    lines = [f"\U0001F4B0 <b>{name}</b>",
             f"Price: {pfmt(p)} ADA",
             f"\U0001F4CA MCAP: {kfmt(info.get('mcap_ada'))} ADA",
             f"⏱ 1h: {pct(info.get('h1'))} · 24h: {pct(info.get('h24'))}"]
    if info.get("liq_ada"):
        lines.append(f"\U0001F4A7 Liquidity: {kfmt(info['liq_ada'])} ADA")
    lines.append(f"\U0001F4C4 <code>{tid}</code>")
    lines.append("\U0001F517 " + links(tid, info))
    return "\n".join(lines)


def cmd_min(args):
    if not args:
        m = mins()
        lines = [f"⚙️ <b>Limites</b>", f"Ga3 tokens: {kfmt(default_min())} ADA"]
        for k, v in sorted(m.items(), key=lambda kv: kv[1]):
            lines.append(f"• {k}: {kfmt(v)} ADA")
        return "\n".join(lines)
    if len(args) == 1:
        v = num(args[0].replace("k", "000").replace("K", "000"))
        if not v:
            return "Ktb b7al: /min 15000 wla /min SNEK 30000"
        state["default_min"] = v
        save_state()
        return f"✅ Limite dyal ga3 tokens daba: {kfmt(v)} ADA"
    tok = args[0].upper().lstrip("$")
    if args[1].lower() in ("off", "0", "remove", "7ayd"):
        state["min_over"][tok] = 0
        save_state()
        return f"✅ {tok} rja3 l limite 3adiya ({kfmt(default_min())} ADA)"
    v = num(args[1].replace("k", "000").replace("K", "000"))
    if not v:
        return "Ktb b7al: /min SNEK 30000"
    state["min_over"][tok] = v
    save_state()
    return f"✅ {tok}: alert ghir fo9 {kfmt(v)} ADA"


def cmd_entry(args):
    if len(args) < 2:
        return "Ktb b7al: /entry SOLADA 2070000 (MCAP b ADA fach chriti)"
    v = num(args[1].replace("m", "e6").replace("M", "e6").replace("k", "e3").replace("K", "e3"))
    if not v:
        return "Ktb b7al: /entry SOLADA 2070000"
    tok = args[0].upper().lstrip("$")
    state["entry_over"][tok] = v
    save_state()
    return f"✅ Entry dyal {tok}: {kfmt(v)} MCAP"


def handle(text):
    parts = text.strip().split()
    if not parts:
        return None
    c = parts[0].split("@")[0].lower()
    args = parts[1:]
    if c == "/price":
        return cmd_price(" ".join(args))
    if c == "/wallet":
        return wallet_text()
    if c == "/min":
        return cmd_min(args)
    if c == "/entry":
        return cmd_entry(args)
    if c == "/summary":
        return summary_text()
    if c in ("/help", "/start"):
        return HELP
    return None


def poll_commands(wait):
    end = time.time() + wait
    while True:
        left = end - time.time()
        if left <= 1:
            return
        ups = tg_updates(state.get("tg_offset", 0), min(left, 25))
        for u in ups:
            state["tg_offset"] = u["update_id"] + 1
            m = u.get("message") or {}
            if str((m.get("chat") or {}).get("id")) != TG_CHAT:
                continue  # ghir chat dyalk
            txt = m.get("text") or ""
            if not txt.startswith("/"):
                continue
            try:
                ans = handle(txt)
            except Exception as e:
                ans = f"Wa9e3 chi mochkil: {e}"
            if ans:
                tg(ans, mirror=False)
        if ups:
            save_state()


# =============== résumé dyal nhar ===============
def summary_text():
    d = day_rec()
    tr = d.get("trades", {})
    lines = [f"\U0001F4CA <b>Résumé dyal nhar {now_local():%d/%m}</b>"]
    nb = sum(x["nb"] for x in tr.values())
    ns = sum(x["ns"] for x in tr.values())
    vb = sum(x["buy"] for x in tr.values())
    vs = sum(x["sell"] for x in tr.values())
    lines.append(f"\U0001F433 Big trades: \U0001F7E2 {nb} buys ({kfmt(vb)} ADA) · "
                 f"\U0001F534 {ns} sells ({kfmt(vs)} ADA)")
    if tr:
        top = sorted(tr.items(), key=lambda kv: -(kv[1]["buy"] - kv[1]["sell"]))
        lines.append("\n\U0001F525 <b>Akbar chra (net)</b>")
        for tid, x in top[:3]:
            net = x["buy"] - x["sell"]
            if net <= 0:
                break
            lines.append(f"• {x['n']}: +{kfmt(net)} ADA ({x['nb']} buys)")
        dump = [kv for kv in reversed(top) if kv[1]["sell"] - kv[1]["buy"] > 0][:3]
        if dump:
            lines.append("\n\U0001F9CA <b>Akbar bi3 (net)</b>")
            for tid, x in dump:
                lines.append(f"• {x['n']}: -{kfmt(x['sell'] - x['buy'])} ADA ({x['ns']} sells)")
    # tokens li tl3o / hbto (24h)
    cand = set(tr)
    try:
        r = S.post("https://charts.dhapi.io/trending", json={}, timeout=20)
        for t in (r.json() if r.ok else []):
            if t.get("token_id"):
                cand.add(t["token_id"])
    except Exception:
        pass
    for tid in list(state["wallet"]):
        cand.add(tid)
    info = dexscreener(list(cand))
    ch = [(tid, i["h24"]) for tid, i in info.items() if i and i.get("h24") is not None]
    if ch:
        ch.sort(key=lambda x: -x[1])
        lines.append("\n\U0001F4C8 <b>Tl3o (24h)</b>")
        for tid, c in ch[:3]:
            if c <= 0:
                break
            lines.append(f"• {token_name(tid)}: {pct(c)} · MCAP {kfmt(info[tid].get('mcap_ada'))}")
        lines.append("\U0001F4C9 <b>Hbto (24h)</b>")
        for tid, c in list(reversed(ch))[:3]:
            if c >= 0:
                break
            lines.append(f"• {token_name(tid)}: {pct(c)} · MCAP {kfmt(info[tid].get('mcap_ada'))}")
    if WALLET:
        snap = wallet_snapshot()
        tv = sum(x["value"] for x in snap)
        tp = sum(x["pnl"] for x in snap)
        ws = d.get("wallet_start")
        lines.append("\n\U0001F4BC <b>Wallet</b>")
        lines.append(f"Total: {kfmt(tv)} ADA" + (f" ({'+' if tv >= ws else '-'}{kfmt(abs(tv - ws))} ADA lyoum)" if ws else ""))
        lines.append(f"{'🟢 Profit' if tp >= 0 else '🔴 Loss'} mn l'entry: {'+' if tp >= 0 else '-'}{kfmt(abs(tp))} ADA")
        for x in sorted(snap, key=lambda x: -abs(x["pnl"]))[:5]:
            lines.append(f"• {x['name']}: {pct(x['gain'])} ({'+' if x['pnl'] >= 0 else '-'}{kfmt(abs(x['pnl']))} ADA)")
    return "\n".join(lines)


def maybe_summary():
    n = now_local()
    key = n.strftime("%Y-%m-%d")
    if n.hour >= SUMMARY_HOUR and state.get("last_summary") != key:
        state["last_summary"] = key
        try:
            tg(summary_text())
        except Exception as e:
            print("Summary error:", e)
        save_state()


# =============== zing wallet (copy-trading) ===============
ZING_ADDR = os.environ.get("ZING_ADDR", "").strip()
ZING_NAME = os.environ.get("ZING_NAME", "ZING").strip() or "ZING"
DISCORD_ZING = os.environ.get("DISCORD_ZING", "").strip()
ZING_EVERY = int(os.environ.get("ZING_EVERY_SEC", "60"))
_zing_stake = [None]


def zing_send(text):
    if DISCORD_ZING:
        try:
            r = S.post(DISCORD_ZING, json={"content": html_to_md(text)[:1990], "flags": 4,
                                           "allowed_mentions": {"parse": []}}, timeout=20)
            if r.status_code == 429:
                time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 10))
                S.post(DISCORD_ZING, json={"content": html_to_md(text)[:1990], "flags": 4,
                                           "allowed_mentions": {"parse": []}}, timeout=20)
            elif not r.ok:
                print("Discord zing error:", r.status_code, r.text[:200])
        except Exception as e:
            print("Discord zing exception:", e)
    else:
        print("ZING:", text[:200])


def is_script(addr):
    # addr1z / addr1x / addr1w = smart contract (DEX, orders...)
    return len(addr) > 5 and addr[5] in "zxw"


def short(addr):
    return f"{addr[:10]}…{addr[-6:]}"


def addr_link(addr):
    kind = "stakekey" if addr.startswith("stake1") else "address"
    return f'<a href="https://cardanoscan.io/{kind}/{addr}">{short(addr)}</a>'


def koios_get(path, params):
    return _koios_call("GET", path, params=params)


def _assets(u):
    """{tid: qty (b decimals)} + ADA."""
    out = {"": (num(u.get("value")) or 0) / 1e6}
    for a in u.get("asset_list") or []:
        tid = (a.get("policy_id") or "") + (a.get("asset_name") or "")
        q = (num(a.get("quantity")) or 0) / (10 ** int(a.get("decimals") or 0))
        out[tid] = out.get(tid, 0) + q
    return out


def _add(d, other, sign=1):
    for k, v in other.items():
        d[k] = d.get(k, 0) + sign * v


def _clean(d, ada_eps=1.0):
    return {k: v for k, v in d.items() if (abs(v) > ada_eps if k == "" else abs(v) > 0)}


def zing_tx_view(tx, stake):
    """net (dyal wallet), sent{addr:assets}, recv{addr:assets}"""
    net, sent, recv = {}, {}, {}
    for u in tx.get("inputs") or []:
        a = _assets(u)
        if u.get("stake_addr") == stake:
            _add(net, a, -1)
        else:
            _add(recv.setdefault(u["payment_addr"]["bech32"], {}), a)
    for u in tx.get("outputs") or []:
        a = _assets(u)
        if u.get("stake_addr") == stake:
            _add(net, a, 1)
        else:
            _add(sent.setdefault(u["payment_addr"]["bech32"], {}), a)
    return net, sent, recv


def _tokens(d):
    return {k: v for k, v in d.items() if k and abs(v) > 0}


def zing_swap_msg(kind, tid, qty, ada, txh, label=None):
    sink = getattr(_SINK, "trades", None)
    if sink is not None:
        sink.append((kind, tid, qty, ada or 0, txh))
        return ""
    label = label or ZING_NAME
    name = token_name(tid)
    info = dex1(tid)
    price = ada / qty if qty and ada else None
    icon = "\U0001F7E2" if kind == "buy" else "\U0001F534"
    lines = [f"\U0001F433 <b>{label} {icon} {'BUY' if kind == 'buy' else 'SELL'} {name}"
             + (f" {kfmt(ada)} ADA" if ada else "") + "</b>",
             f"\U0001F4E6 {kfmt(qty)} {name}" + (f" · \U0001F4B0 {pfmt(price)} ADA" if price else "")]
    if info and info.get("mcap_ada"):
        lines.append(f"\U0001F4CA MCAP: {kfmt(info['mcap_ada'])} ADA")
    lines.append("\U0001F517 " + links(tid, info, txh))
    return "\n".join(lines)


def _fmt_assets(d):
    parts = []
    if d.get("", 0) > 0.5:
        parts.append(f"{kfmt(d[''])} ADA")
    for k, v in d.items():
        if k and v > 0:
            parts.append(f"{kfmt(v)} {token_name(k)}")
    return " + ".join(parts) or "?"


def _order_utxos(tx, stake):
    """Inputs mn script (machi dyal wallet) li homa orders: ADA bo7do, wla token wa7d + ADA sghir."""
    out = []
    for u in tx.get("inputs") or []:
        ad = u["payment_addr"]["bech32"]
        if u.get("stake_addr") == stake or not is_script(ad):
            continue
        a = _assets(u)
        t = {k: v for k, v in a.items() if k and v > 0}
        if not t and a.get("", 0) > 3:
            out.append({"addr": ad, "assets": a})
        elif len(t) == 1 and a.get("", 0) < 10:
            out.append({"addr": ad, "assets": a})
    return out


def zing_classify(tx, stake, pending, label=None):
    label = label or ZING_NAME
    """Kayrj3 lista dyal messages. pending = orders li sifethom l DEX w mazal ma t3mmrouch."""
    txh = tx["tx_hash"]
    net, sent, recv = zing_tx_view(tx, stake)
    toks = _tokens(net)
    ada = net.get("", 0)
    to_scripts = {a: v for a, v in sent.items() if is_script(a)}
    to_people = {a: v for a, v in sent.items() if not is_script(a)}
    from_scripts = {a: v for a, v in recv.items() if is_script(a)}
    from_people = {a: v for a, v in recv.items() if not is_script(a)}
    msgs = []
    wallet_in = any(u.get("stake_addr") == stake for u in tx.get("inputs") or [])
    if not wallet_in:
        to_scripts, to_people = {}, {}  # Tx dyal chi wa7d akhor: dakchi li tsifet machi mn 3ndo

    # 1) swap f nafs Tx (direct, wla order f script dyalo 3amar)
    if len(toks) == 1 and abs(ada) > 2:
        tid, q = next(iter(toks.items()))
        if q > 0 and ada < 0:
            return [zing_swap_msg("buy", tid, q, -ada, txh, label)]
        if q < 0 and ada > 0:
            return [zing_swap_msg("sell", tid, -q, ada, txh, label)]

    # 2) order jdid l DEX: nkhbbiwh 7tta y3mmer
    if to_scripts and ada <= 0:
        for u in tx.get("outputs") or []:
            ad = u["payment_addr"]["bech32"]
            if ad in to_scripts and u.get("stake_addr") != stake:
                a = _assets(u)
                t = {k: v for k, v in a.items() if k and v > 0}
                if (not t and a.get("", 0) > 3) or (len(t) == 1 and a.get("", 0) < 10):
                    pending.append({"addr": ad, "assets": a})
        # transfers sghar m3a order (fees dyal batcher) ma kanbaynouhomch
        to_people = {a: v for a, v in to_people.items() if v.get("", 0) >= 20 or _tokens(v)}

    # 3) order t3mmer (fill): wallet kayjih chi 7aja mn script
    if not wallet_in and from_scripts and (ada > 0.5 or any(v > 0 for v in toks.values())):
        order = None
        for i, p in enumerate(pending):
            if p["addr"] in from_scripts:
                order = pending.pop(i)["assets"]
                break
        if order is None:
            cands = _order_utxos(tx, stake)
            if len(cands) == 1:
                order = cands[0]["assets"]
        got_t = {k: v for k, v in toks.items() if v > 0}
        if order is not None:
            o_t = {k: v for k, v in order.items() if k and v > 0}
            o_ada = order.get("", 0)
            if got_t and not o_t:
                tid, q = max(got_t.items(), key=lambda kv: kv[1])
                spent = o_ada - max(ada, 0)
                if spent > 2:
                    return [zing_swap_msg("buy", tid, q, spent, txh, label)]
                return []
            if o_t and ada > 0 and not got_t:
                tid, q = next(iter(o_t.items()))
                gotada = ada - o_ada
                if gotada > 1:
                    return [zing_swap_msg("sell", tid, q, gotada, txh, label)]
                return []
            return []
        if got_t:
            tid, q = max(got_t.items(), key=lambda kv: kv[1])
            return [zing_swap_msg("buy", tid, q, None, txh, label)]
        return []

    # 4) sifet l chi wa7d
    if to_people:
        total = {}
        for v in to_people.values():
            _add(total, v)
        if total.get("", 0) >= 1 or _tokens(total):
            lines = [f"\U0001F4E4 <b>{label} sent {_fmt_assets(total)}</b>"]
            for a, v in sorted(to_people.items(), key=lambda kv: -kv[1].get("", 0))[:3]:
                lines.append(f"\u27A1\ufe0f to: {addr_link(a)} ({_fmt_assets(v)})")
            lines.append(f'\U0001F517 <a href="https://cardanoscan.io/transaction/{txh}">Tx</a>')
            msgs.append("\n".join(lines))
    # 5) jato mn chi wa7d
    elif from_people and not wallet_in:
        got = {k: v for k, v in net.items() if v > 0}
        if got.get("", 0) < 1:
            got.pop("", None)
        if got:
            lines = [f"\U0001F4E5 <b>{label} received {_fmt_assets(got)}</b>"]
            for a in list(from_people)[:3]:
                lines.append(f"\u2B05\ufe0f from: {addr_link(a)}")
            lines.append(f'\U0001F517 <a href="https://cardanoscan.io/transaction/{txh}">Tx</a>')
            msgs.append("\n".join(lines))
    return msgs


def zings():
    """{smiya: {"addr","stake","height","pending"}} - wallets li kattab3."""
    z = state.setdefault("zings", {})
    if not state.get("zings_init"):
        if not z and ZING_ADDR:
            old = state.get("zing") or {}
            z[ZING_NAME] = {"addr": ZING_ADDR, "stake": "", "height": old.get("height", 0),
                            "pending": old.get("pending", [])}
        state["zings_init"] = True
    return z


def resolve_stake(addr):
    addr = (addr or "").strip()
    if addr.startswith("stake1"):
        return addr
    if not addr.startswith("addr1"):
        return ""
    ai = koios("address_info", {"_addresses": [addr]})
    if isinstance(ai, list) and ai:
        return ai[0].get("stake_address") or ""
    return ""


def find_zing(key):
    z = zings()
    if not key:
        return next(iter(z.items())) if len(z) == 1 else (None, None)
    k = key.strip().lower()
    for name, w in z.items():
        if name.lower() == k or w["addr"].lower() == k or (w.get("stake") or "").lower() == k:
            return name, w
    for name, w in z.items():
        if k in name.lower():
            return name, w
    return None, None


def zing_add(addr, name):
    addr = (addr or "").strip()
    name = (name or "").strip()[:30] or f"{addr[:8]}…{addr[-4:]}"
    stake = resolve_stake(addr)
    if not stake:
        return "❌ Invalid address. It must start with <code>addr1</code> or <code>stake1</code>."
    z = zings()
    for n, w in z.items():
        if w.get("stake") == stake:
            return f"⚠️ This wallet is already tracked as <b>{n}</b>."
    if name in z:
        return f"⚠️ The name <b>{name}</b> is already used. Pick another name."
    if len(z) >= 25:
        return "⚠️ You reached 25 wallets. Remove one with /zing remove first."
    tip = koios_get("tip", {})
    with ZLOCK:
        z[name] = {"addr": addr, "stake": stake, "height": tip[0].get("block_no", 0) if tip else 0, "pending": []}
    save_state()
    return (f"✅ Now tracking <b>{name}</b> \U0001F433\n{addr_link(addr)}\n"
            f"Every buy, sell, send and receive will be posted in #zing-wallet.")


def zing_remove(key):
    name, w = find_zing(key)
    if not name:
        return f"❌ No tracked wallet named <b>{key}</b>. See /zing list."
    with ZLOCK:
        zings().pop(name, None)
    save_state()
    return f"\U0001F5D1️ Stopped tracking <b>{name}</b>."


def zing_list_text():
    z = zings()
    if not z:
        return "No wallets tracked yet. Add one with /zing wallet."
    lines = [f"\U0001F433 <b>Tracked wallets ({len(z)})</b>"]
    for n, w in z.items():
        lines.append(f"• <b>{n}</b>: {addr_link(w['addr'])}")
    return "\n".join(lines)


ZING_FAST_SEC = int(os.environ.get("ZING_FAST_SEC", "20"))
ZING_SLOW_SEC = int(os.environ.get("ZING_SLOW_SEC", "300"))
_zaddr = {"t": 0, "addrs": [], "stakes": []}


def _zing_handle(tx, name, w):
    """Kaysift message dyal Tx mra wa7da bark (fast w slow ma ykrrouch)."""
    done = w.setdefault("done", [])
    if tx["tx_hash"] in done:
        return
    done.append(tx["tx_hash"])
    w["done"] = done[-300:]
    if not isinstance(w.get("pending"), list):
        w["pending"] = []
    try:
        trades, others = classify_trades(tx, w["stake"], w["pending"], name)
    except Exception as e:
        print("Zing classify error:", e)
        trades, others = [], []
    w["pending"] = w["pending"][-60:]
    for kind, tid, q, a, txh in trades:
        try:
            led = _ledger(w["stake"])
            if led is not None and tx.get("block_height"):
                led = ledger_sync(w["stake"], upto=tx["block_height"] - 1, create=False) or led
            msg = zing_swap_msg(kind, tid, q, a or None, txh, name)
            counted = bool(led) and led.get("h", 0) >= (tx.get("block_height") or 0) > 0
            extra = trade_context(kind, tid, q, a, led, counted)
            if extra:
                ls = msg.split("\n")
                msg = "\n".join(ls[:2] + extra + ls[2:])
            zing_send(msg)
            if kind == "buy":
                c = convergence(name, tid)
                if c:
                    zing_send(c)
        except Exception as e:
            print("Zing trade msg error:", e)
    for m in others:
        zing_send(m)


def zing_fast():
    """Kol ~20s: Tx jdad dyal ga3 l wallets f call wa7d (address_txs)."""
    z = zings()
    stakes = {w["stake"]: n for n, w in list(z.items()) if w.get("stake")}
    if not stakes:
        return
    if time.time() - _zaddr["t"] > 600 or sorted(_zaddr["stakes"]) != sorted(stakes):
        rows = koios("account_addresses", {"_stake_addresses": list(stakes), "_empty": True})
        addrs = []
        for r in rows or []:
            addrs += r.get("addresses") or []
        if addrs:
            _zaddr.update(t=time.time(), addrs=list(dict.fromkeys(addrs)), stakes=list(stakes))
            print(f"Zing fast: {len(stakes)} wallets, {len(_zaddr['addrs'])} adresses")
    addrs = _zaddr["addrs"]
    if not addrs:
        return
    h = state.get("zing_fast_h") or 0
    if not h:
        tip = koios_get("tip", {})
        if tip:
            state["zing_fast_h"] = tip[0].get("block_no", 0)
        return
    txs = []
    for i in range(0, len(addrs), 500):
        r = koios("address_txs", {"_addresses": addrs[i:i + 500], "_after_block_height": h + 1})
        if isinstance(r, list):
            txs += r
    if not txs:
        return
    txs.sort(key=lambda t: (t.get("block_height", 0), t.get("block_time", 0)))
    hashes = list(dict.fromkeys(t["tx_hash"] for t in txs))[:40]
    heights = {t["tx_hash"]: t.get("block_height", 0) for t in txs}
    info = koios("tx_info", {"_tx_hashes": hashes, "_inputs": True, "_assets": True})
    by = {t["tx_hash"]: t for t in info or []}
    with ZLOCK:
        for hsh in hashes:
            tx = by.get(hsh)
            if not tx:
                break
            inv = {u.get("stake_addr") for u in (tx.get("inputs") or []) + (tx.get("outputs") or [])}
            for st, name in stakes.items():
                if st in inv and name in z:
                    _zing_handle(tx, name, z[name])
            state["zing_fast_h"] = max(state.get("zing_fast_h") or 0, heights.get(hsh, 0))


def zing_fast_loop():
    while True:
        t0 = time.time()
        try:
            zing_fast()
        except Exception as e:
            print("Zing fast error:", e)
        try:
            ledger_seed_step()
        except Exception as e:
            print("Ledger seed error:", e)
        time.sleep(max(3, ZING_FAST_SEC - (time.time() - t0)))


def check_zing():
    """Safety net (kol ~5 min): wallet b wallet, ila fat chi Tx l fast."""
    z = zings()
    if not z:
        return
    tip = None
    for name, w in list(z.items()):
        if not w.get("stake"):
            w["stake"] = resolve_stake(w["addr"])
            if not w["stake"]:
                continue
        if not w.get("height"):
            tip = tip or koios_get("tip", {})
            if tip:
                w["height"] = tip[0].get("block_no", 0)
                print(f"Zing {name}: bda mn block", w["height"])
            continue
        txs = koios_get("account_txs", {"_stake_address": w["stake"], "_after_block_height": w["height"] + 1})
        if not isinstance(txs, list) or not txs:
            continue
        txs = sorted(txs, key=lambda t: (t.get("block_height", 0), t.get("block_time", 0)))[:40]
        info = koios("tx_info", {"_tx_hashes": [t["tx_hash"] for t in txs], "_inputs": True, "_assets": True})
        by = {t["tx_hash"]: t for t in info or []}
        with ZLOCK:
            for t in txs:
                tx = by.get(t["tx_hash"])
                if not tx:
                    break
                _zing_handle(tx, name, w)
                w["height"] = max(w["height"], t.get("block_height", 0))


# =============== PNL ledger + Portfolio ===============
_SINK = threading.local()
LEDGER_MAX_TX = int(os.environ.get("LEDGER_MAX_TX", "400"))
PORTFOLIO_CHANNEL = os.environ.get("PORTFOLIO_CHANNEL", "").strip()
PORTFOLIO_EVERY = int(os.environ.get("PORTFOLIO_EVERY_SEC", "600"))
CONV_WINDOW = int(os.environ.get("CONV_WINDOW_MIN", "120")) * 60


def classify_trades(tx, stake, pending, label=None):
    """Bhal zing_classify walakin kayrj3 (trades, other_msgs). trades = [(kind, tid, qty, ada, txh)]."""
    _SINK.trades = []
    try:
        msgs = zing_classify(tx, stake, pending, label)
        trades = _SINK.trades
    finally:
        _SINK.trades = None
    return trades, [m for m in msgs if m]


def _ledger(stake):
    return state.setdefault("ledgers", {}).get(stake)


def _apply_trade(led, kind, tid, qty, ada):
    if not ada or not qty:
        return
    t = led["tok"].setdefault(tid, {"bq": 0, "ba": 0, "sq": 0, "sa": 0})
    if kind == "buy":
        t["bq"] += qty
        t["ba"] += ada
    else:
        t["sq"] += qty
        t["sa"] += ada


_LLOCK = threading.RLock()


def ledger_sync(stake, upto=None, create=True):
    """Kat7sseb buys/sells dyal wallet mn l'blockchain (incremental). upto = block max."""
    with _LLOCK:
        return _ledger_sync(stake, upto, create)


def _ledger_sync(stake, upto=None, create=True):
    led = _ledger(stake)
    if led is None:
        if not create:
            return None
        led = {"h": 0, "pend": [], "tok": {}, "n": 0}
    txs = koios_get("account_txs", {"_stake_address": stake, "_after_block_height": led["h"] + 1,
                                    "order": "block_height.desc", "limit": LEDGER_MAX_TX})
    if not isinstance(txs, list):
        return led if _ledger(stake) else None
    txs = sorted(txs, key=lambda t: (t.get("block_height", 0), t.get("block_time", 0)))
    if upto:
        txs = [t for t in txs if t.get("block_height", 0) <= upto]
    txs = txs[-LEDGER_MAX_TX:]
    for i in range(0, len(txs), 25):
        chunk = txs[i:i + 25]
        info = koios("tx_info", {"_tx_hashes": [t["tx_hash"] for t in chunk], "_inputs": True, "_assets": True})
        by = {t["tx_hash"]: t for t in info or []}
        if not by:
            break
        for t in chunk:
            tx = by.get(t["tx_hash"])
            if tx:
                try:
                    trades, _ = classify_trades(tx, stake, led["pend"])
                    for k, tid, q, a, _h in trades:
                        _apply_trade(led, k, tid, q, a)
                except Exception as e:
                    print("Ledger classify error:", e)
            led["h"] = max(led["h"], t.get("block_height", 0))
            led["n"] += 1
        led["pend"] = led["pend"][-60:]
    state.setdefault("ledgers", {})[stake] = led
    return led


def avg_entry(led, tid):
    t = (led or {}).get("tok", {}).get(tid)
    if not t or t["bq"] <= 0:
        return None
    return t["ba"] / t["bq"]


def trade_context(kind, tid, qty, ada, led, counted=False):
    """Lines zaydin l message dyal Track: new position / adding / PnL / closed."""
    if led is None:
        return []
    t = dict(led["tok"].get(tid) or {"bq": 0, "ba": 0, "sq": 0, "sa": 0})
    if counted and qty and ada:  # had trade deja dkhal f ledger: n7aydouh
        if kind == "buy":
            t["bq"] -= qty
            t["ba"] -= ada
        else:
            t["sq"] -= qty
            t["sa"] -= ada
    led = {"tok": {tid: t}}
    held = t["bq"] - t["sq"]
    avg = avg_entry(led, tid)
    out = []
    if kind == "buy":
        if held <= max(t["bq"] * 0.02, 0):
            out.append("\U0001F195 <b>New position</b>")
        elif avg and ada and qty:
            new_avg = (t["ba"] + ada) / (t["bq"] + qty)
            out.append(f"➕ Adding · avg entry {pfmt(avg)} → {pfmt(new_avg)} ADA")
    elif avg and ada and qty:
        cost = qty * avg
        pnl = ada - cost
        p = pnl / cost * 100 if cost else 0
        icon = "\U0001F4B5" if pnl >= 0 else "\U0001F4B8"
        out.append(f"{icon} PnL: <b>{'+' if pnl >= 0 else ''}{kfmt(pnl)} ADA ({p:+.0f}%)</b> · avg entry {pfmt(avg)} ADA")
        if held - qty <= t["bq"] * 0.02:
            out.append("\U0001F6AA <b>Closed position</b>")
    return out


def convergence(name, tid):
    """Ila 2+ wallets tracked chraw nafs token f 2h."""
    c = state.setdefault("conv", {})
    now = time.time()
    lst = [x for x in c.get(tid, []) if now - x[1] < CONV_WINDOW and x[0] != name] + [[name, now]]
    c[tid] = lst
    for k in list(c):
        c[k] = [x for x in c[k] if now - x[1] < CONV_WINDOW]
        if not c[k]:
            c.pop(k)
    names = list(dict.fromkeys(x[0] for x in lst))
    last = state.setdefault("conv_sent", {})
    if len(names) >= 2 and now - last.get(tid, 0) > CONV_WINDOW and len(names) > last.get(tid + ":n", 0):
        last[tid] = now
        last[tid + ":n"] = len(names)
        info = dex1(tid)
        lines = [f"\U0001F525 <b>SMART MONEY: {len(names)} tracked wallets bought {token_name(tid)}</b>",
                 "\U0001F465 " + ", ".join(names[:8])]
        if info and info.get("mcap_ada"):
            lines.append(f"\U0001F4CA MCAP: {kfmt(info['mcap_ada'])} ADA")
        lines.append("\U0001F517 " + links(tid, info))
        return "\n".join(lines)
    return None


def _holdings(stake):
    acc = koios("account_info", {"_stake_addresses": [stake]})
    ada = (num(acc[0].get("total_balance")) or 0) / 1e6 if acc else 0
    rows = koios("account_assets", {"_stake_addresses": [stake]}) or []
    hold = {}
    for a in rows:
        tid = a.get("policy_id", "") + a.get("asset_name", "")
        dec = int(a.get("decimals") or 0)
        hold[tid] = hold.get(tid, 0) + (num(a.get("quantity")) or 0) / (10 ** dec)
    return ada, hold


def portfolio_text(key, max_rows=15):
    key = (key or "").strip()
    label, w = find_zing(key) if key else (None, None)
    if label:
        stake = w.get("stake") or resolve_stake(w["addr"])
    else:
        stake = resolve_stake(key)
        label = f"{key[:10]}…{key[-4:]}" if stake else None
    if not stake:
        return "❌ Unknown wallet. Use a tracked wallet name (/zing list) or an addr1… / stake1… address."
    led = ledger_sync(stake)
    ada, hold = _holdings(stake)
    tids = sorted(hold, key=lambda t: -hold[t])[:150]
    info = dexscreener(tids)
    rows = []
    for t in tids:
        i = info.get(t) or {}
        if not i.get("price"):
            continue
        v = i["price"] * hold[t]
        if v < 1:
            continue
        avg = avg_entry(led, t)
        pnl = (v - hold[t] * avg) if avg else None
        rows.append((t, hold[t], v, avg, pnl, i.get("h24")))
    rows.sort(key=lambda x: -x[2])
    tok_val = sum(r[2] for r in rows)
    unreal = sum(r[4] for r in rows if r[4] is not None)
    cost_known = sum(r[1] * r[3] for r in rows if r[3])
    realized = 0
    for tid, t in (led or {}).get("tok", {}).items():
        a = t["ba"] / t["bq"] if t["bq"] else None
        if a and t["sq"]:
            realized += t["sa"] - min(t["sq"], t["bq"]) * a
    sign = lambda x: ("+" if x >= 0 else "") + kfmt(x)
    lines = [f"\U0001F4BC <b>Portfolio · {label}</b>",
             f"\U0001F4B0 Total: <b>{kfmt(ada + tok_val)} ADA</b> (ADA {kfmt(ada)} + tokens {kfmt(tok_val)})",
             f"\U0001F4C8 Unrealized PnL: <b>{sign(unreal)} ADA</b>"
             + (f" ({unreal / cost_known * 100:+.0f}%)" if cost_known else "")
             + f" · Realized: <b>{sign(realized)} ADA</b>", ""]
    for t, q, v, avg, pnl, h24 in rows[:max_rows]:
        s = f"• <b>{token_name(t)}</b>: {kfmt(v)} ADA"
        if pnl is not None and avg:
            p = pnl / (q * avg) * 100 if q * avg else 0
            dot = "\U0001F7E2" if pnl >= 0 else "\U0001F534"
            s += f" · {dot} {sign(pnl)} ({p:+.0f}%) · entry {pfmt(avg)}"
        else:
            s += " · entry ?"
        if h24 is not None:
            s += f" · 24h {pct(h24)}"
        lines.append(s)
    if len(rows) > max_rows:
        lines.append(f"… +{len(rows) - max_rows} more tokens")
    lines.append(f"\n<i>PnL from the last {led['n'] if led else 0} on-chain txs. Tokens received by transfer show entry ?.</i>")
    lines.append(f'\U0001F517 <a href="https://cardanoscan.io/stakekey/{stake}">Cardanoscan</a>')
    return "\n".join(lines)


def portfolio_set(address):
    address = (address or "").strip()
    stake = resolve_stake(address)
    if not stake:
        return "❌ Not a valid addr1… / stake1… address."
    mine = state.setdefault("my_wallets", [])
    if address in mine:
        mine.remove(address)
        state["portfolio_next"] = 0
        return "\U0001F5D1️ Removed from your #portfolio board."
    if len(mine) >= 3:
        return "⚠️ Max 3 wallets on the board. Run /portfolio set again with one of them to remove it."
    mine.append(address)
    state["portfolio_next"] = 0
    return "✅ Added to your #portfolio board. It updates every 10 min (first update in ~1 min)."


def bot_post(chan_id, text, edit_id=None):
    """Kaysift (wla kay3dl) message b l'bot f channel. Kayrj3 message id."""
    if not (DISCORD_BOT_TOKEN and chan_id):
        print("BOT POST:", text[:200])
        return None
    url = f"https://discord.com/api/v10/channels/{chan_id}/messages"
    body = {"content": html_to_md(text)[:1990], "flags": 4, "allowed_mentions": {"parse": []}}
    h = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        for _ in range(2):
            r = (S.patch(f"{url}/{edit_id}", json=body, headers=h, timeout=20) if edit_id
                 else S.post(url, json=body, headers=h, timeout=20))
            if r.status_code == 429:
                time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 10))
                continue
            if r.status_code == 404 and edit_id:
                edit_id = None
                continue
            if not r.ok:
                print("Bot post error:", r.status_code, r.text[:200])
                return None
            return r.json().get("id")
    except Exception as e:
        print("Bot post exception:", e)
    return None


def portfolio_board():
    if not PORTFOLIO_CHANNEL or time.time() < state.get("portfolio_next", 0):
        return
    state["portfolio_next"] = time.time() + PORTFOLIO_EVERY
    mine = state.get("my_wallets") or []
    msgs = state.setdefault("portfolio_msgs", [])
    texts = [portfolio_text(a, max_rows=12) for a in mine] or [
        "\U0001F4BC <b>Your portfolio</b>\nNo wallet yet. Type <b>/portfolio set</b> with your address and this board "
        "will show your positions and PnL, updated every 10 min."]
    stamp = f"\n\U0001F552 Updated {now_local():%d/%m %H:%M}"
    new_ids = []
    for i, t in enumerate(texts):
        mid = bot_post(PORTFOLIO_CHANNEL, t[:1900] + stamp, msgs[i] if i < len(msgs) else None)
        new_ids.append(mid or (msgs[i] if i < len(msgs) else None))
    for mid in msgs[len(texts):]:
        if mid and DISCORD_BOT_TOKEN:
            try:
                S.delete(f"https://discord.com/api/v10/channels/{PORTFOLIO_CHANNEL}/messages/{mid}",
                         headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}, timeout=20)
            except Exception:
                pass
    state["portfolio_msgs"] = [m for m in new_ids if m]


def ledger_seed_step():
    """F background: kol 2 min, kat7sseb PnL dyal wallet tracked wa7d ma 3ndouch ledger."""
    if time.time() - state.get("ledger_seed_t", 0) < 120:
        return
    for name, w in list(zings().items()):
        if w.get("stake") and _ledger(w["stake"]) is None:
            state["ledger_seed_t"] = time.time()
            ledger_sync(w["stake"])
            print(f"Ledger ready: {name} ({len(_ledger(w['stake'])['tok'])} tokens)")
            return


# =============== Discord bot (/zing, /price ...) ===============
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").strip()


def zing_holdings_text(key=None):
    label, w = find_zing(key)
    if not label:
        return (f"\u274C Ma l9itch wallet <b>{key}</b>. Chouf /zing list." if key
                else "Khtar smiya dyal wallet (chouf /zing list).")
    stake = w.get("stake") or resolve_stake(w["addr"])
    if not stake:
        return "\u274C Ma 9dertch n9ra had l'wallet."
    acc = koios("account_info", {"_stake_addresses": [stake]})
    ada = (num(acc[0].get("total_balance")) or 0) / 1e6 if acc else 0
    rows = koios("account_assets", {"_stake_addresses": [stake]}) or []
    hold = {}
    for a in rows:
        tid = a.get("policy_id", "") + a.get("asset_name", "")
        dec = int(a.get("decimals") or 0)
        hold[tid] = hold.get(tid, 0) + (num(a.get("quantity")) or 0) / (10 ** dec)
    tids = sorted(hold, key=lambda t: -hold[t])[:150]
    info = dexscreener(tids)
    items = []
    for t in tids:
        i = info.get(t) or {}
        if i.get("price"):
            v = i["price"] * hold[t]
            if v >= 1:
                items.append((t, hold[t], v, i.get("mcap_ada"), i.get("h24")))
    items.sort(key=lambda x: -x[2])
    tok_val = sum(x[2] for x in items)
    lines = [f"\U0001F433 <b>{label} wallet</b>",
             f"\U0001F4B0 ADA: <b>{kfmt(ada)}</b>",
             f"\U0001F4BC Tokens: <b>{kfmt(tok_val)} ADA</b> · Total: <b>{kfmt(ada + tok_val)} ADA</b>", ""]
    for t, q, v, mc, h24 in items[:15]:
        extra = f" · MCAP {kfmt(mc)}" if mc else ""
        extra += f" · 24h {pct(h24)}" if h24 is not None else ""
        lines.append(f"• <b>{token_name(t)}</b>: {kfmt(q)} ≈ {kfmt(v)} ADA{extra}")
    if len(items) > 15:
        lines.append(f"… w {len(items) - 15} tokens khrin")
    lines.append(f"\n\U0001F517 <a href=\"https://cardanoscan.io/stakekey/{stake}\">Cardanoscan</a>")
    return "\n".join(lines)


def zing_recent_text(key=None, n=10):
    label, w = find_zing(key)
    if not label:
        return (f"\u274C Ma l9itch wallet <b>{key}</b>. Chouf /zing list." if key
                else "Khtar smiya dyal wallet (chouf /zing list).")
    stake = w.get("stake") or resolve_stake(w["addr"])
    if not stake:
        return "\u274C Ma 9dertch n9ra had l'wallet."
    tip = koios_get("tip", {})
    if not tip:
        return "Koios ma jawbch, 3awd mn b3d."
    txs = koios_get("account_txs", {"_stake_address": stake,
                                    "_after_block_height": tip[0].get("block_no", 0) - 20000})
    if not isinstance(txs, list) or not txs:
        return "Ma kayn 7tta Tx f had l'iyam."
    txs = sorted(txs, key=lambda t: (t.get("block_height", 0), t.get("block_time", 0)))[-40:]
    info = koios("tx_info", {"_tx_hashes": [t["tx_hash"] for t in txs], "_inputs": True, "_assets": True})
    by = {t["tx_hash"]: t for t in info or []}
    pending, out = [], []
    for t in txs:
        tx = by.get(t["tx_hash"])
        if not tx:
            continue
        try:
            msgs = zing_classify(tx, stake, pending, label)
        except Exception:
            msgs = []
        when = datetime.fromtimestamp(t.get("block_time", 0), TZ).strftime("%d/%m %H:%M")
        for m in msgs:
            out.append(f"\U0001F552 {when}\n{m}")
    if not out:
        return "Ma kayn 7tta buy/sell/transfer f akhir Tx."
    return f"\U0001F433 <b>Akhir {min(n, len(out))} Tx dyal {label}</b>\n\n" + "\n\n".join(reversed(out[-n:]))


def trending_text():
    try:
        r = S.post("https://charts.dhapi.io/trending", json={}, timeout=20)
        lst = r.json() if r.ok else []
    except Exception:
        lst = []
    if not lst:
        return "No data from DexHunter right now, try again in a minute."
    tids = [t["token_id"] for t in lst if t.get("token_id")]
    info = dexscreener(tids)
    lines = ["\U0001F525 <b>Trending on DexHunter</b>"]
    for t in lst[:12]:
        tid = t.get("token_id")
        i = info.get(tid) or {}
        lines.append(f"• <b>{token_name(tid)}</b>: vol {kfmt(num(t.get('current_period_volume')))} ADA"
                     f" · 24h {pct(i.get('h24'))} · MCAP {kfmt(i.get('mcap_ada'))}"
                     f" · \U0001F7E2{t.get('amount_buys', 0)}/\U0001F534{t.get('amount_sales', 0)}")
    return "\n".join(lines)


def whales_text():
    tr = day_rec().get("trades", {})
    if not tr:
        return "Mazal ma kayn 7tta big buy/sell lyoum."
    items = list(tr.values())
    lines = [f"\U0001F433 <b>Big buys/sells lyoum ({now_local():%d/%m})</b>"]
    buys = sorted(items, key=lambda x: -x["buy"])[:5]
    sells = sorted(items, key=lambda x: -x["sell"])[:5]
    lines.append("\n\U0001F7E2 <b>Akbar chra</b>")
    for x in buys:
        if x["buy"] > 0:
            lines.append(f"• {x['n']}: {kfmt(x['buy'])} ADA ({x['nb']} buys)")
    lines.append("\n\U0001F534 <b>Akbar bi3</b>")
    for x in sells:
        if x["sell"] > 0:
            lines.append(f"• {x['n']}: {kfmt(x['sell'])} ADA ({x['ns']} sells)")
    return "\n".join(lines)


DISCORD_HELP = ("\U0001F916 <b>Commands</b>\n"
                "/zing add — zid wallet (adresse + smiya)\n"
                "/zing list — wallets li kattab3\n"
                "/zing remove — 7ayd wallet\n"
                "/zing wallet — ach 3nd wallet daba\n"
                "/zing tx — akhir Tx dyal wallet\n"
                "/price SNEK — prix, MCAP, 1h/24h, chart\n"
                "/trending — tokens li fihom volume daba\n"
                "/whales — akbar buys w sells lyoum\n"
                "/summary — résumé dyal nhar")


def _chunks(text, size=1900):
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


ADA_PRICE_CHANNEL = os.environ.get("ADA_PRICE_CHANNEL", "").strip()
BTC_PRICE_CHANNEL = os.environ.get("BTC_PRICE_CHANNEL", "").strip()
ETH_PRICE_CHANNEL = os.environ.get("ETH_PRICE_CHANNEL", "").strip()
ADA_RENAME_SEC = int(os.environ.get("ADA_RENAME_SEC", "310"))
COINS = [("cardano", "ADA", "ADAUSD", 4), ("bitcoin", "BTC", "XBTUSD", 0), ("ethereum", "ETH", "ETHUSD", 2)]


def coin_prices():
    """{SYM: (prix USD, change 24h %)} mn CoinGecko, w Kraken ila tla7."""
    out = {}
    try:
        r = S.get("https://api.coingecko.com/api/v3/simple/price",
                  params={"ids": ",".join(c[0] for c in COINS), "vs_currencies": "usd",
                          "include_24hr_change": "true"}, timeout=15)
        d = r.json()
        for cid, sym, _, _ in COINS:
            if cid in d and d[cid].get("usd"):
                out[sym] = (float(d[cid]["usd"]), float(d[cid].get("usd_24h_change") or 0))
    except Exception:
        pass
    for cid, sym, pair, _ in COINS:
        if sym in out:
            continue
        try:
            r = S.get("https://api.kraken.com/0/public/Ticker", params={"pair": pair}, timeout=15)
            t = next(iter(r.json()["result"].values()))
            last, opn = float(t["c"][0]), float(t["o"])
            out[sym] = (last, (last / opn - 1) * 100 if opn else 0)
        except Exception as e:
            print(f"{sym} price error:", e)
    return out


def coin_label(sym, price, ch):
    dec = next((c[3] for c in COINS if c[1] == sym), 2)
    dot = "\U0001F7E2" if ch >= 0 else "\U0001F534"
    return f"{dot} {sym} ${price:,.{dec}f} ({ch:+.2f}%)"


def start_discord_bot():
    if not DISCORD_BOT_TOKEN:
        return
    try:
        import asyncio
        import threading
        import discord
        from discord import app_commands
    except Exception as e:
        print("discord.py ma kaynch:", e)
        return

    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    guild = discord.Object(id=int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None

    async def answer(inter, fn, *args):
        await inter.response.defer(thinking=True)
        try:
            text = await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))
        except Exception as e:
            text = f"Something went wrong: {e}"
        for c in _chunks(html_to_md(text)):
            await inter.followup.send(c, suppress_embeds=True)

    zing_group = app_commands.Group(name="zing", description="Track Cardano wallets (whales)")

    async def wallet_names(inter: discord.Interaction, current: str):
        cur = (current or "").lower()
        return [app_commands.Choice(name=n, value=n) for n in list(zings()) if cur in n.lower()][:25]

    @zing_group.command(name="wallet", description="Start tracking a wallet")
    @app_commands.describe(address="Wallet address (addr1... or stake1...)", name="A name for this wallet")
    async def z_wallet(inter: discord.Interaction, address: str, name: str):
        await answer(inter, zing_add, address, name)

    @zing_group.command(name="list", description="List the wallets you track")
    async def z_list(inter: discord.Interaction):
        await answer(inter, zing_list_text)

    @zing_group.command(name="remove", description="Stop tracking a wallet")
    @app_commands.describe(name="Wallet name")
    @app_commands.autocomplete(name=wallet_names)
    async def z_remove(inter: discord.Interaction, name: str):
        await answer(inter, zing_remove, name)

    tree.add_command(zing_group, guild=guild)

    pf_group = app_commands.Group(name="portfolio", description="Positions and PnL")

    @pf_group.command(name="view", description="Positions and PnL of a wallet (tracked name or address)")
    @app_commands.describe(wallet="Tracked wallet name, or any addr1… / stake1… address")
    @app_commands.autocomplete(wallet=wallet_names)
    async def pf_view(inter: discord.Interaction, wallet: str):
        await answer(inter, portfolio_text, wallet)

    @pf_group.command(name="set", description="Add/remove your own wallet on the #portfolio board")
    @app_commands.describe(address="Your wallet address (addr1… or stake1…)")
    async def pf_set(inter: discord.Interaction, address: str):
        await answer(inter, portfolio_set, address)

    tree.add_command(pf_group, guild=guild)

    @tree.command(name="trending", description="Top tokens by volume on DexHunter right now", guild=guild)
    async def c_trending(inter: discord.Interaction):
        await answer(inter, trending_text)

    async def ada_ticker():
        chans = {"ADA": ADA_PRICE_CHANNEL, "BTC": BTC_PRICE_CHANNEL, "ETH": ETH_PRICE_CHANNEL}
        last_rename = {}
        while True:
            try:
                px = await asyncio.get_running_loop().run_in_executor(None, coin_prices)
                if "ADA" in px:
                    await client.change_presence(activity=discord.Activity(
                        type=discord.ActivityType.watching, name=coin_label("ADA", *px["ADA"])))
                for sym, cid in chans.items():
                    if not cid or sym not in px or time.time() - last_rename.get(sym, 0) < ADA_RENAME_SEC:
                        continue
                    last_rename[sym] = time.time()
                    try:
                        label = coin_label(sym, *px[sym])
                        chan = client.get_channel(int(cid)) or await client.fetch_channel(int(cid))
                        if chan.name != label:
                            await chan.edit(name=label)
                    except Exception as e:
                        print(f"{sym} ticker error:", e)
            except Exception as e:
                print("Price ticker error:", e)
            await asyncio.sleep(60)

    started = [False]

    @client.event
    async def on_ready():
        try:
            synced = await tree.sync(guild=guild)
            print(f"Discord bot wajed: {client.user} ({len(synced)} commands)")
        except Exception as e:
            print("Discord sync error:", e)
        if not started[0]:
            started[0] = True
            asyncio.get_running_loop().create_task(ada_ticker())

    def runner():
        try:
            asyncio.run(client.start(DISCORD_BOT_TOKEN))
        except Exception as e:
            print("Discord bot error:", e)

    threading.Thread(target=runner, daemon=True).start()



# =============== snek.fun rich launch card ===============
_B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
SNEK_H = {"Origin": "https://snek.fun", "Referer": "https://snek.fun/", "User-Agent": "Mozilla/5.0",
          "Content-Type": "application/json"}


def _b32_polymod(values):
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _convertbits(data, frm, to, pad=True):
    acc, bits, ret, maxv = 0, 0, [], (1 << to) - 1
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (to - bits)) & maxv)
    return ret


def b32_encode(hrp, data):
    d = _convertbits(data, 8, 5)
    hx = [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
    pm = _b32_polymod(hx + d + [0] * 6) ^ 1
    return hrp + "1" + "".join(_B32[x] for x in d + [(pm >> 5 * (5 - i)) & 31 for i in range(6)])


def b32_decode(s):
    s = s.lower()
    pos = s.rfind("1")
    d = [_B32.find(c) for c in s[pos + 1:-6]]
    return bytes(_convertbits(d, 5, 8, False))


def addr_pkh(addr):
    try:
        return b32_decode(addr)[1:29].hex()
    except Exception:
        return ""


def stake_from_skh(skh):
    return b32_encode("stake", list(bytes.fromhex("e1" + skh)))


def short_addr(a):
    a = a or ""
    return f"{a[5:11]}…{a[-5:]}" if a.startswith("addr1") else (a[:8] + "…" if a else "?")


def snek_launched(pkh):
    """Lista dyal tokens li lanca had pkh f snek.fun."""
    if not pkh:
        return []
    try:
        r = S.post("https://in-app-wallet.snek.fun/v1/user-account/profile/launched-assets",
                   json={"pkhFundingWallet": [pkh]}, headers=SNEK_H, timeout=20)
        return (r.json() or {}).get("assets") or [] if r.ok else []
    except Exception as e:
        print("Snek launched error:", e)
        return []


def snek_ath(asset):
    """ATH dyal MCAP (ADA) mn charts dyal snek.fun."""
    try:
        r = S.get("https://charts.snek.fun/v1/charts/mcap/history",
                  params={"base": asset, "quote": ".", "from": 1700000000, "to": int(time.time()), "resolution": "day1"},
                  headers=SNEK_H, timeout=20)
        bars = r.json() if r.ok else []
        return max((b.get("high") or 0) for b in bars) / 1e6 if bars else 0
    except Exception:
        return 0


def _ago(sec):
    sec = max(0, sec)
    if sec >= 365 * 86400:
        return f"{sec / (365 * 86400):.1f} years"
    if sec >= 30 * 86400:
        return f"{sec / (30 * 86400):.0f} months"
    if sec >= 86400:
        return f"{sec / 86400:.0f} days"
    if sec >= 3600:
        return f"{sec / 3600:.0f} hours"
    return f"{max(1, sec // 60):.0f} min"


def _tickers(assets, skip=None, n=2):
    t = [a.get("ticker") or "?" for a in assets if a.get("asset") != skip]
    s = " ".join(f"`${x}`" for x in t[:n])
    return s + (f" `+{len(t) - n}`" if len(t) > n else "")


def snek_card(p):
    """Discord embed dyal launch jdid f snek.fun (Token / Dev / Funder / Contract / Links)."""
    info, pool, m = p.get("info") or {}, p.get("pool") or {}, p.get("metrics") or {}
    asset = info.get("asset") or ""
    tid = asset.replace(".", "")
    tick = str(info.get("ticker") or "?")
    created = pool.get("createdOn") or pool.get("timestamp") or int(time.time())
    mcap = (num(m.get("marketCap")) or 0) / 1e6
    cap = (num(pool.get("adaCapThreshold")) or 0) / 1e6
    author = info.get("poolAuthor") or {}
    pkh, skh = author.get("pkh") or "", author.get("skh") or ""
    snek_url = f"https://snek.fun/token/{tid}"

    # holders + dev address
    holders, dev_addr = None, ""
    try:
        r = S.get("https://analytics.snek.fun/balances/v1/pool/holders", params={"assetId": asset},
                  headers=SNEK_H, timeout=20)
        if r.ok:
            j = r.json()
            holders = j.get("count")
            dev_addr = (j.get("dev") or {}).get("address") or ""
    except Exception:
        pass
    dev_stake = stake_from_skh(skh) if skh else ""

    # dev buy (mn creation tx) + funder (mn awel tx dyal dev)
    dev_buy, age, funder, f_sent, f_time = None, None, "", None, None
    try:
        hashes = []
        create_tx = (info.get("outputId") or "").split(":")[0]
        if create_tx:
            hashes.append(create_tx)
        first = koios_get("account_txs", {"_stake_address": dev_stake, "order": "block_height.asc", "limit": 1}) \
            if dev_stake else []
        if isinstance(first, list) and first:
            age = created - (first[0].get("block_time") or created)
            hashes.append(first[0]["tx_hash"])
        txi = {t["tx_hash"]: t for t in (koios("tx_info", {"_tx_hashes": hashes, "_inputs": True, "_assets": True})
                                         if hashes else []) or []}
        ct = txi.get(create_tx)
        if ct:
            for o in ct.get("outputs") or []:
                if any(a.get("asset_name") == asset.split(".")[-1] and a.get("policy_id") == asset.split(".")[0]
                       for a in o.get("asset_list") or []) and o.get("stake_addr") != dev_stake:
                    v = (num(o.get("value")) or 0) / 1e6 - 3
                    dev_buy = max(dev_buy or 0, v)
        if isinstance(first, list) and first:
            ft = txi.get(first[0]["tx_hash"])
            if ft:
                ins = [u for u in ft.get("inputs") or [] if u.get("stake_addr") != dev_stake]
                if ins:
                    funder = ins[0]["payment_addr"]["bech32"]
                    f_sent = sum((num(o.get("value")) or 0) for o in ft.get("outputs") or []
                                 if o.get("stake_addr") == dev_stake) / 1e6
                    f_time = created - (ft.get("tx_timestamp") or created)
    except Exception as e:
        print("Snek card koios error:", e)

    # launches dyal dev w funder
    dev_l = snek_launched(pkh)
    prev = [a for a in dev_l if a.get("asset") != asset]
    best_t, best_mc = None, 0
    for a in prev[:12]:
        ath = snek_ath(a.get("asset"))
        if ath > best_mc:
            best_t, best_mc = a.get("ticker"), ath
    f_pkh = addr_pkh(funder) if funder else ""
    fund_l = snek_launched(f_pkh) if f_pkh and f_pkh != pkh else []

    cs = lambda a: f"https://cardanoscan.io/address/{a}"
    tok = [f"\U0001F4A7 MC `{kfmt(mcap)} ₳`",
           f"\U0001F465 Holders `{holders if holders is not None else '–'}`",
           f"\U0001F4BB Dev buy `{kfmt(dev_buy)} ₳`" if dev_buy and dev_buy > 0.5 else "\U0001F4BB Dev buy `–`",
           f"\U0001F4C8 Bonding `{min(100, (dev_buy or 0) / cap * 100):.0f}%`" if cap else "\U0001F4C8 Bonding `–`"]
    dev = [f"\U0001F464 Wallet [`{short_addr(dev_addr)}`]({cs(dev_addr)})" if dev_addr else "\U0001F464 Wallet `?`",
           f"⏳ Wallet age `{_ago(age)}`" if age is not None else "⏳ Wallet age `new`",
           (f"\U0001F680 Launches `{len(prev)}` " + _tickers(prev)) if prev else "\U0001F680 Launches `0` · first launch"]
    if best_t:
        dev.append(f"\U0001F3C6 Best `{kfmt(best_mc)} ₳ MC` on `${best_t}`")
    fund = []
    if funder:
        fund = [f"\U0001F3E6 Wallet [`{short_addr(funder)}`]({cs(funder)})"]
        if f_sent:
            fund.append(f"\U0001F4B8 Sent `{kfmt(f_sent)} ₳` {_ago(f_time)} before launch")
        fund.append((f"⚠️ Launches `{len(fund_l)}` " + _tickers(fund_l)) if fund_l else "✅ Launches `0`")
    soc = info.get("socials") or {}
    links = [f"[Trade on DexHunter](https://app.dexhunter.io/swap?tokenIdSell=&tokenIdBuy={tid})",
             f"[snek.fun]({snek_url})"]
    for k, lab in (("twitter", "X"), ("telegram", "Telegram"), ("discord", "Discord"), ("website", "Web")):
        if soc.get(k):
            links.append(f"[{lab}]({soc[k]})")
    desc = str(info.get("description") or "").strip()[:200]
    logo = info.get("logoCID")
    emb = {
        "author": {"name": "New coin on snek.fun · Cardano"},
        "title": f"${tick}",
        "url": snek_url,
        "color": 0xF5A623,
        "fields": [{"name": "Token", "value": "\n".join(tok)},
                   {"name": "Dev", "value": "\n".join(dev)}]
                  + ([{"name": "Funder", "value": "\n".join(fund)}] if fund else [])
                  + [{"name": "Contract", "value": f"```{tid}```"},
                     {"name": "Links", "value": " · ".join(links)}],
        "footer": {"text": "Notif DEX launch alert · not financial advice"},
        "timestamp": datetime.fromtimestamp(created, timezone.utc).isoformat(),
    }
    if desc:
        emb["description"] = desc
    if logo:
        emb["thumbnail"] = {"url": f"https://ipfs.io/ipfs/{logo}"}
        emb["author"]["icon_url"] = f"https://ipfs.io/ipfs/{logo}"
    return emb


def post_embed(chan_id, embed, webhook=None):
    body = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    try:
        if webhook:
            r = S.post(webhook, json=body, timeout=20)
        elif DISCORD_BOT_TOKEN and chan_id:
            r = S.post(f"https://discord.com/api/v10/channels/{chan_id}/messages", json=body,
                       headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}, timeout=20)
        else:
            print("EMBED:", embed.get("title"))
            return
        if r.status_code == 429:
            time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 10))
            r = (S.post(webhook, json=body, timeout=20) if webhook else
                 S.post(f"https://discord.com/api/v10/channels/{chan_id}/messages", json=body,
                        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}, timeout=20))
        if not r.ok:
            print("Embed post error:", r.status_code, r.text[:300])
    except Exception as e:
        print("Embed post exception:", e)


# =============== snek.fun new launches ===============
DISCORD_SNEKFUN = os.environ.get("DISCORD_SNEKFUN", "").strip()
SNEKFUN_EVERY = int(os.environ.get("SNEKFUN_EVERY_SEC", "60"))


def snek_send(text):
    if not DISCORD_SNEKFUN:
        bot_post(os.environ.get("SNEKFUN_CHANNEL", "").strip(), text)
        return
    body = {"content": html_to_md(text)[:1990], "flags": 4, "allowed_mentions": {"parse": []}}
    try:
        r = S.post(DISCORD_SNEKFUN, json=body, timeout=20)
        if r.status_code == 429:
            time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 10))
            S.post(DISCORD_SNEKFUN, json=body, timeout=20)
        elif not r.ok:
            print("Discord snekfun error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Discord snekfun exception:", e)


def check_snekfun():
    try:
        r = S.get("https://analytics.snek.fun/v1/pools-main-page/initial/state?filter=Latest",
                  headers={"Origin": "https://snek.fun", "Referer": "https://snek.fun/",
                           "User-Agent": "Mozilla/5.0"}, timeout=20)
        lst = r.json() if r.ok else None
    except Exception as e:
        print("Snekfun fetch error:", e)
        return
    if not isinstance(lst, list):
        print("Snekfun: jawab ghalat", getattr(r, "status_code", "?"))
        return
    seen = state.setdefault("snek_seen", [])
    first = not state.get("snek_init")
    if not first and not state.get("snek_bf2"):  # mra wa7da: nsifto akhir 3 launches bach tban l channel khddama
        state["snek_bf2"] = True
        newest = sorted(lst, key=lambda p: (p.get("pool") or {}).get("createdOn") or 0)[-3:]
        drop = {(p.get("info") or {}).get("asset") for p in newest}
        seen[:] = [a for a in seen if a not in drop]
    new = []
    for p in lst:
        info = p.get("info") or {}
        pool = p.get("pool") or {}
        asset = info.get("asset") or ""
        if not asset or asset in seen:
            continue
        seen.append(asset)
        new.append(p)
    state["snek_seen"] = seen[-500:]
    state["snek_init"] = True
    if first:
        print(f"Snekfun: bda, {len(new)} tokens deja kaynin (ma tsifet walo)")
        return
    new.sort(key=lambda p: (p.get("pool") or {}).get("createdOn") or 0)
    for p in new[-10:]:
        try:
            emb = snek_card(p)
        except Exception as e:
            print("Snek card error:", e)
            continue
        post_embed(os.environ.get("SNEKFUN_CHANNEL", "").strip(), emb, DISCORD_SNEKFUN or None)
        print("Snekfun new:", (p.get("info") or {}).get("ticker"))


# =============== dgens.fun + solada.fun new launches ===============
DISCORD_DGENS = os.environ.get("DISCORD_DGENS", "").strip()
DISCORD_SOLADA = os.environ.get("DISCORD_SOLADA", "").strip()
LAUNCH_EVERY = int(os.environ.get("LAUNCH_EVERY_SEC", "60"))
BROWSER_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/130.0 Safari/537.36", "Accept": "application/json"}


def hook_send(url, text, tag):
    if not url or url.startswith("chan:"):
        bot_post(url[5:] if url else "", text)
        return
    body = {"content": html_to_md(text)[:1990], "flags": 4, "allowed_mentions": {"parse": []}}
    try:
        r = S.post(url, json=body, timeout=20)
        if r.status_code == 429:
            time.sleep(min(float((r.json() or {}).get("retry_after", 2)), 10))
            S.post(url, json=body, timeout=20)
        elif not r.ok:
            print(f"Discord {tag} error:", r.status_code, r.text[:200])
    except Exception as e:
        print(f"Discord {tag} exception:", e)


def _new_launches(key, items, ident, created):
    """Kayrj3 ghir tokens jdad (ma tsifet walo f awel merra)."""
    seen = state.setdefault(key + "_seen", [])
    first = not state.get(key + "_init")
    new = []
    for c in items:
        i = ident(c)
        if not i or i in seen:
            continue
        seen.append(i)
        new.append(c)
    state[key + "_seen"] = seen[-1500:]
    state[key + "_init"] = True
    if first:
        print(f"{key}: bda, {len(new)} tokens deja kaynin (ma tsifet walo)")
        return []
    return sorted(new, key=created)[-10:]


def _when(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%d/%m %H:%M") if ms else ""


def _launch_msg(tick, name, when, desc, mcap, extra, ca, links):
    tick = html.escape(str(tick or "?"))
    name = html.escape(str(name or ""))
    head = f"\U0001F680 <b>NEW LAUNCH: ${tick}</b>" + (f" ({name})" if name and name.upper() != tick.upper() else "")
    lines = [head + (f" · {when}" if when else "")]
    desc = html.escape(str(desc or "").strip())[:200]
    if desc:
        lines.append(f"\U0001F4DD {desc}")
    lines.append(f"\U0001F4B0 MCAP: <b>{kfmt(mcap)} ADA</b>" + (f" · {extra}" if extra else ""))
    if ca:
        lines.append(f"\U0001F9FE CA: <code>{ca}</code>")
    lines.append("\U0001F517 " + " · ".join(links))
    return "\n".join(lines)


def stake_of(addr):
    """stake1… mn addr1… (base address)."""
    try:
        b = b32_decode(addr or "")
        if len(b) >= 57:
            return b32_encode("stake", [0xE1] + list(b[29:57]))
    except Exception:
        pass
    return ""


def dev_funder(dev_stake, created):
    """(wallet age sec, funder addr, ADA li sift, sec 9bel launch) mn awel Tx dyal dev."""
    out = {"age": None, "funder": "", "sent": None, "before": None}
    if not dev_stake:
        return out
    try:
        first = koios_get("account_txs", {"_stake_address": dev_stake, "order": "block_height.asc", "limit": 1})
        if not (isinstance(first, list) and first):
            return out
        out["age"] = created - (first[0].get("block_time") or created)
        ft = (koios("tx_info", {"_tx_hashes": [first[0]["tx_hash"]], "_inputs": True}) or [None])[0]
        if ft:
            ins = [u for u in ft.get("inputs") or [] if u.get("stake_addr") != dev_stake]
            if ins:
                out["funder"] = ins[0]["payment_addr"]["bech32"]
                out["sent"] = sum((num(o.get("value")) or 0) for o in ft.get("outputs") or []
                                  if o.get("stake_addr") == dev_stake) / 1e6
                out["before"] = created - (ft.get("tx_timestamp") or created)
    except Exception as e:
        print("Dev funder error:", e)
    return out


def launch_card(site, chain, tick, url, logo, desc, created, tok_lines, dev_addr, dev_extra, prev,
                fund, fund_prev, ca, links, mc_label="MC"):
    """Embed b nafs chkel dyal snek_card. prev/fund_prev = [(ticker, mcap)]."""
    cs = lambda a: f"https://cardanoscan.io/address/{a}"
    dev = [f"\U0001F464 Wallet [`{short_addr(dev_addr)}`]({cs(dev_addr)})" if dev_addr else "\U0001F464 Wallet `?`",
           f"\u23F3 Wallet age `{_ago(fund['age'])}`" if fund.get("age") is not None else "\u23F3 Wallet age `new`"]
    tk = lambda lst: " ".join(f"`${t}`" for t, _ in lst[:2]) + (f" `+{len(lst) - 2}`" if len(lst) > 2 else "")
    dev.append((f"\U0001F680 Launches `{len(prev)}` " + tk(prev)) if prev else "\U0001F680 Launches `0` · first launch")
    best = max(prev, key=lambda x: x[1] or 0) if prev else None
    if best and best[1]:
        dev.append(f"\U0001F3C6 Best `{kfmt(best[1])} ₳ {mc_label}` on `${best[0]}`")
    dev += dev_extra
    fl = []
    if fund.get("funder"):
        fl = [f"\U0001F3E6 Wallet [`{short_addr(fund['funder'])}`]({cs(fund['funder'])})"]
        if fund.get("sent"):
            fl.append(f"\U0001F4B8 Sent `{kfmt(fund['sent'])} ₳` {_ago(fund['before'])} before launch")
        fl.append((f"\u26A0\ufe0f Launches `{len(fund_prev)}` " + tk(fund_prev)) if fund_prev else "\u2705 Launches `0`")
    emb = {
        "author": {"name": f"New coin on {site} · {chain}"},
        "title": f"${tick}",
        "url": url,
        "color": 0xF5A623,
        "fields": [{"name": "Token", "value": "\n".join(tok_lines)},
                   {"name": "Dev", "value": "\n".join(dev)}]
                  + ([{"name": "Funder", "value": "\n".join(fl)}] if fl else [])
                  + ([{"name": "Contract", "value": f"```{ca}```"}] if ca else [])
                  + [{"name": "Links", "value": " · ".join(links)}],
        "footer": {"text": "Notif DEX launch alert · not financial advice"},
        "timestamp": datetime.fromtimestamp(created, timezone.utc).isoformat(),
    }
    if desc:
        emb["description"] = str(desc).strip()[:200]
    if logo:
        emb["thumbnail"] = {"url": logo}
        emb["author"]["icon_url"] = logo
    return emb


def _soc_links(lk):
    out = []
    for k, lab in (("twitter", "X"), ("telegram", "Telegram"), ("discord", "Discord"), ("website", "Web")):
        if (lk or {}).get(k):
            out.append(f"[{lab}]({lk[k]})")
    return out


def dgens_all():
    allc = []
    for off in range(0, 400, 50):
        try:
            r = S.get(f"https://dgens.fun/api/coins?chain=cardano&sort=new&limit=50&offset={off}",
                      headers=BROWSER_H, timeout=20)
            c = (r.json() or {}).get("coins") or [] if r.ok else []
        except Exception:
            c = []
        allc += c
        if len(c) < 50:
            break
    return allc


def dgens_card(c, allc):
    cid = c.get("id") or ""
    created = int((c.get("createdAt") or time.time() * 1000) / 1000)
    dev_addr = c.get("creator") or ""
    dst = stake_of(dev_addr)
    fund = dev_funder(dst, created)
    prev = [(x.get("ticker"), num(x.get("athMcap")) or num(x.get("mcap")) or 0) for x in allc
            if x.get("id") != cid and dst and stake_of(x.get("creator")) == dst]
    fst = stake_of(fund["funder"]) if fund.get("funder") else ""
    fprev = [(x.get("ticker"), num(x.get("athMcap")) or 0) for x in allc
             if fst and fst != dst and stake_of(x.get("creator")) == fst]
    dbuy = (num(c.get("devBuy")) or 0) / 1e6
    tok = [f"\U0001F4A7 MC `{kfmt(num(c.get('mcap')) or 0)} ₳`",
           f"\U0001F465 Holders `{c.get('holders') if c.get('holders') is not None else '–'}`",
           f"\U0001F4BB Dev buy `{kfmt(dbuy)} ₳`" if dbuy > 0.5 else "\U0001F4BB Dev buy `–`",
           f"\U0001F4C8 Bonding `{(num(c.get('progressBps')) or 0) / 100:.0f}%`"]
    if (c.get("devLock") or {}).get("tokens") and not (c.get("devLock") or {}).get("unlocked"):
        tok.append(f"\U0001F512 Dev tokens locked until `{datetime.fromtimestamp((c['devLock'].get('until') or 0) / 1000, TZ):%d/%m/%Y}`")
    extra = []
    if c.get("creatorRugs"):
        extra.append(f"\u2620\ufe0f Rugs `{c['creatorRugs']}`")
    url = f"https://dgens.fun/coin/cardano/{cid}"
    logo = ("https://dgens.fun" + c["image"]) if (c.get("image") or "").startswith("/") else c.get("image")
    return launch_card("dgens.fun", "Cardano", c.get("ticker") or "?", url, logo, c.get("description"), created,
                       tok, dev_addr, extra, prev, fund, fprev, cid,
                       [f"[Trade on dgens.fun]({url})"] + _soc_links(c.get("links")), mc_label="ATH MC")


def check_dgens():
    try:
        r = S.get("https://dgens.fun/api/coins?chain=cardano&sort=new&limit=50&offset=0",
                  headers=BROWSER_H, timeout=20)
        coins = (r.json() or {}).get("coins") if r.ok else None
    except Exception as e:
        print("Dgens fetch error:", e)
        return
    if not isinstance(coins, list):
        print("Dgens: jawab ghalat", getattr(r, "status_code", "?"))
        return
    if state.get("dgens_init") and not state.get("dgens_bf2"):
        state["dgens_bf2"] = True
        drop = {c.get("id") for c in sorted(coins, key=lambda c: c.get("createdAt") or 0)[-3:]}
        state["dgens_seen"] = [i for i in state.get("dgens_seen", []) if i not in drop]
    new = _new_launches("dgens", coins, lambda c: c.get("id"), lambda c: c.get("createdAt") or 0)
    allc = dgens_all() if new else []
    for c in new:
        try:
            emb = dgens_card(c, allc or coins)
        except Exception as e:
            print("Dgens card error:", e)
            continue
        post_embed(os.environ.get("DGENS_CHANNEL", "").strip(), emb, DISCORD_DGENS or None)
        print("Dgens new:", c.get("ticker"))


def solada_card(c, allc):
    sid = c.get("id") or ""
    slug = c.get("slug") or sid
    created = int((c.get("createdAtMs") or time.time() * 1000) / 1000)
    dev_addr = c.get("launcherAddress") or ""
    dst = stake_of(dev_addr)
    fund = dev_funder(dst, created)
    prev = [(x.get("ticker"), num(x.get("marketCapAda")) or 0) for x in allc
            if x.get("id") != sid and dst and stake_of(x.get("launcherAddress")) == dst]
    fst = stake_of(fund["funder"]) if fund.get("funder") else ""
    fprev = [(x.get("ticker"), num(x.get("marketCapAda")) or 0) for x in allc
             if fst and fst != dst and stake_of(x.get("launcherAddress")) == fst]
    dbuy, dpct = 0, None
    try:
        tr = S.get(f"https://www.solada.fun/api/coins/{sid}/trades", headers=BROWSER_H, timeout=20).json()
        dbuy = sum((num(t.get("lovelace")) or 0) for t in tr.get("trades") or []
                   if t.get("dev") and t.get("side") == "buy") / 1e6
        ho = S.get(f"https://www.solada.fun/api/coins/{sid}/holders", headers=BROWSER_H, timeout=20).json()
        dpct = (ho.get("dev") or {}).get("pct")
    except Exception:
        pass
    cv = c.get("curve") or {}
    bond = (num(cv.get("progressBps")) or num((c.get("pump") or {}).get("progressBps")) or 0) / 100
    tok = [f"\U0001F4A7 MC `{kfmt(num(c.get('marketCapAda')) or 0)} ₳`",
           f"\U0001F465 Holders `{c.get('holders') if c.get('holders') is not None else '–'}`",
           f"\U0001F4BB Dev buy `{kfmt(dbuy)} ₳`" + (f" · holds `{dpct:.1f}%`" if dpct else "") if dbuy > 0.5
           else "\U0001F4BB Dev buy `–`",
           f"\U0001F4C8 Bonding `{bond:.0f}%`"]
    pump = c.get("kind") == "pump"
    ca = c.get("pumpMint") if pump else ((c.get("policyId") or "") + (c.get("assetName") or ""))
    url = f"https://www.solada.fun/coin/{slug}"
    return launch_card("solada.fun", "Solana" if pump else "Cardano", c.get("ticker") or "?", url,
                       c.get("imageUri"), c.get("description"), created, tok, dev_addr, [], prev, fund, fprev,
                       ca, [f"[Trade on solada.fun]({url})"] + _soc_links(c.get("links")), mc_label="MC")


def check_solada():
    try:
        r = S.get("https://www.solada.fun/api/coins?view=cards", headers=BROWSER_H, timeout=30)
        coins = (r.json() or {}).get("coins") if r.ok else None
    except Exception as e:
        print("Solada fetch error:", e)
        return
    if not isinstance(coins, list):
        print("Solada: jawab ghalat", getattr(r, "status_code", "?"))
        return
    coins = [c for c in coins if not c.get("hidden")]
    if state.get("solada_init") and not state.get("solada_bf2"):
        state["solada_bf2"] = True
        drop = {c.get("id") for c in sorted(coins, key=lambda c: c.get("createdAtMs") or 0)[-3:]}
        state["solada_seen"] = [i for i in state.get("solada_seen", []) if i not in drop]
    new = _new_launches("solada", coins, lambda c: c.get("id"), lambda c: c.get("createdAtMs") or 0)
    if not new:
        return
    try:  # lista kamla (fiha launcherAddress) ghir mni kayn launch jdid
        full = (S.get("https://www.solada.fun/api/coins", headers=BROWSER_H, timeout=60).json() or {}).get("coins") or []
    except Exception:
        full = []
    byid = {x.get("id"): x for x in full}
    for c in new:
        try:
            emb = solada_card(byid.get(c.get("id")) or c, full)
        except Exception as e:
            print("Solada card error:", e)
            continue
        post_embed(os.environ.get("SOLADA_CHANNEL", "").strip(), emb, DISCORD_SOLADA or None)
        print("Solada new:", c.get("ticker"))


# =============== main ===============
def main():
    if not (KEY and TG_TOKEN and TG_CHAT):
        print("Khass DEXHUNTER_KEY, TG_TOKEN w TG_CHAT_ID f GitHub Secrets.")
        sys.exit(1)
    load_state()
    start_discord_bot()
    threading.Thread(target=zing_fast_loop, daemon=True).start()
    state["streaks"] = {}  # kat3awd tbna mn l'lookback
    seen = set()
    try:
        seen = set(json.load(open(SEEN_FILE)))
    except Exception:
        pass

    start = time.time()
    since = start - LOOKBACK_MIN * 60
    end = start + RUN_MINUTES * 60
    total = checks = 0
    last_wallet = 0
    last_zing = 0
    last_snek = 0
    last_launch = 0
    while True:
        loop_start = time.time()
        try:
            orders = fetch_since(since)
            a = process(orders, since, seen, quiet=(checks == 0))
        except Exception as e:
            print("Process error:", e)
            orders, a = [], 0
        total += a
        checks += 1
        if time.time() - last_wallet >= WALLET_EVERY:
            try:
                check_wallet(first=(last_wallet == 0 and checks == 1 and not state["wallet"]))
            except Exception as e:
                print("Wallet error:", e)
            last_wallet = time.time()
        if time.time() - last_zing >= max(ZING_SLOW_SEC, 20 * len(state.get("zings") or {})):
            try:
                check_zing()
            except Exception as e:
                print("Zing error:", e)
            last_zing = time.time()
        if time.time() - last_snek >= SNEKFUN_EVERY:
            try:
                check_snekfun()
            except Exception as e:
                print("Snekfun error:", e)
            last_snek = time.time()
        if time.time() - last_launch >= LAUNCH_EVERY:
            for fn in (check_dgens, check_solada):
                try:
                    fn()
                except Exception as e:
                    print(fn.__name__, "error:", e)
            last_launch = time.time()
        try:
            portfolio_board()
        except Exception as e:
            print("Portfolio board error:", e)
        maybe_summary()
        if checks == 1:
            print(f"Check 1: {len(orders)} ordres, {a} alerte(s), wallet {len(state['wallet'])} tokens")
            if TEST:
                tg(f"✅ Bot khddam: alerts fo9 {kfmt(default_min())} ADA, \U0001F525 clusters, "
                   f"wallet ({len(state['wallet'])} tokens), résumé f {SUMMARY_HOUR}:00."
                   + ("\nKtb /help bach tchouf l'commands." if COMMANDS else ""))
        try:
            json.dump(list(seen)[-5000:], open(SEEN_FILE, "w"))
        except Exception:
            pass
        save_state()
        since = loop_start - 600
        if RUN_MINUTES <= 0 or time.time() + POLL_SEC > end:
            break
        wait = max(1, POLL_SEC - (time.time() - loop_start))
        if COMMANDS:
            poll_commands(wait)
        else:
            time.sleep(wait)
    save_state()
    print(f"Sala: {checks} checks, {total} alerte(s)")


if __name__ == "__main__":
    main()
