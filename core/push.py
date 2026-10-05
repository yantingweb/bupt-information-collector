"""推送：把过滤后的结果送到手机上。

判断落地与否的标准只有一条 —— **人出门在外、没开电脑时能不能收到**。
本地 markdown 写得再漂亮，人不在机器前面就等于没推。

通道按「要不要额外注册」排序：

  feishu      飞书群自定义机器人（webhook）。建群 → 加机器人 → 抄 webhook URL 填进来。
              不用申请应用、不用审核，是最省事的一条路。
  serverchan  Server酱（微信服务号），需要一个 sendkey
  pushplus    PushPlus（微信），需要一个 token
  bark        iOS，需要一个 key
  telegram    需要 bot token + chat id
  smtp        邮件兜底，需要授权码
  file        永远可用：写回 inbox（等于没推，但不会丢）

加新通道 = 往 SENDERS 里加一个函数，不用碰别的地方。
"""
import datetime
import json
import pathlib
import urllib.parse
import urllib.request

from core.config import resolve

# 手机上没人会读长文。宁可少推几条，也不要他划半天
MAX_BODY = 1600
MAX_ITEMS = 12

SOURCE_LABEL = {"byr": "论坛", "portal": "通知"}


# ── 已推送记录 ──────────────────────────────────────────────────────────────
# 同一条只在第一次出现时推。定时任务一天跑三次，不记就会一天推三遍一样的东西。

def _pushed_path(cfg) -> pathlib.Path:
    return resolve(cfg, f"{cfg['paths']['data_dir']}/pushed.json")


def load_pushed(cfg) -> dict:
    p = _pushed_path(cfg)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def mark_pushed(cfg, keys):
    if not keys:
        return
    p = _pushed_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    done = load_pushed(cfg)
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    for k in keys:
        done[k] = stamp
    # 只留最近 5000 条，别让这个文件无限长
    if len(done) > 5000:
        done = dict(sorted(done.items(), key=lambda kv: kv[1])[-5000:])
    p.write_text(json.dumps(done, ensure_ascii=False, indent=0), encoding="utf-8")


def key_of(it) -> str:
    """Item 或 dict 都能算 key。"""
    if isinstance(it, dict):
        return (it.get("url") or it.get("title") or "").split("?")[0]
    return (getattr(it, "url", "") or getattr(it, "title", "")).split("?")[0]


def unpush(cfg, items):
    """过滤掉已经推过的。"""
    done = load_pushed(cfg)
    return [i for i in items if key_of(i) not in done]


# ── 格式化 ──────────────────────────────────────────────────────────────────

def _g(it, *names, default=""):
    for n in names:
        v = it.get(n) if isinstance(it, dict) else getattr(it, n, None)
        if v not in (None, ""):
            return v
    return default


def _urgency_line(it) -> str:
    """截止时间是手机上最该被看见的东西。

    纯文本通道不渲染 markdown，别加星号 —— 加了只会显示成字面量的 **。
    """
    dl = _g(it, "deadline")
    if not dl:
        return ""
    d = str(dl)[:10]
    try:
        left = (datetime.date.fromisoformat(d) - datetime.date.today()).days
    except ValueError:
        return f"截止 {d}"
    if left < 0:
        return f"已截止（{d}）"
    if left == 0:
        return "今天截止"
    return f"截止 {d[5:]}（剩 {left} 天）"


def format_digest(items, cfg, max_items=MAX_ITEMS) -> tuple[str, str]:
    """返回 (title, body)。手机上读的，一切从简。"""
    push_cfg = cfg.get("push") or {}
    line = push_cfg.get("action_line", 12)

    def score(it):
        return _g(it, "score", default=0) or 0

    hot = [i for i in items if score(i) >= line]
    cold = [i for i in items if score(i) < line]
    hot.sort(key=lambda x: -score(x))

    today = datetime.date.today()
    title = (f"[{today:%m-%d}] {len(hot)} 条要动手" if hot
             else f"[{today:%m-%d}] 今天没有要动手的")

    blocks = []
    for source, label in (("portal", "官方通知"), ("byr", "论坛机会")):
        grp = [i for i in hot if _g(i, "source") == source]
        if not grp:
            continue
        lines = [f"【{label}】"]
        for it in grp[:max_items]:
            lines.append(f"· {_g(it, 'title')}"[:60])
            bits = [f"{SOURCE_LABEL.get(source, source)}·{_g(it, 'channel')}"]
            u = _urgency_line(it)
            if u:
                bits.append(u)
            lines.append("   " + " / ".join(bits))
            if _g(it, "url"):
                lines.append(f"   {_g(it, 'url')}")
        blocks.append("\n".join(lines))

    if cold:
        blocks.append(f"—\n另有 {len(cold)} 条低优先级，见 inbox")

    body = "\n\n".join(blocks)
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rsplit("\n", 1)[0] + "\n…（截断，完整版看 inbox）"
    return title, body


# ── 各通道 ──────────────────────────────────────────────────────────────────

def _post(url, data=None, headers=None, timeout=20):
    body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")[:300]


def _post_json(url, obj, timeout=20):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return _post(url, data=body, headers={"Content-Type": "application/json"},
                 timeout=timeout)


# 模板里的占位符。单独认出来，不然用户看到的是一串 UnicodeEncodeError
_PLACEHOLDER_HINTS = ("在这里填", "填你的", "your-webhook", "xxxx", "<", ">")


def _send_feishu(ch, title, text):
    """飞书群自定义机器人（webhook）。

    建群 → 群设置 → 群机器人 → 添加「自定义机器人」→ 抄下 webhook URL。
    成功响应 `{"code":0,...}`，老版本网关返回 `{"StatusCode":0,...}`，两种都认。
    """
    url = str(ch.get("webhook") or "").strip()
    if not url:
        return False, "缺 webhook"
    # 占位符必须单独认出来。不认的话，URL 里的中文会让 urllib 抛 UnicodeEncodeError，
    # 用户看到的是一句跟"你还没填密钥"毫无关系的英文报错 —— 照着模板直接跑就会撞上。
    if any(h in url for h in _PLACEHOLDER_HINTS):
        return False, ("webhook 还是模板里的占位符。去飞书建个群 → 群设置 → 群机器人 → "
                       "添加「自定义机器人」，把它给的 URL 填进 config.yaml")
    if not url.startswith("https://"):
        return False, f"webhook 看着不像 URL：{url[:50]}"
    payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{text}"}}
    try:
        s, b = _post_json(url, payload)
    except UnicodeEncodeError:
        return False, "webhook 里有非 ASCII 字符 —— 检查 config.yaml 是不是还留着模板里的中文"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    flat = b.replace(" ", "")
    ok = s == 200 and ('"code":0' in flat or '"StatusCode":0' in flat)
    return ok, f"HTTP {s} {b[:120]}"


def _send_serverchan(ch, title, text):
    key = ch.get("key")
    if not key:
        return False, "缺 key"
    s, b = _post(f"https://sctapi.ftqq.com/{key}.send",
                 {"title": title[:100], "desp": text})
    return s == 200, f"HTTP {s} {b[:120]}"


def _send_pushplus(ch, title, text):
    tok = ch.get("token")
    if not tok:
        return False, "缺 token"
    s, b = _post("https://www.pushplus.plus/send",
                 {"token": tok, "title": title[:100], "content": text,
                  "template": ch.get("template", "txt")})
    return s == 200, f"HTTP {s} {b[:120]}"


def _send_bark(ch, title, text):
    key = ch.get("key")
    if not key:
        return False, "缺 key"
    base = (ch.get("server") or "https://api.day.app").rstrip("/")
    url = (f"{base}/{key}/{urllib.parse.quote(title[:100])}/"
           f"{urllib.parse.quote(text[:1200])}")
    s, b = _post(url)
    return s == 200, f"HTTP {s} {b[:120]}"


def _send_telegram(ch, title, text):
    tok, chat = ch.get("token"), ch.get("chat_id")
    if not (tok and chat):
        return False, "缺 token/chat_id"
    s, b = _post(f"https://api.telegram.org/bot{tok}/sendMessage",
                 {"chat_id": chat, "text": f"{title}\n\n{text}"[:4000]})
    return s == 200, f"HTTP {s} {b[:120]}"


def _send_smtp(ch, title, text):
    import smtplib
    from email.header import Header
    from email.mime.text import MIMEText

    need = ("host", "user", "password", "to")
    if any(not ch.get(k) for k in need):
        return False, f"缺 {[k for k in need if not ch.get(k)]}"
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = ch["user"]
    msg["To"] = ch["to"]
    try:
        with smtplib.SMTP_SSL(ch["host"], int(ch.get("port", 465)), timeout=30) as s:
            s.login(ch["user"], ch["password"])
            s.sendmail(ch["user"], [ch["to"]], msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _send_file(cfg, ch, title, text):
    """兜底通道：永远成功，但只是落到 inbox —— 算不上真推送。"""
    inbox = resolve(cfg, cfg["paths"]["inbox_dir"])
    inbox.mkdir(parents=True, exist_ok=True)
    out = inbox / f"{datetime.date.today():%Y-%m-%d}.md"
    with out.open("a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.datetime.now():%H:%M} 推送（未发通道）· {title}\n\n{text}\n")
    return True, f"写入 {out.name}"


SENDERS = {
    "feishu": _send_feishu,
    "serverchan": _send_serverchan,
    "pushplus": _send_pushplus,
    "bark": _send_bark,
    "telegram": _send_telegram,
    "smtp": _send_smtp,
}


def enabled_channels(cfg) -> dict:
    chs = (cfg.get("push") or {}).get("channels") or {}
    return {k: v for k, v in chs.items()
            if isinstance(v, dict) and v.get("enabled")}


def send(cfg, title, text, only=None) -> list[tuple[str, bool, str]]:
    """往所有启用的通道推。返回 [(通道, 成功, 详情)]。

    一个通道挂了不影响别的 —— 半夜三点机器人 token 过期也不该让整条链路断掉。
    """
    chs = enabled_channels(cfg)
    if only:
        chs = {k: v for k, v in chs.items() if k in only}
    if not chs:
        return [("(无通道)", False, "config.yaml 的 push.channels 全是 enabled: false")]

    results = []
    for name, ch in chs.items():
        kind = ch.get("type", name)
        try:
            if kind == "file":
                ok, detail = _send_file(cfg, ch, title, text)
            else:
                fn = SENDERS.get(kind)
                if fn is None:
                    ok, detail = False, f"未知通道类型 {kind}"
                else:
                    ok, detail = fn(ch, title, text)
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        results.append((name, ok, detail))
    log_send(cfg, title, results)
    return results


# ── 推送日志 ────────────────────────────────────────────────────────────────
# 一次推送失败如果是「静默成功」（异常被吞、进程 exit 0、计划任务记 Result=0），
# 唯一的症状就是人没收到东西 —— 那要等他自己察觉。记下来才能事后追。

def _log_path(cfg) -> pathlib.Path:
    return resolve(cfg, f"{cfg['paths']['data_dir']}/push.log")


def log_send(cfg, title, results):
    """每次推送都留一行，成功失败都记。"""
    p = _log_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    brief = " ".join((title or "").split())[:40]
    lines = []
    for name, ok, detail in results:
        d = " ".join(str(detail).split())[:120]
        lines.append(f"{stamp}\t{'OK ' if ok else 'FAIL'}\t{name}\t{brief}\t{d}")
    with p.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def last_success(cfg):
    """最后一次**真的推出去了**的时间。没有则返回 None。

    `file` 通道不算 —— 它永远"成功"，但东西根本没离开这台机器。
    把它算进来的话，`doctor` 会信心满满地报「最后一次推送成功」，
    而手机上什么都没有。这正是这个项目最想避免的那种谎。
    """
    p = _log_path(cfg)
    if not p.exists():
        return None

    chs = (cfg.get("push") or {}).get("channels") or {}
    not_real = {k for k, v in chs.items()
                if isinstance(v, dict) and v.get("type", k) == "file"}

    hit = None
    for line in p.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1].strip() == "OK" \
                and parts[2].strip() not in not_real:
            hit = parts[0].strip()
    return hit
