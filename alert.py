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


def save_state():
    try:
        json.dump(state, open(STATE_FILE, "w"))
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


def koios(path, body):
    try:
        r = S.post(f"{KOIOS}/{path}", json=body, timeout=30)
        return r.json() if r.ok else []
    except Exception as e:
        print("Koios error:", e)
        return []


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
    try:
        r = S.get(f"{KOIOS}/{path}", params=params, timeout=30)
        return r.json() if r.ok else []
    except Exception as e:
        print("Koios error:", e)
        return []


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
    z[name] = {"addr": addr, "stake": stake, "height": tip[0].get("block_no", 0) if tip else 0, "pending": []}
    save_state()
    return (f"✅ Now tracking <b>{name}</b> \U0001F433\n{addr_link(addr)}\n"
            f"Every buy, sell, send and receive will be posted in #zing-wallet.")


def zing_remove(key):
    name, w = find_zing(key)
    if not name:
        return f"❌ No tracked wallet named <b>{key}</b>. See /zing list."
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


def check_zing():
    z = zings()
    if not z:
        return
    tip = None
    for name, w in list(z.items()):
        if not isinstance(w.get("pending"), list):
            w["pending"] = []
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
        for t in txs:
            tx = by.get(t["tx_hash"])
            if not tx:
                continue
            try:
                for m in zing_classify(tx, w["stake"], w["pending"], name):
                    zing_send(m)
            except Exception as e:
                print("Zing classify error:", e)
            w["height"] = max(w["height"], t.get("block_height", 0))
        w["pending"] = w["pending"][-60:]


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

    @tree.command(name="trending", description="Top tokens by volume on DexHunter right now", guild=guild)
    async def c_trending(inter: discord.Interaction):
        await answer(inter, trending_text)

    @client.event
    async def on_ready():
        try:
            synced = await tree.sync(guild=guild)
            print(f"Discord bot wajed: {client.user} ({len(synced)} commands)")
        except Exception as e:
            print("Discord sync error:", e)

    def runner():
        try:
            asyncio.run(client.start(DISCORD_BOT_TOKEN))
        except Exception as e:
            print("Discord bot error:", e)

    threading.Thread(target=runner, daemon=True).start()



# =============== snek.fun new launches ===============
DISCORD_SNEKFUN = os.environ.get("DISCORD_SNEKFUN", "").strip()
SNEKFUN_EVERY = int(os.environ.get("SNEKFUN_EVERY_SEC", "60"))


def snek_send(text):
    if not DISCORD_SNEKFUN:
        print("SNEKFUN:", text[:200])
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
        info, pool, m = p.get("info") or {}, p.get("pool") or {}, p.get("metrics") or {}
        tid = info["asset"].replace(".", "")
        tick = html.escape(str(info.get("ticker") or "?"))
        desc = html.escape(str(info.get("description") or "").strip())[:200]
        mcap = (num(m.get("marketCap")) or 0) / 1e6
        vol = (num(m.get("totalVolumeAda")) or 0) / 1e6
        cap = (num(pool.get("adaCapThreshold")) or 0) / 1e6
        ts = pool.get("createdOn") or pool.get("timestamp")
        when = datetime.fromtimestamp(ts, TZ).strftime("%d/%m %H:%M") if ts else ""
        soc = info.get("socials") or {}
        links = [f'<a href="https://snek.fun/token/{tid}">snek.fun</a>',
                 f'<a href="https://app.dexhunter.io/swap?tokenIdSell=&tokenIdBuy={tid}">DexHunter</a>']
        for k, lab in (("twitter", "X"), ("telegram", "Telegram"), ("discord", "Discord"), ("website", "Web")):
            if soc.get(k):
                links.append(f'<a href="{html.escape(soc[k])}">{lab}</a>')
        lines = [f"\U0001F680 <b>NEW LAUNCH: ${tick}</b>" + (f" · {when}" if when else "")]
        if desc:
            lines.append(f"\U0001F4DD {desc}")
        lines.append(f"\U0001F4B0 MCAP: <b>{kfmt(mcap)} ADA</b> · Vol: {kfmt(vol)} ADA"
                     + (f" · Bonding: {kfmt(cap)} ADA" if cap else ""))
        lines.append(f"\U0001F9FE CA: <code>{tid}</code>")
        lines.append("\U0001F517 " + " · ".join(links))
        snek_send("\n".join(lines))
        print("Snekfun new:", info.get("ticker"))


# =============== dgens.fun + solada.fun new launches ===============
DISCORD_DGENS = os.environ.get("DISCORD_DGENS", "").strip()
DISCORD_SOLADA = os.environ.get("DISCORD_SOLADA", "").strip()
LAUNCH_EVERY = int(os.environ.get("LAUNCH_EVERY_SEC", "60"))
BROWSER_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/130.0 Safari/537.36", "Accept": "application/json"}


def hook_send(url, text, tag):
    if not url:
        print(f"{tag}:", text[:200])
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
    for c in _new_launches("dgens", coins, lambda c: c.get("id"), lambda c: c.get("createdAt") or 0):
        cid = c.get("id") or ""
        links = [f'<a href="https://dgens.fun/coin/cardano/{cid}">dgens.fun</a>']
        for k, lab in (("twitter", "X"), ("telegram", "Telegram"), ("discord", "Discord"), ("website", "Web")):
            u = (c.get("links") or {}).get(k)
            if u:
                links.append(f'<a href="{html.escape(u)}">{lab}</a>')
        dev = c.get("creatorName") or ""
        extra = (f"Dev buy: {kfmt((num(c.get('devBuy')) or 0) / 1e6)} ADA" if c.get("devBuy") else "")
        if dev:
            extra += (" · " if extra else "") + f"Dev: {html.escape(dev)} ({c.get('creatorLaunches', 0)} launches, {c.get('creatorRugs', 0)} rugs)"
        hook_send(DISCORD_DGENS, _launch_msg(c.get("ticker"), c.get("name"), _when(c.get("createdAt")),
                                             c.get("description"), num(c.get("mcap")) or 0, extra, cid, links), "DGENS")
        print("Dgens new:", c.get("ticker"))


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
    for c in _new_launches("solada", coins, lambda c: c.get("id"), lambda c: c.get("createdAtMs") or 0):
        slug = c.get("slug") or c.get("id") or ""
        kind = "Solana (pump)" if c.get("kind") == "pump" else "Cardano curve"
        cv = c.get("curve") or {}
        extra = kind + (f" · Grad: {kfmt(cv.get('graduationAda'))} ADA" if cv.get("graduationAda") else "")
        links = [f'<a href="https://www.solada.fun/coin/{slug}">solada.fun</a>']
        hook_send(DISCORD_SOLADA, _launch_msg(c.get("ticker"), c.get("name"), _when(c.get("createdAtMs")),
                                              "", num(c.get("marketCapAda")) or 0, extra, "", links), "SOLADA")
        print("Solada new:", c.get("ticker"))


# =============== main ===============
def main():
    if not (KEY and TG_TOKEN and TG_CHAT):
        print("Khass DEXHUNTER_KEY, TG_TOKEN w TG_CHAT_ID f GitHub Secrets.")
        sys.exit(1)
    load_state()
    start_discord_bot()
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
        if time.time() - last_zing >= max(ZING_EVERY, 20 * len(state.get("zings") or {})):
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
