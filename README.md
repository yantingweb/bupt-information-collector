# bupt-information-collector

**把「今天该看的信息」从两个地方捞出来，过滤掉噪音，只把要动手的那几条推到你手机上。**

它监听两类源：**校园信息门户**（教务 / 学院 / 学生处的通知公告）和**北邮人论坛**（科研进组、
实习内推、竞赛招募）。每天定时扫三遍，过滤、去重、打分，然后通过**飞书**把「截止 / 报名 / 招募」
这类需要你动手的条目推出去 —— 人在外面、没开电脑也能收到。

说白了就是**一个信息监听器**：监听 → 去重 → 打分 → 推送，到手机为止。
它不排日程，也不做目标管理 —— 那些是另一个问题，这个仓库不假装能一起解决。

---

## 先说清楚：谁 clone 下来能直接用

**这个仓库是为北邮写的**，两个源都是北邮的站点：

| 源 | 通用性 | 换学校要改什么 |
|---|---|---|
| `sources/portal.py` 信息门户 | **通用**。它不认具体站点，只找「链接 + 邻近日期」这种列表行 | 改 `config.yaml` 里 `sources.portal.lists` 的 URL |
| `sources/byr.py` 北邮人论坛 | **北邮专属**。登录态（`UTMPUSERID` cookie）、版面代号（`Paper` / `Robot`…）、正文选择器（`td.a-content`）都绑死在 BYR 上 | 得另写一个源 |

所以：

- **北邮的同学** —— clone 下来五分钟能用，`run.py login byr` 登一次就行。
- **别的学校的同学** —— 能直接复用的是**中间三层**（去重 / 打分 / 推送）和通用的 `portal.py`。
  要接自己的论坛或教务，写一个源即可，契约只有一个。

### 写一个新源要做的全部事情

1. 新建 `sources/<名字>.py`，实现两个函数：

```python
def fetch(cfg, only=None, quiet=False):
    """返回 (items_by_channel, watch, errors)

    items_by_channel : {栏目名: [Item, ...]}
    watch            : None 即可（给「关注某个帖子」这类需求预留的）
    errors           : 抓失败的信息列表。会打印出来，但不影响别的源
    """

def read(cfg, url, max_chars=1200):
    """返回 (title, body) —— 给 `run.py read <目标>` 用"""
```

2. 在 `sources/__init__.py` 的 `REGISTRY` 里加一行。
3. 在 `config.yaml` 的 `sources:` 下加一段配置。`run.py scan <名字>` 就能跑。

**`Item` 是全系统唯一的契约**（`core/schema.py`）：`source / channel / title / url` 必填，
其余字段能给就给 —— 其中 `deadline` 最值钱，给了它推送会把这行排到最前面。
下游三层（去重 / 打分 / 推送）完全不关心数据是从哪来的。

---

## 解决的问题

信息本身不贵，**漏掉信息的代价才贵**。保研报名、进组招募、实习内推这类通知，共同点是：

1. 分散在好几个站点，每个都要手动点开翻；
2. 通知列表和论坛帖子页**改版一次，你自己写的爬虫就全废**；
3. 绝大多数是噪音（二手转让、灌水、别的届的校招），真正相关的可能一天就一条；
4. 看到了才算数 —— 只写进本地文件而没推到手机，等于没看。

所以这个东西的设计目标很窄：**别漏，别吵**。

---

## 它怎么工作

```
        采集              去重                判读              推送
  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
  │ 信息门户     │   │ 增量 diff    │   │ 加权打分     │   │ 飞书 webhook│
  │ (公开公告页) │──▶│ seen 快照    │──▶│ rules.yaml   │──▶│ 只推要动手的 │
  │ 北邮人论坛   │   │ 跨栏目折叠   │   │ 词表可调     │   │ 到手机      │
  │ (会话快照)   │   │             │   │             │   │             │
  └─────────────┘   └─────────────┘   └─────────────┘   └─────────────┘
        ▲                                                        │
        └──────────────── 每天 07:30 / 12:30 / 19:00 ─────────────┘
```

四层各自独立，加一个新源的成本只有「写个函数产出 `Item`」，其余全都不用动。

---

## 快速开始

```bash
git clone <this-repo> && cd bupt-information-collector

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp config.example.yaml config.yaml
#   编辑 config.yaml：填 push.channels.feishu.webhook，改 sources.portal.lists 为你自己学校的列表页

python run.py login byr      # 开浏览器登一次论坛，会话存成快照，之后无头复用
python run.py cold           # 冷启动：建去重基线，一条都不推
python run.py scan --push    # 真正扫一遍并推手机
```

设成定时任务（Windows）：

```powershell
# 管理员 PowerShell
.\deploy\Install.ps1
```

Linux / macOS 用 cron：

```cron
30 7,12,19 * * *  cd /path/to/bupt-information-collector && .venv/bin/python run.py scan --push >> data/cron.log 2>&1
```

---

## 三个值得说的地方

### 1. 会话接管，不是逆向登录协议

论坛登录有验证码、有反爬，硬啃协议的话，对方改一次版你就得重来。

这里的做法是：**用真实浏览器登一次，把 `storage_state` 存下来，之后无头复用。**
整个采集层因此完全不需要知道对方怎么加密、怎么发 token —— 验证码、JWT 兑换、
签名这些事根本不出现。代价是首次要人工登录一次。

同一个 `Session` 类被论坛和门户共用，`core/browser.py` 里也顺手打了去掉
`navigator.webdriver` 之类的伪装补丁，因为有些站点会直接拦自动化浏览器。

### 2. 增量 diff，而不是"抓最新的 N 条"

`data/seen/<source>.json` 记录每个栏目下见过的 URL。第二次扫描只有 URL 不在快照里的
才算新条目。定时任务一天跑三次，所以**推送也去重**（`data/pushed.json`）——
同一条只在第一次出现时推，不然你一天会收到三遍一样的东西。

有个细节值得单说：**冷启动时「抽不到日期」不等于「陈旧」。** 通知列表页本来就是
倒序的最新几条，很多院系站点根本不在列表上给日期。如果冷启动逻辑把"没有日期"当成
"太老"丢掉，最值钱的学院通知会一条都不剩 —— 这个坑踩过。

### 3. 打分，而不是"全推给你"

`rules.yaml` 是一张可调的词表，按组累加：`机会`（内推/招募/实习）、`科研进组`、
`方向匹配`（具身/大模型/智能体）、`升学`（保研/推免）、`官方与截止`，还有负向的
`exclude`（社招）和 `noise`（二手/灌水/代购）。

低于 `archive_line`（默认 3 分）的直接丢掉，≥ `action_line`（默认 12 分）的才推手机。
词表是配置不是代码 —— 换方向、换届数，改 YAML 就行。

---

## 踩过的坑（都留了注释）

**`Item.parse_day` 按格式串长度截断字符串 → 静默丢数据。**
最早是这么写的：

```python
datetime.strptime(s[:len(fmt)], fmt)     # s = "2026-09-24" 被截成 "2026-09-"
```

结果所有日期解析失败、全变成 `None`，冷启动逻辑据此把新帖判成"太旧"全部丢弃 ——
**653 条抓进来、0 条推出去，而控制台一片成功。** 最难查的 bug 是不报错的那种。
修法是不做任何截断，按多个格式逐个整体匹配，最后再用正则兜底。

**推送失败曾经是"静默成功"。** 通道调用如果抛异常、被吞掉、进程照样 `exit 0`，
计划任务就会记录 `Result=0`，唯一的症状是「人没收到东西」—— 那要等他自己察觉。
现在每次推送都写一行 `data/push.log`，失败会让进程以非零码退出，`run.py doctor`
一条命令就能回答"我今天为什么没收到推送"。

**计划任务名不能含冒号。** `bupt-information-collector 07:30` 会被 `Register-ScheduledTask` 拒掉
（`0x80070057 参数错误`，还不告诉你哪个参数错了）。另外注册完必须回查一遍，
否则脚本会心安理得地打印"已注册"而实际一条都没装上。

---

## 目录结构

```
run.py                CLI 入口：scan / cold / push / read / login / doctor
config.example.yaml   配置模板（config.yaml 自己填，已被 gitignore）
rules.yaml            打分词表
watchlist.json        监听哪些论坛版面

core/
  config.py           路径与配置加载
  schema.py           Item 数据结构 —— 各源都产出它，下游不关心来源
  browser.py          会话管理。核心是 storage_state 复用，不逆向登录协议
  store.py            落盘：去重快照 + items.jsonl
  rules.py            打分器
  digest.py           渲染成 inbox markdown
  push.py             推送：可插拔通道 + 去重 + 失败留痕

sources/
  byr.py              北邮人论坛
  portal.py           教务 / 学院 / 学生处通知（通用列表页抽取）

deploy/
  Install.ps1         Windows 计划任务

inbox/                每天生成的 markdown 摘要（gitignore）
data/                 去重快照、items、推送记录（gitignore）
```

---

## 边界

- **只做「采 → 筛 → 推」这条链路。** 不做日程排期、不做目标管理 —— 那是另一件事。
- 论坛源需要**首次人工登录**一次（会话快照会过期，过期时 `scan` 会提示重登）。
- `sources.portal.lists` 里默认填的是北邮的几个列表页，换学校改 URL 即可；
  抽取逻辑是通用的（找"链接 + 邻近日期"的行），改版时通常只需调 CSS selector。
- 前端反自动化检测很强的站点（比如某些 SPA 教学系统）这套办法不保证能过，
  那种情况的正确做法是直接走它的 HTTP 接口，而不是跟浏览器指纹死磕。

## License

MIT
