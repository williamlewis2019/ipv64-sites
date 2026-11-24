#!/usr/bin/env python3
"""app.py — one container serving all three ipv64 sites on Cloady.

Pure stdlib. Listens on $PORT (Cloady routes to it; default 8080) and routes
by Host header:
  doctor.ipv64.de (and any other host, incl. the built-in *.cloady.io
  hostname) -> live status page, re-rendered every 300 s in a background
  thread (checks 3 sites, reads R2 backup generations via SigV4);
  student.ipv64.de -> static/student/index.html (capacity dashboard,
  refreshed daily by pushing a new copy to the Git repo);
  teacher.ipv64.de -> static/teacher/*.html (document archive, static).

Design: broadsheet / gazette style — warm paper, ink black, serif display
type, hairline rules, one vermillion accent (the seal stamp). No gradients,
no glow, no animation.
"""
import hashlib
import hmac
import html
import os
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or "8080")
SITES = [
    ("Agricalc", "农业计算器", "https://agricalc.online/"),
    ("PlayVizo", "在线游戏站", "https://playvizo.com/"),
    ("PayStubs", "工资单工具", "https://paystubs.shop/"),
]
STATE = {"page": "<h1>starting…</h1>", "at": 0.0}
CN_NUM = "一二三四五六七八九"
WEEKDAYS = "一二三四五六日"

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#f6f1e7; color:#1d1a13; padding:46px 20px 38px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased; }
.sheet { max-width:720px; margin:0 auto; }
.serif { font-family:Georgia,"Times New Roman","Songti SC","STSong","Noto Serif SC",serif; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.mast { display:flex; justify-content:space-between; align-items:flex-end; padding-bottom:12px; }
.mast-l { font-size:31px; font-weight:700; letter-spacing:.16em; }
.mast-r { font-size:10.5px; color:#6f6a5c; text-align:right; line-height:1.8; letter-spacing:.1em; }
.rule-double { border-top:3.5px solid #1d1a13; }
.rule-double::after { content:""; display:block; border-top:1px solid #1d1a13; margin-top:2.5px; }
.headline { padding:36px 0 6px; position:relative; }
.stamp { position:absolute; right:4px; top:34px; width:54px; height:54px; background:#bc3f16; color:#fdf6ec;
  display:flex; align-items:center; justify-content:center; font-size:27px; font-weight:700;
  transform:rotate(-4deg); border-radius:3px; opacity:.94; }
h1.big { font-size:54px; font-weight:700; letter-spacing:.03em; line-height:1.15; }
.lede { margin-top:12px; color:#57523f; font-size:15px; }
.sec { display:flex; justify-content:space-between; align-items:baseline; margin:36px 0 2px;
  padding-bottom:7px; border-bottom:2px solid #1d1a13; font-size:15px; letter-spacing:.26em; font-weight:700; }
.sec-r { font-size:10px; letter-spacing:.2em; color:#8a8471; font-weight:400; }
.row { display:flex; justify-content:space-between; align-items:center; gap:16px;
  padding:16px 2px; border-bottom:1px solid #ddd3bc; }
.r-name { font-size:21px; font-weight:700; }
.r-sub { font-size:13px; color:#6f6a5c; font-weight:400; margin-left:9px; letter-spacing:.05em; }
.r-url { font-size:11px; color:#8a8471; margin-top:4px; letter-spacing:.04em; }
.r-side { text-align:right; flex:none; }
.r-status { font-size:14.5px; font-weight:600; display:flex; align-items:center; gap:7px; justify-content:flex-end; }
.dot { width:8px; height:8px; border-radius:50%; background:#3d6b35; display:inline-block; }
.dot.bad { background:#bc3f16; }
.r-status.bad { color:#bc3f16; }
.r-lat { font-size:11px; color:#8a8471; margin-top:5px; letter-spacing:.03em; }
.tally { display:flex; gap:4px; justify-content:flex-end; align-items:center; }
.tally i { width:10px; height:10px; background:#1d1a13; display:inline-block; }
.tally i.off { background:transparent; border:1.5px solid #b9b09a; width:7px; height:7px; }
.tally span { margin-left:9px; font-size:12.5px; color:#57523f; }
.cols { display:grid; grid-template-columns:1fr 1fr 1.5fr; }
.col { padding:18px 18px 2px 0; }
.col + .col { border-left:1px solid #ddd3bc; padding-left:18px; }
.col .k { font-size:11.5px; letter-spacing:.24em; color:#6f6a5c; }
.col .v { font-size:21px; font-weight:700; margin-top:8px; font-variant-numeric:tabular-nums; }
.meter { height:5px; background:#e3dbc7; margin-top:12px; position:relative; }
.meter i { position:absolute; top:0; bottom:0; left:0; background:#1d1a13; display:block; }
footer { margin-top:40px; padding-top:12px; border-top:1px solid #1d1a13;
  font-size:10.5px; color:#8a8471; display:flex; justify-content:space-between; letter-spacing:.05em; }
@media (max-width:560px) {
  h1.big { font-size:38px; } .cols { grid-template-columns:1fr; }
  .col + .col { border-left:none; padding-left:0; }
  .stamp { width:45px; height:45px; font-size:22px; top:28px; }
  .mast-l { font-size:24px; }
}
"""


def load_env():
    env = {}
    for key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    if "R2_ACCESS_KEY_ID" not in env:
        for name in ("r2-ro.env", ".r2-ro.env"):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
            try:
                for line in open(path):
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        env[k] = v
                break
            except FileNotFoundError:
                continue
    return env


def check_site(url):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "doctor-status/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read(1024)
            return r.status, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0
    except Exception:
        return 0, time.time() - t0


def _sign(key, msg):
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def r2_list(env):
    ak = env["R2_ACCESS_KEY_ID"]
    sk = env["R2_SECRET_ACCESS_KEY"]
    host = env["R2_ENDPOINT"].split("://", 1)[1].rstrip("/")
    bucket = env.get("R2_BUCKET", "server-backups")
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    canonical_uri = f"/{bucket}"
    query = "list-type=2"
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(["GET", canonical_uri, query, canonical_headers, signed, payload_hash])
    scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                                hashlib.sha256(canonical_request.encode()).hexdigest()])
    k_date = _sign(("AWS4" + sk).encode(), date_stamp)
    k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    auth = (f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}")
    url = f"https://{host}{canonical_uri}?{query}"
    req = urllib.request.Request(url, headers={
        "Authorization": auth, "x-amz-date": amz_date, "x-amz-content-sha256": payload_hash})
    with urllib.request.urlopen(req, timeout=30) as r:
        root = ET.fromstring(r.read())
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    out = []
    for c in root.iter(f"{ns}Contents"):
        key = c.findtext(f"{ns}Key") or ""
        size = int(c.findtext(f"{ns}Size") or 0)
        lastmod = c.findtext(f"{ns}LastModified") or ""
        out.append((key, size, lastmod))
    return out


def tally_html(n):
    filled = min(n, 4)
    squares = "".join("<i></i>" for _ in range(filled)) + \
              "".join('<i class="off"></i>' for _ in range(max(0, 4 - filled)))
    label = f"{CN_NUM[n - 1]}代存档" if 1 <= n <= 9 else f"{n} 代存档"
    return f'<div class="tally">{squares}<span>{label}</span></div>'


def backup_row(env):
    try:
        objects = r2_list(env)
    except Exception as e:
        return False, (
            '<div class="row"><div><div class="r-name serif">异地备份'
            '<span class="r-sub">Cloudflare R2</span></div>'
            f'<div class="r-url mono">读不到备份桶 · {html.escape(type(e).__name__)}</div></div>'
            '<div class="r-side"><div class="r-status bad"><span class="dot bad"></span>异常</div></div></div>')
    if not objects:
        return False, (
            '<div class="row"><div><div class="r-name serif">异地备份'
            '<span class="r-sub">Cloudflare R2</span></div>'
            '<div class="r-url mono">备份桶是空的</div></div>'
            '<div class="r-side"><div class="r-status bad"><span class="dot bad"></span>无备份</div></div></div>')
    gens = {}
    for key, size, lastmod in objects:
        gen = key.split("/", 1)[0]
        g = gens.setdefault(gen, {"n": 0, "bytes": 0, "last": ""})
        g["n"] += 1
        g["bytes"] += size
        g["last"] = max(g["last"], lastmod)
    newest = sorted(gens)[-1]
    g = gens[newest]
    last_utc = g["last"].replace("T", " ").split(".")[0]
    return True, (
        '<div class="row"><div><div class="r-name serif">异地备份'
        '<span class="r-sub">Cloudflare R2</span></div>'
        f'<div class="r-url mono">最近一代 {html.escape(newest)} · {g["n"]} 个文件 · '
        f'{g["bytes"] / 1e6:.0f} MB · 上传于 {html.escape(last_utc)} UTC</div></div>'
        f'<div class="r-side">{tally_html(len(gens))}'
        '<div class="r-lat">每周日 04:15 运行 · 保留四代</div></div></div>')


def host_stats():
    try:
        up = float(open("/proc/uptime").read().split()[0])
        uptime = f"{int(up // 86400)} 天 {int(up % 86400 // 3600)} 小时"
    except Exception:
        uptime = "未知"
    try:
        load = open("/proc/loadavg").read().split()[0]
    except Exception:
        load = "未知"
    try:
        st = os.statvfs("/")
        free_gb = st.f_bavail * st.f_frsize / 1e9
        total_gb = st.f_blocks * st.f_frsize / 1e9
        used_pct = max(0.0, min(100.0, (total_gb - free_gb) / total_gb * 100)) if total_gb else 0.0
        disk = (free_gb, total_gb, used_pct)
    except Exception:
        disk = None
    return uptime, load, disk


def render():
    env = load_env()
    now_dt = datetime.now()
    now = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    date_line = f"{now_dt.year}年{now_dt.month}月{now_dt.day}日 · 星期{WEEKDAYS[now_dt.weekday()]}"
    rows = []
    all_ok = True
    for name, sub, url in SITES:
        code, secs = check_site(url)
        ok = code == 200
        all_ok = all_ok and ok
        host = url.replace("https://", "").rstrip("/")
        if ok:
            status = '<div class="r-status"><span class="dot"></span>正常</div>'
            lat = f'<div class="r-lat mono">响应 {secs:.2f} 秒 · HTTP {code}</div>'
        else:
            status = '<div class="r-status bad"><span class="dot bad"></span>异常</div>'
            lat = f'<div class="r-lat mono">HTTP {code or "无响应"}</div>'
        rows.append(
            f'<div class="row"><div><div class="r-name serif">{html.escape(name)}'
            f'<span class="r-sub">{html.escape(sub)}</span></div>'
            f'<div class="r-url mono">{html.escape(host)}</div></div>'
            f'<div class="r-side">{status}{lat}</div></div>')
    b_ok, b_row = backup_row(env)
    all_ok = all_ok and b_ok
    uptime, load, disk = host_stats()
    if disk:
        free_gb, total_gb, used_pct = disk
        disk_v = f"剩余 {free_gb:.1f} / {total_gb:.0f} GB"
        disk_bar = f'<div class="meter"><i style="width:{used_pct:.0f}%"></i></div>'
    else:
        disk_v, disk_bar = "未知", ""
    stamp = "安" if all_ok else "恙"
    big = "一切正常" if all_ok else "有服务异常"
    lede = ("三站巡检无碍，备份按期存档。本页独立刊印于 Cloady，主服务器有恙时照常出刊。" if all_ok
            else "部分服务出现异常，详情见下表。")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>服务公报 · doctor.ipv64.de</title>
<style>{CSS}</style>
</head>
<body>
<div class="sheet">
  <header class="mast">
    <div class="mast-l serif">服务公报</div>
    <div class="mast-r mono">DOCTOR.IPV64.DE<br>{date_line}</div>
  </header>
  <div class="rule-double"></div>
  <section class="headline">
    <div class="stamp serif">{stamp}</div>
    <h1 class="big serif">{big}</h1>
    <p class="lede">{lede}</p>
  </section>
  <h2 class="sec">网站服务<span class="sec-r mono">SERVICES</span></h2>
  {''.join(rows)}
  <h2 class="sec">数据备份<span class="sec-r mono">ARCHIVE</span></h2>
  {b_row}
  <h2 class="sec">本页主机<span class="sec-r mono">CLOADY</span></h2>
  <div class="cols">
    <div class="col"><div class="k">运行时间</div><div class="v serif">{html.escape(uptime)}</div></div>
    <div class="col"><div class="k">系统负载</div><div class="v serif">{html.escape(load)}</div></div>
    <div class="col"><div class="k">磁盘空间</div><div class="v serif">{html.escape(disk_v)}</div>{disk_bar}</div>
  </div>
  <footer class="mono"><span>DOCTOR.IPV64.DE · 刊印于 CLOADY</span><span>每五分钟刊印一次 · 本期 {now}</span></footer>
</div>
</body>
</html>
"""


def refresher():
    while True:
        try:
            STATE["page"] = render()
            STATE["at"] = time.time()
        except Exception:
            pass
        time.sleep(300)


# --- static sites (student / teacher) --------------------------------------
# Files baked into the image at deploy time under ./static/<site>/.
STATIC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
HOST_SITES = {
    "student.ipv64.de": "student",
    "teacher.ipv64.de": "teacher",
}
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".xml": "application/xml",
}


def serve_static(site, path):
    """Return (bytes, content_type) for a file under static/<site>/, or None.

    Path traversal is blocked: the resolved path must stay inside the site
    directory. A bare ``/<slug>`` also tries ``/<slug>.html`` (teacher links).
    """
    root = os.path.join(STATIC_ROOT, site)
    if path in ("", "/"):
        path = "/index.html"
    rel = os.path.normpath(path.lstrip("/"))
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    full = os.path.join(root, rel)
    if not os.path.isfile(full):
        if not rel.endswith(".html"):
            cand = full + ".html"
            if os.path.isfile(cand):
                full = cand
            else:
                return None
        else:
            return None
    with open(full, "rb") as f:
        data = f.read()
    ctype = MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
    return data, ctype


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def _route(self):
        if urllib.parse.urlparse(self.path).path == "/healthz":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
            return
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        site = HOST_SITES.get(host)
        if site is None:
            # doctor page: the default for doctor.ipv64.de and for any other
            # host, including the built-in *.cloady.io hostname.
            self._send(200, STATE["page"].encode(), "text/html; charset=utf-8")
            return
        res = serve_static(site, urllib.parse.urlparse(self.path).path)
        if res is None:
            self._send(404, b"404 not found\n", "text/plain; charset=utf-8")
            return
        data, ctype = res
        self._send(200, data, ctype)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=refresher, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
