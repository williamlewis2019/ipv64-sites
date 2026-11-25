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

Design: restrained big-company dashboard — light gray background, white
cards, one blue accent (#2563eb), pill status labels, thin progress bars.
No gradients, no glow, no animation.
"""
import hashlib
import hmac
import html
import json
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
body { background:#f7f8fa; color:#111827; padding:0 20px 40px;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased; }
.wrap { max-width:1024px; margin:0 auto; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
.topnav { display:flex; gap:4px; padding:14px 0 2px; }
.topnav a { font-size:13px; color:#6b7280; text-decoration:none; padding:6px 12px; border-radius:8px; }
.topnav a:hover { color:#111827; background:#eef0f3; }
.topnav a.active { color:#2563eb; font-weight:600; background:#eff6ff; }
.mast { display:flex; justify-content:space-between; align-items:flex-end; padding:22px 0 4px; }
.mast-l { font-size:24px; font-weight:700; }
.mast-r { font-size:12px; color:#6b7280; text-align:right; line-height:1.7; }
.hero { margin-top:16px; background:#fff; border:1px solid #e5e7eb; border-radius:12px;
  box-shadow:0 1px 2px rgba(16,24,40,.04); padding:24px; }
.hero .h-top { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.hero h1 { font-size:28px; font-weight:700; }
.hero p { margin-top:10px; color:#4b5563; font-size:14px; line-height:1.7; }
.pill { display:inline-flex; align-items:center; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:600; white-space:nowrap; }
.pill.ok { background:#dcfce7; color:#15803d; }
.pill.bad { background:#fee2e2; color:#b91c1c; }
.pill.warn { background:#fef3c7; color:#b45309; }
.pill.muted { background:#f3f4f6; color:#4b5563; }
.sec { margin:32px 0 12px; font-size:12px; font-weight:600; letter-spacing:.08em; color:#6b7280; }
.card { background:#fff; border:1px solid #e5e7eb; border-radius:12px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
.row { display:flex; justify-content:space-between; align-items:center; gap:16px; padding:18px 22px; }
.row + .row { border-top:1px solid #eef0f3; }
.r-name { font-size:16px; font-weight:600; }
.r-sub { font-size:13px; color:#6b7280; font-weight:400; margin-left:8px; }
.r-url { font-size:12px; color:#9ca3af; margin-top:3px; }
.r-side { text-align:right; flex:none; }
.r-lat { font-size:12px; color:#6b7280; margin-top:5px; }
.tally { font-size:12px; color:#4b5563; font-weight:600; }
.stats { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
.stat { background:#fff; border:1px solid #e5e7eb; border-radius:12px; box-shadow:0 1px 2px rgba(16,24,40,.04); padding:20px 22px; }
.stat .k { font-size:12px; font-weight:600; letter-spacing:.08em; color:#6b7280; }
.stat .v { font-size:26px; font-weight:700; margin-top:8px; font-variant-numeric:tabular-nums; }
.bar { height:6px; background:#e5e7eb; border-radius:999px; margin-top:12px; overflow:hidden; }
.bar i { display:block; height:100%; background:#2563eb; border-radius:999px; }
.prog { padding:4px 22px 16px; }
.prog-item { padding:14px 0; border-top:1px solid #eef0f3; }
.prog-item:first-child { border-top:none; }
.prog-top { display:flex; justify-content:space-between; align-items:baseline; gap:16px; }
.prog-name { font-size:15px; font-weight:600; }
.prog-num { font-size:15px; font-weight:700; font-variant-numeric:tabular-nums; white-space:nowrap; }
.prog-num small { font-size:12px; color:#6b7280; font-weight:400; }
.prog-detail { margin-top:6px; font-size:13px; color:#4b5563; }
.prog-more { display:block; margin-top:14px; font-size:13px; color:#2563eb; text-decoration:none; }
footer { margin-top:36px; padding-top:14px; border-top:1px solid #e5e7eb;
  font-size:12px; color:#9ca3af; display:flex; justify-content:space-between; }
footer a { color:#6b7280; }
@media (max-width:640px) {
  .stats { grid-template-columns:repeat(3,1fr); gap:10px; }
  .stat { padding:14px; } .stat .v { font-size:20px; }
  .mast-l { font-size:20px; } .hero h1 { font-size:23px; }
  .row { padding:16px; }
}
@media (max-width:560px) { .stats { grid-template-columns:1fr; } }
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
    label = f"{n} 代存档" if n >= 1 else "无存档"
    return f'<div class="tally">{label}</div>'


def backup_row(env):
    try:
        objects = r2_list(env)
    except Exception as e:
        return False, (
            '<div class="row"><div><div class="r-name">异地备份'
            '<span class="r-sub">Cloudflare R2</span></div>'
            f'<div class="r-url mono">读不到备份桶 · {html.escape(type(e).__name__)}</div></div>'
            '<div class="r-side"><span class="pill bad">异常</span></div></div>')
    if not objects:
        return False, (
            '<div class="row"><div><div class="r-name">异地备份'
            '<span class="r-sub">Cloudflare R2</span></div>'
            '<div class="r-url mono">备份桶是空的</div></div>'
            '<div class="r-side"><span class="pill bad">无备份</span></div></div>')
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
        '<div class="row"><div><div class="r-name">异地备份'
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


def nav_html(active):
    links = [
        ("https://doctor.ipv64.de", "服务状态", "doctor"),
        ("https://student.ipv64.de", "产能看板", "student"),
        ("https://teacher.ipv64.de", "文档公报", "teacher"),
    ]
    out = []
    for url, label, key in links:
        cls = ' class="active"' if key == active else ""
        out.append(f'<a href="{url}"{cls}>{label}</a>')
    return '<nav class="topnav">' + "".join(out) + "</nav>"


def load_progress():
    """Progress data: static/progress.json in the deployed image first,
    sandbox copy as a local-dev fallback. Missing file -> empty groups."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "progress.json")
    for path in (here, "/home/hatch/workspace/student/progress.json"):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            continue
    return {"in_progress": [], "done": [], "upcoming": []}


def progress_compact_html(p):
    items = p.get("in_progress", [])[:4]
    rows = []
    for it in items:
        done, total = it.get("done"), it.get("total")
        if done is not None and total:
            pct = done / total * 100
            num = f'{done} <small>/ {total}</small>'
            bar = f'<div class="bar"><i style="width:{pct:.0f}%"></i></div>'
        else:
            num = '<span class="pill muted">进行中</span>'
            bar = ""
        rows.append(
            f'<div class="prog-item"><div class="prog-top">'
            f'<div class="prog-name">{html.escape(it["name"])}</div>'
            f'<div class="prog-num">{num}</div></div>{bar}'
            f'<div class="prog-detail">{html.escape(it.get("detail", ""))}</div></div>')
    if not rows:
        return ""
    return ('<div class="sec">任务进度</div><div class="card"><div class="prog">'
            + "".join(rows) +
            '<a class="prog-more" href="https://student.ipv64.de">完整进度见产能看板 →</a>'
            '</div></div>')


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
            status = '<span class="pill ok">正常</span>'
            lat = f'<div class="r-lat mono">响应 {secs:.2f} 秒 · HTTP {code}</div>'
        else:
            status = '<span class="pill bad">异常</span>'
            lat = f'<div class="r-lat mono">HTTP {code or "无响应"}</div>'
        rows.append(
            f'<div class="row"><div><div class="r-name">{html.escape(name)}'
            f'<span class="r-sub">{html.escape(sub)}</span></div>'
            f'<div class="r-url mono">{html.escape(host)}</div></div>'
            f'<div class="r-side">{status}{lat}</div></div>')
    b_ok, b_row = backup_row(env)
    all_ok = all_ok and b_ok
    uptime, load, disk = host_stats()
    if disk:
        free_gb, total_gb, used_pct = disk
        disk_v = f"剩余 {free_gb:.1f} / {total_gb:.0f} GB"
        disk_bar = f'<div class="bar"><i style="width:{used_pct:.0f}%"></i></div>'
    else:
        disk_v, disk_bar = "未知", ""
    big = "一切正常" if all_ok else "有服务异常"
    pill = '<span class="pill ok">全部正常</span>' if all_ok else '<span class="pill bad">有异常</span>'
    lede = ("三站巡检无碍，备份按期存档。本页由 Cloady 托管，主服务器有恙时照常更新。" if all_ok
            else "部分服务出现异常，详情见下表。")
    progress = progress_compact_html(load_progress())
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>服务状态 · doctor.ipv64.de</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  {nav_html("doctor")}
  <header class="mast">
    <div class="mast-l">服务状态</div>
    <div class="mast-r mono">DOCTOR.IPV64.DE<br>{date_line}</div>
  </header>
  <section class="hero">
    <div class="h-top"><h1>{big}</h1>{pill}</div>
    <p>{lede}</p>
  </section>
  <div class="sec">网站服务</div>
  <div class="card">{''.join(rows)}</div>
  <div class="sec">数据备份</div>
  <div class="card">{b_row}</div>
  <div class="sec">本页主机</div>
  <div class="stats">
    <div class="stat"><div class="k">运行时间</div><div class="v">{html.escape(uptime)}</div></div>
    <div class="stat"><div class="k">系统负载</div><div class="v">{html.escape(load)}</div></div>
    <div class="stat"><div class="k">磁盘空间</div><div class="v">{html.escape(disk_v)}</div>{disk_bar}</div>
  </div>
  {progress}
  <footer class="mono"><span>ipv64-sites · Cloady</span><span>每五分钟更新一次 · 本期 {now}</span></footer>
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
    "student.hidns.vip": "student",
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
