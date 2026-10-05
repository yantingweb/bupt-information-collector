"""浏览器会话管理。所有源共用一套逻辑，别每个源各写一遍。

设计前提：**不逆向登录协议。** 人在真实浏览器里登一次，我们把 storage_state 存下来，
之后无头复用。验证码、站点改版、token 兑换这些事情因此全部不存在。
"""
import os
import pathlib
import platform
import shutil

from playwright.sync_api import sync_playwright

# 有些校内老系统会直接拦自动化浏览器（页面上直接写「不支持该浏览器」）。
# 两件事要同时做：装成正常浏览器（去掉 automation 开关、补 navigator 字段），
# 以及尽量用本机真实的 Edge/Chrome —— UA 和指纹都是真的，不用跟网站玩猜谜。
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
]

STEALTH_JS = """
try {
  Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
  Object.defineProperty(navigator, 'languages',
      {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
  Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
  window.chrome = window.chrome || {runtime: {}};
} catch (e) {}
"""

_WIN_EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
_WIN_CHROME = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
               r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
_MAC_EDGE = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
_MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _known_paths(which: str):
    """按平台给出常见安装位置，最后再问一遍 PATH。"""
    sysname = platform.system()
    if which in ("edge", "msedge"):
        fixed = _WIN_EDGE if sysname == "Windows" else \
            ([_MAC_EDGE] if sysname == "Darwin" else [])
        return fixed + [shutil.which("microsoft-edge"),
                        shutil.which("microsoft-edge-stable")]
    if which == "chrome":
        fixed = _WIN_CHROME if sysname == "Windows" else \
            ([_MAC_CHROME] if sysname == "Darwin" else [])
        return fixed + [shutil.which("google-chrome"),
                        shutil.which("google-chrome-stable")]
    return []


def _pw_cache():
    """playwright 下载浏览器内核的缓存目录（各平台不同）。"""
    if platform.system() == "Windows":
        base = pathlib.Path(os.environ.get("LOCALAPPDATA")
                            or (pathlib.Path.home() / "AppData" / "Local"))
        return base / "ms-playwright"
    if platform.system() == "Darwin":
        return pathlib.Path.home() / "Library" / "Caches" / "ms-playwright"
    return pathlib.Path.home() / ".cache" / "ms-playwright"


# 只认完整版 chromium。**别用 chromium_headless_shell** ——
# 装了 `playwright install chromium` 而没装 headless shell 时，headless 启动
# 会去找 headless shell 并报「Executable doesn't exist」。显式指完整版最稳。
_PW_CHROMIUM = [
    "chromium-*/chrome-win64/chrome.exe",
    "chromium-*/chrome-win/chrome.exe",
    "chromium-*/chrome-linux64/chrome",
    "chromium-*/chrome-linux/chrome",
    "chromium-*/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium",
    "chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium",
]


def playwright_chromium():
    base = _pw_cache()
    hits = []
    for pat in _PW_CHROMIUM:
        hits += list(base.glob(pat))
    return str(sorted(hits)[-1]) if hits else None


def browser_exe(cfg=None):
    """实际要用的浏览器路径。优先本机真实 Edge/Chrome，最后退回 playwright 自带 chromium。"""
    net = ((cfg or {}).get("net") or {})
    if net.get("executable_path"):
        return net["executable_path"]
    which = str(net.get("browser", "chromium")).lower()
    for p in _known_paths(which):
        if p and pathlib.Path(p).exists():
            return p
    return playwright_chromium()


_CHANNEL = {"edge": "msedge", "chrome": "chrome"}


def launch(pw, cfg=None, headless=True):
    """无头时别用 executable_path 硬指 —— 那样 Edge/Chrome 会跑成老式 headless，
    UA 里带 `HeadlessChrome`，前端检测一眼就看出来。
    走 channel 才是新版无头模式，UA 和平时一样。"""
    net = ((cfg or {}).get("net") or {})
    which = str(net.get("browser", "chromium")).lower()
    if not net.get("executable_path") and which in _CHANNEL:
        try:
            return pw.chromium.launch(headless=headless, channel=_CHANNEL[which],
                                      args=STEALTH_ARGS)
        except Exception:
            pass  # 没装那个浏览器就退回自带 chromium，别让整条链路挂掉
    return pw.chromium.launch(headless=headless, executable_path=browser_exe(cfg),
                              args=STEALTH_ARGS)


def harden(ctx):
    """给 context 打伪装补丁。每个 context 都要打，不是每个 page。"""
    ctx.add_init_script(STEALTH_JS)
    return ctx


class Session:
    """包住 playwright 生命周期，业务代码只管拿 page。"""

    def __init__(self, cfg, source_key: str):
        src = cfg["sources"][source_key]
        self.cfg = cfg
        self.key = source_key
        self.state_path = _resolve_session(cfg, src["session"])
        self.base = src.get("base") or next(
            (l["url"] for l in src.get("lists", [])), None)
        # 公开的公告页不需要登录。默认 true，只有明确标 false 的源允许匿名抓
        self.requires_login = src.get("requires_login", True)
        self.timeout = cfg["net"]["timeout_ms"]
        self._pw = None
        self._browser = None
        self.ctx = None

    def __enter__(self):
        has_state = self.state_path.exists()
        self.logged_in = has_state
        if not has_state:
            if self.requires_login:
                raise SystemExit(
                    f"[{self.state_path.name}] 没有会话快照。"
                    f"先跑：python run.py login {self.key}")
            print(f"  （{self.key} 无会话，按公开页匿名抓取）")

        self._pw = sync_playwright().start()
        self._browser = launch(self._pw, self.cfg, headless=True)
        state = str(self.state_path) if has_state else None
        self.ctx = harden(self._browser.new_context(storage_state=state))
        return self

    def __exit__(self, *exc):
        if self.ctx:
            self.ctx.close()
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def new_page(self):
        return self.ctx.new_page()

    def ensure_page(self, url=None):
        """拿到一个「已经停在同源页面上」的 page。

        page.evaluate 里发 fetch 是从当前页面的 origin 发出的。停在 about:blank
        上时 origin 是 null，请求会被浏览器直接拒掉（TypeError: Failed to fetch），
        看起来像接口挂了，其实是没导航。
        """
        p = self.ctx.pages[0] if self.ctx.pages else self.new_page()
        if p.url in ("about:blank", "", None):
            p.goto(url or self.base, wait_until="domcontentloaded", timeout=self.timeout)
        return p

    def cookie(self, *names):
        """按名字取 cookie。判登录态优先用这个 —— 不依赖页面，不受 HttpOnly 和
        document.cookie 访问限制影响，也不需要先起一个 page。"""
        want = {n.lower() for n in names}
        for c in self.ctx.cookies():
            if c["name"].lower() in want and c["value"] not in ("", "guest"):
                return c["value"]
        return None


def _resolve_session(cfg, filename: str) -> pathlib.Path:
    from core.config import resolve
    return resolve(cfg, f"{cfg['paths']['session_dir']}/{filename}")


def token_probe():
    """通用登录探测器。

    不猜选择器（SPA 站点没有用户名这种稳定节点），而是在 localStorage 和
    cookie 里找 token / JWT / 用户 id 的痕迹。找到就返回指纹，没找到返回 None。
    """
    js = """() => {
      const parts = [];
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i), v = localStorage.getItem(k) || '';
          if (/token|eyJ|userId|uid|userInfo|studentId|user_id|phone|realName/i.test(k + v))
            parts.push('LS:' + k + '=' + v.slice(0, 80));
        }
      } catch (e) {}
      const cks = (document.cookie || '').split(';').map(s => s.trim()).filter(Boolean);
      for (const c of cks) {
        if (/token|eyJ|uid|user|sid|SESSION|login|auth/i.test(c)) parts.push('CK:' + c.slice(0, 80));
      }
      return parts.length ? parts.sort().join('|') : null;
    }"""
    return lambda p: p.evaluate(js)


def login(source_key: str, start_url: str, wait_seconds: int = 300, probe=None,
          verify=None):
    """开一个真实浏览器让他登录，登完存 storage_state。

    probe:  可选 callable(page) -> str|None，判断「登上了没」。
    verify: 可选 callable(page) -> bool。存完快照再验一次，确认没被踢回登录页。
            有些站点未登录也会下发一串随机 cookie（看着像 token），probe 一兴奋
            就报「登录成功」，结果带快照打开又被踢回去 —— 所以要 verify。

    超时也不空手而归：先把当前 storage_state 存下来。探测器没命中不代表没登上，
    存下来至少能拿 `scan` 验证一次，不用让人再登一遍。
    """
    import time

    from core.config import load_config, resolve

    cfg = load_config()
    dst = resolve(cfg, f"{cfg['paths']['session_dir']}/"
                       f"{cfg['sources'][source_key]['session']}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        b = launch(p, cfg, headless=False)
        ctx = harden(b.new_context())
        page = ctx.new_page()
        page.goto(start_url, wait_until="domcontentloaded", timeout=60000)

        before = probe(page) if probe else None
        print(f"\n浏览器已打开 {start_url}")
        print(f"请在窗口里登录。当前状态：{before or '未登录'}")
        print(f"最多等 {wait_seconds} 秒，登录后脚本会自动接管。\n")

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(3)
            try:
                now = probe(page) if probe else None
            except Exception:
                continue
            if now and now != before:
                if verify and not _safe(verify, page):
                    print("  状态变了但自检没过（还被踢回登录页），继续等你操作…")
                    continue
                ctx.storage_state(path=str(dst))
                print(f"登录成功：{str(now)[:80]}")
                print(f"会话已存到 {dst}  （别让它进 git）")
                ctx.close()
                b.close()
                return True

        print("等待超时，会话没保存。再跑一次这个命令即可。")
        ctx.close()
        b.close()
        return False


def _safe(fn, *a):
    """verify 里 goto 失败不该把整个登录流程打断。"""
    try:
        return fn(*a)
    except Exception as e:
        print(f"  （自检异常 {type(e).__name__}，当作没过）")
        return False
