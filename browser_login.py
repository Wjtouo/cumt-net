#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
browser_login.py —— 用真实 Chrome 打开校园网认证页并填表登录

为什么要有这个脚本：
  矿大的认证页（a79.htm）里的登录表单是由压缩过的 a41.js 动态渲染的，
  已登录状态下无论怎么请求都拿不到表单结构，纯 HTTP 逆推走不通。
  让真浏览器执行页面自己的 JS，就不需要关心接口细节，页面改版也不怕。

额外收益：
  会把页面真正发出的登录请求录到 logs/captured_request.txt，
  将来可以据此把纯 HTTP 快路径写准，不再需要浏览器。

用法：
  .venv\\Scripts\\python.exe browser_login.py          # 登录一次
  .venv\\Scripts\\python.exe browser_login.py --show   # 显示窗口（排错用）
"""

import importlib.util
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

_spec = importlib.util.spec_from_file_location("cn", os.path.join(BASE, "cumt_net.py"))
cn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cn)

CAPTURE_PATH = os.path.join(BASE, "logs", "captured_request.txt")
SHOT_PATH = os.path.join(BASE, "logs", "login_page.png")

USER_SEL = ("input[name='user'], input#user, input[name='username'], "
            "input[name='DDDDD'], input[name='account'], input[name='userId']")
PASS_SEL = "input[type='password']"
CODE_SEL = ("input[name='code'], input[name='captcha'], input[name='verify'], "
            "input[name='validcode']")
BTN_TEXTS = ("登录", "登陆", "Login", "LOGIN", "登录上网", "连接", "确定")

# 服务类型下拉框里各运营商可能的叫法，按 config.json 的 provider 选关键词
PROVIDER_KEYWORDS = {
    "校园联通": ["中国联通", "校园联通", "联通"],
    "校园移动": ["中国移动", "校园移动", "移动"],
    "校园电信": ["中国电信", "校园电信", "电信"],
    "校园用户": ["校园用户", "校园网"],
    "校园其他": ["校园其他", "其他"],
}


def find_login_frame(page):
    """返回包含密码输入框的那个 frame（页面可能用 iframe 组织）。"""
    for fr in page.frames:
        try:
            if fr.query_selector(PASS_SEL):
                return fr
        except Exception:
            continue
    return None


def fill_and_submit(fr, cfg, log):
    user_el = None
    for sel in USER_SEL.split(", "):
        el = fr.query_selector(sel)
        if el and el.is_visible():
            user_el = el
            break
    if user_el is None:
        for el in fr.query_selector_all("input"):
            t = (el.get_attribute("type") or "text").lower()
            if t in ("text", "") and el.is_visible():
                user_el = el
                break
    if user_el is None:
        return False, "找不到用户名输入框"

    pass_el = fr.query_selector(PASS_SEL)
    if pass_el is None:
        return False, "找不到密码输入框"

    user_el.fill(cfg["account"])
    pass_el.fill(cfg["password"])
    log("已填写账号/密码")

    code_el = fr.query_selector(CODE_SEL)
    if code_el and code_el.is_visible():
        return False, "页面要求验证码，无法自动填写（请人工登录一次）"

    # 服务类型：按 config.json 里的 provider 匹配（下拉框优先，其次单选框/标签）
    kws = PROVIDER_KEYWORDS.get(cfg.get("provider", ""), [])
    if not kws:
        log("未配置服务类型关键字（provider=%s），跳过" % cfg.get("provider"))
    else:
        picked = ""
        try:
            for sel in fr.query_selector_all("select"):
                if picked:
                    break
                for opt in sel.query_selector_all("option"):
                    txt = (opt.inner_text() or "").strip()
                    if any(k in txt for k in kws):
                        sel.select_option(value=opt.get_attribute("value"))
                        picked = txt
                        log("服务类型选择：%s" % txt)
                        break
            if not picked:
                for lab in fr.query_selector_all("label"):
                    txt = (lab.inner_text() or "").strip()
                    if len(txt) <= 8 and any(k in txt or txt in k for k in kws):
                        lab.click()
                        picked = txt
                        log("服务类型点击：%s" % txt)
                        break
            if not picked:
                log("没找到匹配「%s」的服务类型选项，按页面默认值提交" % "/".join(kws))
        except Exception as e:
            log("服务类型选择跳过（%r）" % (e,))

    # 只认真实的可点击控件。绝不能把 div 当候选：
    # 包着按钮的外层 div 的 innerText 也是「登 录」，点了它不会触发按钮的回调。
    def _norm(s):
        return re.sub(r"\s+", "", s or "")

    cands = fr.query_selector_all(
        "button, input[type='submit'], input[type='button'], a")
    target = None
    for el in cands:
        try:
            if not el.is_visible():
                continue
            label = _norm(el.inner_text()) or _norm(el.get_attribute("value"))
            if label and label in BTN_TEXTS:
                target = (el, label)
                break
        except Exception:
            continue
    if target is None:                      # 退化匹配：只认按钮/输入，且标签很短
        for el in cands:
            try:
                tag = (el.evaluate("e => e.tagName") or "").upper()
                if tag not in ("BUTTON", "INPUT") or not el.is_visible():
                    continue
                label = _norm(el.inner_text()) or _norm(el.get_attribute("value"))
                if label and len(label) <= 4 and (
                        "登录" in label or "登陆" in label
                        or "login" in label.lower()):
                    target = (el, label)
                    break
            except Exception:
                continue

    if target is not None:
        el, label = target
        try:
            el.click()
            log("已点击登录按钮：%s" % label)
        except Exception as e:
            log("点击失败（%r），改用回车提交" % (e,))
            try:
                pass_el.press("Enter")
            except Exception:
                return False, "找不到登录按钮"
    else:
        try:
            pass_el.press("Enter")
            log("未找到登录按钮，改用回车提交")
        except Exception:
            return False, "找不到登录按钮"
    return True, "已提交"


def wait_network_ready(cfg, timeout=120):
    """注销/重连后 WiFi 需要重新拿地址，这期间连认证服务器都不通。
    先等 TCP 到认证服务器可通，再谈登录，否则浏览器只会报 DNS/超时。"""
    import socket
    host = cfg["portal_host"]
    t0 = time.time()
    announced = False
    while time.time() - t0 < timeout:
        try:
            s = socket.create_connection((host, 80), timeout=3)
            s.close()
            if announced:
                cn.log("网络已就绪（等了 %.0f 秒）" % (time.time() - t0))
            return True
        except Exception:
            if not announced:
                cn.log("网络尚未就绪，等 WiFi 重新获取地址…（最多 %d 秒）" % timeout)
                announced = True
        time.sleep(2)
    cn.log("等了 %d 秒网络仍未就绪" % timeout)
    return False


def relogin_flow(page, cfg):
    """一键验证：若已登录先注销 → 等登录表单出现 → 填表提交 → 验证联网。"""
    host = cfg["portal_host"]
    base = "http://%s/" % host
    try:
        page.goto(base, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        cn.log("打开认证页失败：%r" % (e,))
        return False
    page.wait_for_timeout(2200)

    try:
        body = page.inner_text("body") or ""
    except Exception:
        body = ""
    logged_in = ("已经成功登录" in body) or bool(
        page.query_selector("input[name='logout']"))

    if logged_in:
        cn.log("当前是已登录状态，先主动注销（会短暂断网，属正常）")
        try:
            page.on("dialog", lambda d: d.accept())   # 兜底：自动确认原生弹窗
        except Exception:
            pass
        try:
            how = page.evaluate("""() => {
                try { if (typeof exit === 'function') { exit(); return 'exit()'; } } catch (e) {}
                try { if (typeof wc === 'function') { wc(); return 'wc()'; } } catch (e) {}
                return 'none';
            }""")
            cn.log("注销调用：%s" % how)
        except Exception as e:
            cn.log("注销调用异常：%r" % (e,))
        page.wait_for_timeout(1500)

        # 页面用的是自定义确认框（Boxy.confirm），需要再点一次「确定」
        for _ in range(3):
            hit = page.evaluate("""() => {
                const nodes = [...document.querySelectorAll('button,a,span,div')];
                for (const n of nodes) {
                    const t = (n.innerText || '').trim();
                    if ((t === '确定' || t === '确认' || t === 'OK') && n.offsetParent) {
                        n.click(); return t;
                    }
                }
                return '';
            }""")
            if hit:
                cn.log("已点击确认框：%s" % hit)
                break
            page.wait_for_timeout(800)
        page.wait_for_timeout(3000)
    else:
        cn.log("当前已是未登录状态，直接登录")

    fr = None
    for i in range(6):
        fr = find_login_frame(page)
        if fr:
            break
        cn.log("等待登录表单出现（第 %d 次重试）" % (i + 1))
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            cn.log("重新打开失败：%r" % (e,))
        page.wait_for_timeout(2200)

    if fr is None:
        try:
            page.screenshot(path=SHOT_PATH)
        except Exception:
            pass
        cn.log("仍未出现登录表单；截图存于 %s" % SHOT_PATH)
        return False

    try:
        page.screenshot(path=SHOT_PATH)
        cn.log("登录页截图已存 %s" % SHOT_PATH)
    except Exception:
        pass

    good, msg = fill_and_submit(fr, cfg, cn.log)
    cn.log("填表结果：%s" % msg)
    if not good:
        return False

    for _ in range(4):
        page.wait_for_timeout(2000)
        if cn.probe(cfg)[0] == "online":
            return True
    return False


def main(argv):
    show = "--show" in argv
    relogin_mode = "--relogin" in argv
    cfg = cn.load_conf()

    if "--watch" in argv:
        secs = 480
        for a in argv:
            if a.isdigit():
                secs = int(a)
        cn.log("浏览器蹲守启动：最多等 %d 分钟，一检测到未认证就用 Chrome 登录"
               % (secs // 60))
        t0 = time.time()
        while time.time() - t0 < secs:
            if cn.probe(cfg)[0] != "online":
                break
            time.sleep(3)
        else:
            cn.log("等待超时，未检测到未认证状态")
            return 1
        cn.log("检测到未认证状态，等网络就绪…")
        if not wait_network_ready(cfg):
            return 1
        cn.log("网络就绪，开始用浏览器登录")

    portal = ""
    if relogin_mode:
        cn.log("=== 一键验证：注销 → 等登录页 → 自动填表 → 验证联网 ===")
    else:
        state, portal = cn.probe(cfg)
        if state == "online":
            uid = cn.read_online_uid(cfg)
            if cn.uid_is_mine(cfg, uid) is False:
                cn.log("网络是通的，但在线的是另一个账号（%s），"
                       "不是配置里的 %s" % (cn.mask_uid(uid),
                                        cn.mask_uid(cfg["account"])))
                return 2          # 2 = 别人在线，按「不顶号」策略不干预
            cn.log("已在线（账号 %s），无需登录" % cn.mask_uid(uid))
            return 0
        cn.log("当前状态 %s，启动浏览器登录" % state)
        # 关键：先等网络就绪，否则浏览器会一路报 DNS 失败/超时
        if not wait_network_ready(cfg):
            return 1
        state, portal = cn.probe(cfg)
        if state == "online":
            cn.log("网络已恢复且已在线，无需登录")
            return 0
        cn.log("网络就绪，当前状态 %s" % state)

    targets = []
    if portal:
        targets.append(portal)
    # 先打一个外网探测地址，让 AC 重新跳一次 —— 这样落地页会带上完整的
    # wlanuserip / wlanacname / mac 参数，登录表单才渲染得出来
    targets += ["http://connect.rom.miui.com/generate_204",
                "http://%s/" % cfg["portal_host"],
                "http://%s/a79.htm" % cfg["portal_host"],
                "http://9.9.9.9/"]

    captured = []

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=not show,
            args=["--no-proxy-server", "--ignore-certificate-errors"])
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        def on_request(req):
            u = req.url
            if cfg["portal_host"] in u and req.method == "POST":
                captured.append((req.method, u, req.post_data))

        page.on("request", on_request)

        ok = False
        foreign = ""
        if relogin_mode:
            ok = relogin_flow(page, cfg)
            targets = []
        for url in targets:
            cn.log("打开 %s" % url[:140])
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                cn.log("打开失败：%r" % (e,))
                continue
            page.wait_for_timeout(1800)

            fr = find_login_frame(page)
            if fr is None:
                cn.log("该页没有登录表单，换下一个入口")
                continue

            try:
                page.screenshot(path=SHOT_PATH)
            except Exception:
                pass

            good, msg = fill_and_submit(fr, cfg, cn.log)
            cn.log(msg)
            if not good:
                return 1
            page.wait_for_timeout(3000)

            # AC 认证生效有延迟（实测约 8~15 秒）。等久一点，
            # 否则会误判失败、跑到下一个入口重复提交一次登录。
            for _ in range(8):
                if cn.probe(cfg)[0] == "online":
                    # 关键：外网通不等于「是我这个号登的」，
                    # 必须核对认证页里的 uid，否则会把别人登录误判成自己成功
                    uid = cn.read_online_uid(cfg)
                    mine = cn.uid_is_mine(cfg, uid)
                    if mine is False:
                        cn.log("外网已通，但在线账号是另一个号（%s），"
                               "不是本次要登的账号 —— 判定为未成功"
                               % cn.mask_uid(uid))
                        foreign = cn.mask_uid(uid)
                        break
                    cn.log("已确认在线账号：%s" % cn.mask_uid(uid))
                    ok = True
                    break
                page.wait_for_timeout(2500)
            if ok:
                break

        try:
            browser.close()
        except Exception:
            pass

    if captured:
        try:
            os.makedirs(os.path.dirname(CAPTURE_PATH), exist_ok=True)
            with open(CAPTURE_PATH, "w", encoding="utf-8") as f:
                for method, u, data in captured:
                    f.write("%s %s\n" % (method, u))
                    if data:
                        f.write("BODY: %s\n" % data)
                    f.write("\n")
            cn.log("已录到 %d 条登录请求 → %s" % (len(captured), CAPTURE_PATH))
        except OSError:
            pass

    if ok:
        cn.log("浏览器登录成功，已确认是你自己的账号在线")
        return 0
    if foreign:
        cn.log("浏览器登录未成功：外网虽通，但在线的是另一个账号 %s"
               "（要登自己的号，得先让那个账号下线）" % foreign)
    else:
        cn.log("浏览器登录未成功，页面截图见 %s" % SHOT_PATH)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
