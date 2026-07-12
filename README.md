# BISTU 成绩监控

北京信息科技大学（BISTU）教务系统成绩自动监控 → Telegram 推送。

- 每 20 分钟（可调）自动查一次教务系统
- 发现新成绩或分数变更时，推一条带 **平时成绩 / 期末成绩 / 总成绩 / 学分** 的消息
- 同时推送本学期 + 总平均分（按学分加权）
- 支持多个学号（共用一个 bot 或各自的 chat 都行）
- macOS launchd / Linux systemd-user 后台服务
- CAS cookie 持久化：会话能用就直接用，过期才登一次
- 通知 outbox 续传：Telegram 部分失败时只重试未确认的消息

---

## 工作目录

```
Find-Score/
├── grade_monitor/              主程序包
│   ├── __init__.py             包标识
│   ├── __main__.py             CLI 入口（run_once / run_loop）
│   ├── constants.py            路径常量、URL 端点
│   ├── logging_config.py       日志配置（带 2MB 滚动）
│   ├── config.py               配置加载 & 多用户兼容
│   ├── crypto.py               AES 密码加密
│   ├── session.py              教务系统 HTTP 会话
│   ├── storage.py              原子 JSON 写入与运行时路径工具
│   ├── cache.py                成绩缓存、outbox 与旧格式迁移
│   ├── changes.py              纯函数式成绩快照差异计算
│   ├── formatting.py           学期解析、成绩格式化、平均分
│   ├── notify.py               Telegram + macOS/Linux 桌面通知
│   ├── polling.py              单次轮询逻辑
│   ├── monitor.py              单用户处理 & 失败告警
│   ├── locking.py              macOS/Linux 单实例进程锁
│   └── service.py              launchd/systemd-user 服务管理
├── run_monitor.py              顶层启动脚本
├── config.json                 你的账号 + Telegram 配置（git 忽略）
├── pyproject.toml / uv.lock    项目元数据与锁定依赖
├── tests/                      离线单元测试
├── start.sh / stop.sh          跨平台后台服务启停
├── restart.sh / status.sh      重启 / 查状态
├── grade_monitor.log           运行日志（带 2MB 滚动）
├── grades_cache.<name>.json    每用户成绩缓存 + 失败状态（git 忽略）
├── cookies.<name>.json         每用户持久化 cookies（git 忽略）
└── .venv/                      Python 虚拟环境（git 忽略）
```

后台定义由程序生成：

- macOS：`~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist`
- Linux：`${XDG_CONFIG_HOME:-~/.config}/systemd/user/find-score.service`

运行时目录默认为包含 `config.json` 的源码根目录；使用已安装的 `find-score`
命令时则为当前目录。可通过 `FIND_SCORE_HOME=/path/to/data` 显式指定
`config.json`、缓存、cookies 和日志所在目录。

---

## 首次部署

1. **建虚拟环境 + 装锁定依赖**
   ```bash
   uv sync --frozen
   ```
   项目支持 Python 3.10+；`.python-version` 仅将本地开发环境锁定在 3.13。
   macOS 和 Linux 需要分别执行 `uv sync`，不要跨系统复制 `.venv`。

2. **配 Telegram bot**
   - 找 [@BotFather](https://t.me/BotFather)，`/newbot` 创建一个，记下 `bot_token`
   - 找 [@userinfobot](https://t.me/userinfobot)，给他发任意消息，拿到你的 `chat_id`
   - 在新 bot 里给自己发条消息，激活会话（否则 bot 不能主动联系你）

3. **写 `config.json`**（参考 `config.json` 模板，见下文"多用户配置"）
   ```bash
   chmod 600 config.json
   ```

4. **启动当前平台的后台服务**
   ```bash
   ./start.sh
   ```
   会立刻跑一次（拉现有成绩做基线，仅发初始化通知，不逐条推送），之后按配置间隔轮询。

---

## 日常操作

```bash
./status.sh    # launchctl / systemctl --user 状态 + 最近日志
./restart.sh   # 改代码 / 改 config 后重启
./stop.sh      # 停止、禁用并删除当前用户服务定义
```

等价的 Python 命令：

```bash
uv run find-score-service start
uv run find-score-service stop
uv run find-score-service restart
uv run find-score-service status
uv run find-score-service render      # 只查看将生成的 plist / unit
```

**手动跑一次**（不等 20 分钟）：
```bash
uv run find-score
# 等价：uv run python -m grade_monitor
```

**循环模式**（持续运行，不通过系统服务；调试用）：
```bash
uv run find-score loop
```

### Linux 补充说明

- 后台管理依赖 systemd user manager；不支持 systemd 的发行版可手动运行 loop 模式。
- Telegram 在服务器/无桌面环境中仍正常工作。桌面通知需要可选的 `notify-send`。
- systemd user service 默认跟随登录会话。如需未登录或注销后仍运行，由管理员显式执行
  `loginctl enable-linger "$USER"`；Find-Score 不会自动修改该系统策略。

---

## 多用户配置

`config.json` 的 `users` 数组里追加一项，互不干扰：

```json
{
  "users": [
    {
      "name": "yumeng",
      "jwxt": {
        "username": "2024012616",
        "password": "你的教务密码"
      },
      "telegram": {
        "bot_token": "1234567890:AAA...",
        "chat_id": "5257180504"
      }
    },
    {
      "name": "alice",
      "jwxt": {
        "username": "2024999999",
        "password": "另一个人的教务密码"
      },
      "telegram": {
        "bot_token": "1234567890:AAA...",
        "chat_id": "1111111111"
      }
    }
  ],
  "interval_minutes": 20
}
```

字段说明：
- **`name`**：昵称（最多 64 字符），用于缓存/cookies 文件和推送前缀；不能重名或映射到同一文件名
- **`jwxt.username`** / **`password`**：教务系统学号 + 密码
- **`telegram.bot_token`** / **`chat_id`**：可以**两人共用一个 bot**（同一个 token），各自填自己的 `chat_id`；也可以完全独立
- 推送格式：`🎓 [alice] 发现 X 条新成绩！`，前缀里的 `[name]` 让你能分清是谁的成绩
- 改完 `config.json` 执行 `./restart.sh` 生效

---

## 推送格式示例

```
🎓 [yumeng] 发现 2 条新成绩！
────────────────────

常微分方程
学期：大二第一学期
平时成绩：98
期末成绩：90
总成绩：92
学分：3.0

数学分析(3)
学期：大二第一学期
平时成绩：99
期末成绩：91
总成绩：93
学分：5.0


📊 [yumeng] 平均分统计
大二第一学期：91.36
大一小学期：98.00
大一第二学期：90.51
大一第一学期：84.86
总平均分：88.90
```

分数变更（旧 → 新）：
```
🔄 [yumeng] 1 条成绩有变更！
────────────────────

线性代数
学期：大一第二学期
总成绩：85 → 88
学分：4.0
```

---

## 调试 & 排错

```bash
tail -f grade_monitor.log                 # 看实时日志
uv run find-score                         # 手动跑一次（看输出）
uv run python -m unittest discover -s tests -v  # 跑离线测试
rm grades_cache.<name>.json               # 清缓存 → 下次重建静默基线
rm cookies.<name>.json                    # 清 cookies → 下次会重新登录
```

**常见情况**

| 现象 | 原因 / 解法 |
|---|---|
| Telegram 收不到任何消息 | 1) `bot_token` / `chat_id` 配错；2) 没在 bot 里发过消息激活会话 |
| 日志大量 SSL EOF | CAS 限流（一般是短时多次手动登录触发）。等 10-15 分钟，或换 IP（手机热点） |
| 推送 `[name] 连续 N 次失败` | 真出问题了，检查日志末尾的具体异常 |
| 缓存里出现 `outbox` | 正常：用于续传尚未确认的 Telegram 消息，送达后会自动清空 |
| 想换 20 分钟为其他间隔 | 编辑 `config.json` 的 `interval_minutes`，下一轮循环自动生效 |
| Linux 提示无法连接 user bus | 确认在普通用户登录会话内执行，不要使用 `sudo systemctl --user` |
| Linux 没有桌面弹窗 | 安装 `notify-send` 并确保有图形会话；不影响 Telegram |
| 提示已有 Find-Score 进程 | 后台服务正在运行；单实例锁会防止并发查询和重复推送 |

---

## 休眠 / 后台运行

- **锁屏或仅显示器熄灭**：系统没有 suspend，macOS 和 Linux 都会继续查询。
- **合盖、suspend、hibernate**：两端都不查询、不联网，也不会由 Find-Score 唤醒机器。
- **唤醒后**：分段墙钟等待会在最多约 30 秒内合并补查一轮，不会按错过轮数突发请求。
- **异常退出**：launchd/systemd-user 会自动重启，节流间隔为 60 秒。
- 单次查询约占用数秒 CPU 和少量网络流量。

---

## 代码架构

```
grade_monitor/
├── constants.py         所有 URL、路径、阈值常量
├── logging_config.py    日志配置 → 其他模块 import log
├── config.py            配置文件读取
├── crypto.py            AES-CBC 密码加密
├── session.py           JwxtSession（CAS 登录 + API 调用）
├── storage.py           原子 JSON 写入与安全文件名
├── cache.py             成绩快照 + 通知 outbox + 旧格式迁移
├── changes.py           纯成绩差异计算
├── formatting.py        学期解析 + 成绩格式化 + 加权平均
├── notify.py            Telegram + macOS/Linux 通知适配器
├── polling.py           查询 → 差异 → outbox 投递编排
├── monitor.py           单用户边界、状态落盘与失败告警
├── locking.py           POSIX 进程级单实例锁
├── service.py           launchd/systemd-user 定义与生命周期
└── __main__.py          CLI 入口
```

接口细节详见各模块 docstring：

- **CAS 登录**: `https://wxjw.bistu.edu.cn/authserver/login` （AES-CBC 加密密码）
- **成绩列表**: `POST /jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do`，body `pageSize=200&pageNumber=1`
- **分项成绩**: `POST /jwapp/sys/cjzhcxapp/api/wdcj/details.do`，body `WID=<row.WID>`
  - 返回 `datas.details.itemScores[]`，`code=PSCJ` 是平时，`code=QMCJ` 是期末
- 注意：CAS 登录成功后必须 GET 一次 `/jwapp/sys/cjzhcxapp/*default/index.do?forceApp=cjzhcxapp` 才能注册应用上下文，否则 cxwdcj.do 会 403
