#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cumt_net.py —— 中国矿业大学 校园网（Dr.COM 老版门户）自动登录

实测结论（2026-10-26 在矿大 CUMT_Stu 上验证）：
  · 认证服务器   http://10.2.5.251/        —— 返回 GB2312 编码的配置页
  · 该页内嵌登录接口定义：
        authloginpath = /eportal/?c=ACSetting&a=Login
        authloginport = 801          ← 注意是 801，不是 80
        authuserfield = DDDDD
        authpassfield = upass
        authsuccess   = Dr.COMWebLoginID_3.htm
  · 80 端口 /eportal/ 返回 404，所以端口必须从配置页里读，不能写死
  · 服务类型后缀：@xyw 校园用户 / @dx 校园电信 / @lt 校园联通 / 空 校园其他

设计原则：
  1. 零第三方依赖（只用标准库）→ 开机自启不需要虚拟环境
  2. 不猜成功与否 —— 每次登录后重新探测真实连通性，探到才算成功
  3. 参数从认证页现取（IP / MAC / 端口每次会话都可能变），不硬编码
  4. 全失败时可用 --capture 录制你自己的请求兜底

用法：
  python cumt_net.py --status     # 只看状态和解析到的接口参数，不登录
  python cumt_net.py             # 检查一次，掉线则登录
  python cumt_net.py --daemon    # 常驻守护，掉线自动重登
  python cumt_net.py --dry-run   # 只看打算怎么做，绝不发登录请求
  python cumt_net.py --capture "<F12 里那条登录请求的完整 URL>"

仅供本人已授权账号的本地登录自动化使用。
"""

import datetime
import http.cookiejar
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONF_PATH = os.path.join(BASE, "config.json")
LOG_DIR = os.path.join(BASE, "logs")
LOG_PATH = os.path.join(LOG_DIR, "login.log")
LIBS_DIR = os.path.join(BASE, "libs")
BROWSER_PY = os.path.join(BASE, "browser_login.py")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# 服务类型后缀（矿大认证页 carrier 字段实测值）
SUFFIX_MAP = {
    "校园用户": ["@xyw"],
    "校园电信": ["@dx"],
    "校园联通": ["@lt"],
    "校园移动": ["@cmcc", ""],   # 部分账号走无后缀，两种都试
    "校园其他": [""],
}

# 命中这些标记说明返回的是本校认证页，不是真正的外网
PORTAL_MARKS = ("eportal", "a79.htm", "drcom", "wlanuserip", "wlanacname")

DEFAULT_CONF = {
    "account": "",
    "password": "",
    "provider": "校园移动",
    "portal_host": "10.2.5.251",
    "login_port": 0,          # 0 = 自动从认证页读取（实测为 801）
    "probe_urls": [
        "http://connect.rom.miui.com/generate_204",
        "http://wifi.vivo.com.cn/generate_204",
        "http://captive.apple.com/hotspot-detect.html",
    ],
    "interval": 20,
    "custom_login_url": "",
    "custom_login_method": "GET",
    "custom_login_body": "",
}


# ------------------------------------------------------------ 基础工具

def log(msg, echo=True):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    if echo and sys.stdout is not None:
        try:
            print(line)
        except Exception:
            pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            with open(LOG_PATH, encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-800:]
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_conf():
    if not os.path.exists(CONF_PATH):
        with open(CONF_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONF, f, ensure_ascii=False, indent=2)
        print("已生成配置模板：%s\n请填写 account / password 后重跑。" % CONF_PATH)
        sys.exit(2)
    with open(CONF_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    for k, v in DEFAULT_CONF.items():
        cfg.setdefault(k, v)
    return cfg


def save_conf(cfg):
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


_cookies = http.cookiejar.CookieJar()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# ProxyHandler({}) 强制直连：否则 Windows 系统代理（Clash 等）会掩盖真实认证状态
_op_raw = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(_cookies), _NoRedirect())
_op_follow = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(_cookies))


def http(url, method="GET", fields=None, follow=False, referer="", timeout=6):
    """返回 (状态码, 响应头(大小写不敏感), 响应体前 8KB, 最终URL)。-1 表示网络层失败。"""
    data = None
    if fields is not None:
        data = urllib.parse.urlencode(fields, encoding="gb2312",
                                      errors="replace").encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    if referer:
        req.add_header("Referer", referer)
    if data is not None:
        req.add_header("Content-Type",
                       "application/x-www-form-urlencoded; charset=gb2312")
    opener = _op_follow if follow else _op_raw
    try:
        r = opener.open(req, timeout=timeout)
        return r.status, r.headers, r.read(8192), r.geturl()
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(8192)
        except Exception:
            pass
        return e.code, e.headers, body, url
    except Exception as e:
        return -1, {}, str(e).encode(), url


def _is_portal(cfg, url="", body=b""):
    host = cfg.get("portal_host", "")
    low_url = (url or "").lower()
    if host and host in low_url:
        return True
    if any(m in low_url for m in PORTAL_MARKS):
        return True
    low_body = (body or b"").lower()
    return any(m.encode() in low_body for m in PORTAL_MARKS)


def probe(cfg):
    """三态探测，避免把「网络不可达」误判成「需要认证」而反复发登录请求。

    "online"  —— 已通过认证，能上外网
    "login"   —— 被劫持到本校认证页，需要登录
    "unknown" —— 全部探测地址都不通（不在校园网内 / DNS 故障 / 断网）
    """
    responded = False
    for u in cfg["probe_urls"]:
        code, headers, body, final = http(u, follow=False)
        if code == -1:
            continue
        responded = True
        if code in (301, 302, 303, 307, 308):
            loc = headers.get("Location") or ""
            if _is_portal(cfg, loc):
                return "login", urllib.parse.urljoin(u, loc)
            continue          # 站点自身跳转，不算劫持
        if code == 204:
            return "online", ""
        if code == 200:
            if _is_portal(cfg, final, body):
                return "login", final
            if b"success" in body.lower():
                return "online", ""
    return ("online" if responded else "unknown"), ""


def read_online_uid(cfg):
    """读认证页里内嵌的 uid —— 也就是「当前在线的账号」。

    这一条是必要的：外网通不等于「是我这个账号登的」。
    别人（或另一个账号）登录也会让外网通，光看联网状态会误判成功。
    """
    code, _, body, _ = http("http://%s/" % cfg["portal_host"], timeout=6)
    if code == -1 or not body:
        return ""
    text = body.decode("gb2312", "ignore")
    m = re.search(r"\buid\s*=\s*'([^']*)'", text)
    return m.group(1).strip() if m else ""


def mask_uid(uid):
    if not uid:
        return "(空)"
    name, _, suffix = uid.partition("@")
    head = name[:6] if len(name) > 6 else name
    return head + "***" + ("@" + suffix if suffix else "")


def uid_is_mine(cfg, uid):
    """在线 uid 是否属于本配置要登录的账号。"""
    if not uid:
        return None              # 读不到，无法判定
    name = uid.split("@")[0].strip().upper()
    mine = (cfg.get("account") or "").strip().upper()
    if not mine:
        return None
    return name == mine or name.startswith(mine) or mine.startswith(name)


def portal_reachable(cfg):
    """认证服务器是否可达。用 TCP 探测而不是 HTTP GET ——
    未认证时 AC 可能吞掉 HTTP 请求，但端口本身是通的。"""
    try:
        s = socket.create_connection((cfg["portal_host"], 80), timeout=3)
        s.close()
        return True
    except Exception:
        return False


# ------------------------------------------------------------ 从认证页读接口定义

CONF_KEYS = ("authloginpath", "authloginport", "authuserfield", "authpassfield",
             "authloginIP", "authsuccess", "authfail", "charset",
             "v4ip", "olmac", "v46m", "zxopt")


def parse_portal_config(text):
    """认证页里把登录接口信息以 JS 变量形式内嵌了，直接读，比逆向 JS 可靠。"""
    out = {}
    for k in CONF_KEYS:
        m = re.search(r"\b" + k + r"\s*=\s*'([^']*)'", text)
        if not m:
            m = re.search(r"\b" + k + r"\s*=\s*([^;\n/]+)", text)
        if m:
            out[k] = m.group(1).strip().strip("'\"")
    return out


def fetch_ctx(cfg, portal_url=""):
    """汇总本次会话的登录上下文：动态参数 + 认证页里的接口定义。"""
    conf = {}
    _, _, body, _ = http("http://%s/" % cfg["portal_host"], timeout=6)
    if body:
        conf = parse_portal_config(body.decode("gb2312", "ignore"))

    params, raw_query = {}, ""
    if portal_url:
        raw_query = urllib.parse.urlparse(portal_url).query
        params = {k: v[0] for k, v in urllib.parse.parse_qs(
            raw_query, keep_blank_values=True).items()}
    if not params:
        for u in cfg["probe_urls"]:
            _, headers, _, _ = http(u, follow=False)
            loc = headers.get("Location") or ""
            if _is_portal(cfg, loc):
                portal_url = urllib.parse.urljoin(u, loc)
                raw_query = urllib.parse.urlparse(portal_url).query
                params = {k: v[0] for k, v in urllib.parse.parse_qs(
                    raw_query, keep_blank_values=True).items()}
                break

    # 兜底：参数没拿到就用认证页里记录的客户端 IP / MAC
    if not params.get("wlanuserip") and conf.get("v4ip"):
        params["wlanuserip"] = conf["v4ip"]
    if not params.get("mac") and conf.get("olmac"):
        params["mac"] = conf["olmac"]
    if not params.get("wlanacip") and conf.get("authloginIP"):
        params["wlanacip"] = conf["authloginIP"]

    return {"params": params, "query": raw_query, "conf": conf,
            "portal_url": portal_url}


def login_port(cfg, conf):
    if cfg.get("login_port"):
        return int(cfg["login_port"])
    try:
        return int(conf.get("authloginport") or 801)
    except ValueError:
        return 801


# ------------------------------------------------------------ 登录实现

def _build_login_query(cfg, conf, params):
    acip = params.get("wlanacip") or params.get("nasip", "")
    uip = params.get("wlanuserip") or params.get("ip", "")
    return {
        "c": "ACSetting", "a": "Login", "protocol": "http:",
        "hostname": cfg["portal_host"], "iTermType": "1",
        "wlanuserip": uip, "wlanacip": acip,
        "wlanacname": params.get("wlanacname", "BRAS"),
        "mac": params.get("mac", ""), "ip": uip,
        "enAdvert": "0", "queryACIP": "0", "loginMethod": "1",
    }


def _build_login_body(acct, pwd):
    return {"DDDDD": acct, "upass": pwd, "R1": "0", "R2": "", "R3": "0",
            "R6": "0", "para": "00", "0MKKey": "123456"}


def _login_url(cfg, conf, extra, port):
    """拼登录 URL。注意 authloginpath 本身可能已带查询串（如 /eportal/?c=ACSetting&a=Login）。"""
    path = conf.get("authloginpath") or "/eportal/?c=ACSetting&a=Login"
    sep = "&" if "?" in path else "?"
    return "http://%s:%d%s%s%s" % (
        cfg["portal_host"], port, path, sep, urllib.parse.urlencode(extra))


def login_acsetting_post(cfg, acct, pwd, ctx, port):
    """ACSetting 标准口径：参数在 query，账号密码在 POST body。"""
    conf, params = ctx["conf"], ctx["params"]
    url = _login_url(cfg, conf, _build_login_query(cfg, conf, params), port)
    code, _, resp, _ = http(url, method="POST",
                            fields=_build_login_body(acct, pwd),
                            referer="http://%s/" % cfg["portal_host"])
    return code, resp


def login_acsetting_get(cfg, acct, pwd, ctx, port):
    """同上，但账号密码也放 query（部分 AC 只认 GET）。"""
    conf, params = ctx["conf"], ctx["params"]
    q = _build_login_query(cfg, conf, params)
    q.update(_build_login_body(acct, pwd))
    url = _login_url(cfg, conf, q, port)
    code, _, resp, _ = http(url, referer="http://%s/" % cfg["portal_host"])
    return code, resp


def login_drcom_classic(cfg, acct, pwd, ctx, port):
    """备选：老式 /drcom/login。"""
    q = {"callback": "dr1003", "DDDDD": acct, "upass": pwd,
         "0MKKey": "123456", "R1": "0", "R2": "", "R3": "0", "R6": "0",
         "para": "00", "v6ip": "", "v": str(int(time.time() * 1000))}
    url = "http://%s:%d/drcom/login?%s" % (cfg["portal_host"], port,
                                           urllib.parse.urlencode(q))
    code, _, resp, _ = http(url, referer="http://%s/" % cfg["portal_host"])
    return code, resp


def login_custom(cfg, acct, pwd, ctx, port):
    """用 --capture 录下的模板原样重放。"""
    tpl = (cfg.get("custom_login_url") or "").strip()
    if not tpl:
        return 0, b""
    safeparams = {k: (v if isinstance(v, str) else "")
                  for k, v in ctx["params"].items()}
    vars_ = dict(safeparams)
    vars_.update({"account": acct, "password": pwd,
                  "host": cfg["portal_host"], "query": ctx["query"],
                  "port": str(port)})
    url = tpl.format(**vars_)
    method = (cfg.get("custom_login_method") or "GET").upper()
    if method == "POST":
        body_tpl = (cfg.get("custom_login_body") or "").format(**vars_)
        fields = {k: v[0] for k, v in urllib.parse.parse_qs(
            body_tpl, keep_blank_values=True).items()}
        code, _, resp, _ = http(url, method="POST", fields=fields)
    else:
        code, _, resp, _ = http(url)
    return code, resp


VARIANTS = [
    ("ACSetting-POST", login_acsetting_post),
    ("ACSetting-GET", login_acsetting_get),
    ("DrCOM-classic", login_drcom_classic),
]


# ------------------------------------------------------------ 主流程

def do_login(cfg):
    state, portal_url = probe(cfg)
    if state == "online":
        return True, "已在线，无需登录"
    if state == "unknown":
        if not portal_reachable(cfg):
            return False, ("探测不到网络且认证服务器 %s 不可达 —— "
                           "可能没连校园网" % cfg["portal_host"])
        log("外网探测失败但认证服务器可达，判定为被劫持，继续尝试登录")

    ctx = fetch_ctx(cfg, portal_url)
    conf, params = ctx["conf"], ctx["params"]
    log("认证页接口：path=%s port=%s 字段=%s/%s" % (
        conf.get("authloginpath", "?"), conf.get("authloginport", "?"),
        conf.get("authuserfield", "?"), conf.get("authpassfield", "?")))
    log("动态参数：ip=%s mac=%s acip=%s acname=%s" % (
        params.get("wlanuserip", "?"), params.get("mac", "?"),
        params.get("wlanacip", "?"), params.get("wlanacname", "?")))

    suffixes = SUFFIX_MAP.get(cfg["provider"], [""])
    plan = VARIANTS
    if cfg.get("custom_login_url"):
        plan = [("custom(优先)", login_custom)] + VARIANTS

    # 端口优先用认证页里声明的那个；再退一步试 80（不同 AC 部署不一样）
    ports = [login_port(cfg, conf)]
    if 80 not in ports:
        ports.append(80)

    last = ""
    for port in ports:
        for name, fn in plan:
            for suffix in suffixes:
                acct = cfg["account"] + suffix
                try:
                    code, resp = fn(cfg, acct, cfg["password"], ctx, port)
                except Exception as e:
                    last = "%s 异常：%r" % (name, e)
                    log(last)
                    continue
                snippet = resp.decode("gb2312", "ignore")[:200].replace("\n", " ")
                log("尝试 %s @%d端口（后缀 %s）→ HTTP %s  %s"
                    % (name, port, suffix or "无", code, snippet))
                time.sleep(1.5)
                if probe(cfg)[0] == "online":
                    # 核实在线的是不是自己的号，避免把别人登录误判成自己成功
                    uid = read_online_uid(cfg)
                    if uid_is_mine(cfg, uid) is False:
                        return False, ("外网已通，但在线账号是另一个号（%s），"
                                       "不是配置里的账号" % mask_uid(uid))
                    return True, "%s（端口 %d，后缀 %s）登录成功，在线账号 %s" % (
                        name, port, suffix or "无", mask_uid(uid))
                # 安全阀：凭据类错误立刻停手，避免反复失败触发账号锁定
                if any(w in snippet for w in ("密码错", "密码不正确", "账号不",
                                              "口令错", "password error",
                                              "用户不存在")):
                    return False, ("服务端明确拒绝，已停止重试以免账号被锁：%s"
                                   % snippet[:140])
                if any(w in snippet for w in ("终端", "上限", "顶号")):
                    return False, ("终端数已达上限 / 账号在别处在线：%s "
                                   "（先关掉手机或其他设备再试）" % snippet[:120])
                if snippet:
                    last = snippet[:160]
    return False, "所有登录口径均未生效，最后一次返回：%s" % (last or "无响应")


def clean_text(resp):
    """响应体可能是 UTF-8 也可能是 GB2312，先试 UTF-8 避免乱码。"""
    for enc in ("utf-8", "gb18030"):
        try:
            txt = resp.decode(enc)
            break
        except Exception:
            continue
    else:
        txt = resp.decode("gb18030", "ignore")
    txt = re.sub(r"<script[\s\S]*?</script>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def do_logout(cfg):
    """注销当前会话，用来观察「未认证」状态下 AC 到底给出什么认证页。"""
    ctx = fetch_ctx(cfg, "")
    conf = ctx["conf"]
    path = conf.get("authlogoutpath") or "/eportal/?c=ACSetting&a=Logout&ver=1.0"
    for port in (80, 801):
        url = "http://%s:%d%s" % (cfg["portal_host"], port, path)
        code, _, body, final = http(url, follow=True)
        log("注销 %s → HTTP %s | %s" % (url[:90], code,
                                     clean_text(body)[:100] or "(空)"))
    time.sleep(2)
    log("注销后外网状态 = %s" % probe(cfg)[0])
    return 0


def do_inspect(cfg):
    """抓现场：未认证时认证页的真实表单结构与接口定义。"""
    state, portal_url = probe(cfg)
    log("外网状态 = %s" % state)
    if portal_url:
        log("AC 跳转地址 = %s" % portal_url[:220])
    ctx = fetch_ctx(cfg, portal_url)
    log("认证页配置 = %s" % {k: v for k, v in ctx["conf"].items() if v})
    log("动态参数 = %s" % ctx["params"])
    targets = ["http://%s/" % cfg["portal_host"]]
    if portal_url:
        targets.insert(0, portal_url)
    for u in targets:
        code, headers, body, final = http(u, follow=True, timeout=8)
        t = body.decode("gb18030", "ignore")
        forms = re.findall(r"<form[^>]*>", t, re.I)
        inputs = re.findall(r"<input[^>]*name=[\"']?([\w.\[\]-]+)", t, re.I)
        scripts = re.findall(r"<script[^>]*src=[\"']([^\"']+)", t, re.I)
        log("==")
        log("请求 %s → HTTP %s，落地 %s" % (u[:110], code, final[:110]))
        log("  form : %s" % forms[:2])
        log("  input: %s" % inputs[:10])
        log("  js   : %s" % scripts[:6])
    return 0


def try_once(cfg, suffix="", port=None):
    """诊断用：只向认证服务器发一次登录请求，打印服务端原始返回。

    刻意只发一次 —— 密码或后缀猜错时反复重试可能触发账号锁定/限速。
    """
    state, portal_url = probe(cfg)
    log("诊断：当前外网状态 = %s" % state)
    ctx = fetch_ctx(cfg, portal_url)
    conf = ctx["conf"]
    p = port or login_port(cfg, conf)
    acct_len = len(cfg["account"]) + len(suffix)
    log("单次尝试 ACSetting-POST @端口%d，后缀=%s，账号长度=%d"
        % (p, suffix or "无", acct_len))
    url = _login_url(cfg, conf, _build_login_query(cfg, conf, ctx["params"]), p)
    code, headers, body, _ = http(
        url, method="POST",
        fields=_build_login_body(cfg["account"] + suffix, cfg["password"]),
        referer="http://%s/" % cfg["portal_host"])
    loc = headers.get("Location") or ""
    log("HTTP %s | Location: %s" % (code, loc or "(无)"))
    if body:
        log("响应体：%s" % (clean_text(body)[:300] or "(空)"))
    if loc:
        nxt = urllib.parse.urljoin(url, loc)
        c2, _, b2, f2 = http(nxt, follow=True)
        log("跟随跳转 → %s | HTTP %s" % (f2[:110], c2))
        log("落地页内容：%s" % (clean_text(b2)[:300] or "(空)"))
    time.sleep(2)
    st2, _ = probe(cfg)
    log("尝试后外网状态 = %s" % st2)
    return 0


def browser_fallback():
    """用 Playwright 驱动本机 Chrome 填表登录。

    返回退出码：
      0  = 登录成功（已核对在线账号就是本人）
      2  = 网络本来通着、但在线的是另一个账号（按「不顶号」策略不干预）
      其他 = 失败
    """
    if not (os.path.isdir(LIBS_DIR) and os.path.exists(BROWSER_PY)):
        log("未找到浏览器登录组件（libs 目录或缺 browser_login.py）")
        return 1
    env = dict(os.environ)
    env["PYTHONPATH"] = LIBS_DIR + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run([sys.executable, BROWSER_PY], cwd=BASE, env=env,
                           timeout=180, capture_output=True)
    except Exception as e:
        log("启动浏览器登录失败：%r" % (e,))
        return 1
    # 子进程输出是 GBK（中文 Windows 控制台默认），按 UTF-8 解会乱码
    raw = r.stdout or b""
    try:
        text = raw.decode("gbk")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    for line in text.strip().splitlines()[-5:]:
        log("  [浏览器] " + line)
    return r.returncode


def login_auto(cfg):
    """优先浏览器登录：不依赖接口细节，页面改版也不受影响。
    浏览器失败才退回 HTTP 口径（避免反复撞同一堵墙触发风控）。"""
    if not portal_reachable(cfg):
        return False, "认证服务器不可达，本轮跳过（不在校园网 / WiFi 还没就绪）"
    rc = browser_fallback()
    if rc == 0:
        return True, "浏览器登录成功（已核对是本人账号在线）"
    if rc == 2:
        return True, "网络已通，但在线的是另一个账号 —— 按策略未做干预"
    log("浏览器登录未成功，回退到 HTTP 口径")
    return do_login(cfg)


def watch(cfg, seconds=600):
    """蹲守模式：等用户注销/掉线，第一时间抓现场并尝试登录。

    存在的意义：登录表单只在「未认证」状态下才由认证页渲染，
    已登录时无论怎么请求都拿不到它。
    """
    log("蹲守模式启动：最多等 %d 分钟，一旦检测到未认证就抓现场并尝试登录"
        % (seconds // 60))
    t0 = time.time()
    last = None
    while time.time() - t0 < seconds:
        state, url = probe(cfg)
        if state != "online":
            log("检测到非在线状态（%s），开始抓现场" % state)
            try:
                do_inspect(cfg)
            except Exception as e:
                log("抓现场失败：%r" % (e,))
            ok, msg = login_auto(cfg)
            log(("登录成功：" if ok else "登录失败：") + msg)
            return 0 if ok else 1
        if last != state:
            log("当前仍在线，继续等待…")
            last = state
        time.sleep(3)
    log("等待超时，未检测到未认证状态")
    return 1


def daemon(cfg):
    log("守护模式启动：每 %s 秒检测一次（连续失败 3 次后降到 5 分钟）"
        % cfg["interval"])
    fails = 0
    last_state = None
    while True:
        try:
            state, _ = probe(cfg)
            if state == "online":
                if last_state is not None and last_state != "online":
                    log("已恢复在线")
                fails = 0
            elif state == "unknown":
                # 关键补洞：开机时 WiFi 已连上、但 AC 把 DNS 掐了，外网探测会全部失败，
                # 这时状态是 unknown 而不是 login。若认证服务器 TCP 可达，说明就在
                # 校园网里、只是没认证，必须去登录，否则会一直跳过、永远登不上。
                if portal_reachable(cfg):
                    if last_state != "unknown":
                        log("外网探测不通但认证服务器可达 → 判定为需要登录")
                    ok, msg = login_auto(cfg)
                    log(("登录成功：" if ok else "登录失败：") + msg)
                    fails = 0 if ok else fails + 1
                elif last_state != "unknown":
                    log("外网和认证服务器都不可达，跳过（未连校园网 / WiFi 未就绪）")
            else:
                ok, msg = login_auto(cfg)
                log(("登录成功：" if ok else "登录失败：") + msg)
                fails = 0 if ok else fails + 1
            last_state = state
        except KeyboardInterrupt:
            log("收到中断，退出")
            return
        except Exception as e:
            log("循环异常：%r" % (e,))
            fails += 1
        try:
            time.sleep(cfg["interval"] if fails < 3 else 300)
        except KeyboardInterrupt:
            return


def capture(cfg, url):
    acc, pwd = cfg.get("account", ""), cfg.get("password", "")
    tpl = url
    for raw, ph in ((acc, "{account}"), (pwd, "{password}")):
        if raw:
            tpl = tpl.replace(urllib.parse.quote(raw, safe=""), ph).replace(raw, ph)
    cfg["custom_login_url"] = tpl
    cfg["custom_login_method"] = "POST" if cfg.get("custom_login_body") else "GET"
    save_conf(cfg)
    print("已保存到 config.json 的 custom_login_url：")
    print("  " + tpl)
    if "{account}" not in tpl:
        print("\n没在 URL 里认出你的账号，请手动把账号那段改成 {account}、")
        print("密码那段改成 {password}，脚本下次会优先使用这个模板。")


def main(argv):
    for s in (sys.stdout, sys.stderr):
        try:
            if s is not None:
                s.reconfigure(errors="replace")
        except Exception:
            pass

    cfg = load_conf()
    args = argv[1:]
    need_acct = not ("--status" in args or "--dry-run" in args)
    if need_acct and (not cfg.get("account") or not cfg.get("password")):
        print("请先在 %s 里填写 account 和 password。" % CONF_PATH)
        return 2

    if "--capture" in args:
        i = args.index("--capture")
        if i + 1 >= len(args):
            print('用法：--capture "http://10.2.5.251:801/eportal/?...完整登录URL"')
            return 2
        capture(cfg, args[i + 1])
        return 0

    if "--status" in args:
        state, loc = probe(cfg)
        print("外网状态：%s" % {"online": "在线（已通过认证）",
                            "login": "离线（被劫持到认证页）",
                            "unknown": "外网探测不通"}[state])
        print("认证服务器 %s：%s" % (
            cfg["portal_host"],
            "可达" if portal_reachable(cfg) else "不可达（不在校园网内？）"))
        ctx = fetch_ctx(cfg, loc)
        conf = ctx["conf"]
        if conf:
            print("登录接口：http://%s:%d%s"
                  % (cfg["portal_host"], login_port(cfg, conf),
                     conf.get("authloginpath") or "/eportal/?c=ACSetting&a=Login"))
            print("字段名：%s / %s" % (conf.get("authuserfield", "?"),
                                   conf.get("authpassfield", "?")))
        print("动态参数：%s" % {k: v for k, v in ctx["params"].items()
                            if k in ("wlanuserip", "mac", "wlanacip",
                                     "wlanacname")})
        if loc:
            print("门户跳转：%s" % loc[:150])
        return 0

    if "--dry-run" in args:
        state, _ = probe(cfg)
        print("探测结果：%s" % {"online": "在线", "login": "离线需认证",
                            "unknown": "不可达"}[state])
        print("服务类型：%s，后缀候选：%s"
              % (cfg["provider"], SUFFIX_MAP.get(cfg["provider"], [""])))
        order = [n for n, _ in VARIANTS]
        if cfg.get("custom_login_url"):
            order = ["custom(优先)"] + order
        print("登录口径尝试顺序：%s" % order)
        print("（dry-run 不发送任何登录请求）")
        return 0

    if "--watch" in args:
        i = args.index("--watch")
        secs = 600
        if i + 1 < len(args) and args[i + 1].isdigit():
            secs = int(args[i + 1])
        return watch(cfg, secs)

    if "--logout" in args:
        return do_logout(cfg)

    if "--inspect" in args:
        return do_inspect(cfg)

    if "--try" in args:
        i = args.index("--try")
        suffix = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("-") else ""
        return try_once(cfg, suffix)

    if "--daemon" in args:
        daemon(cfg)
        return 0

    ok, msg = login_auto(cfg)
    log(("登录成功：" if ok else "登录失败：") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
