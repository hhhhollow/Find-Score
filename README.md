# BISTU 成绩监控

北京信息科技大学（BISTU）教务系统成绩自动监控 → Telegram 推送。

- 每 20 分钟（可调）自动查一次教务系统
- 发现新成绩或分数变更时，推一条带 **平时成绩 / 期末成绩 / 总成绩 / 学分** 的消息
- 同时推送本学期 + 总平均分（按学分加权）
- 支持多个学号（共用一个 bot 或各自的 chat 都行）
- macOS launchd 调度：合盖睡眠时不跑，醒来再继续
- CAS cookie 持久化：会话能用就直接用，过期才登一次

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
│   ├── cache.py                成绩缓存 & 原子写文件
│   ├── formatting.py           学期解析、成绩格式化、平均分
│   ├── notify.py               Telegram 推送（带重试）
│   ├── polling.py              单次轮询逻辑
│   └── monitor.py              单用户处理 & 失败告警
├── run_monitor.py              顶层启动脚本
├── config.json                 你的账号 + Telegram 配置（git 忽略）
├── requirements.txt            Python 依赖
├── start.sh / stop.sh          启停 launchd
├── restart.sh / status.sh      重启 / 查状态
├── grade_monitor.log           运行日志（带 2MB 滚动）
├── grades_cache.<name>.json    每用户成绩缓存 + 失败状态（git 忽略）
├── cookies.<name>.json         每用户持久化 cookies（git 忽略）
└── .venv/                      Python 虚拟环境（git 忽略）
```

LaunchAgent plist 在 `~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist`。

---

## 首次部署

1. **建虚拟环境 + 装依赖**
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

2. **配 Telegram bot**
   - 找 [@BotFather](https://t.me/BotFather)，`/newbot` 创建一个，记下 `bot_token`
   - 找 [@userinfobot](https://t.me/userinfobot)，给他发任意消息，拿到你的 `chat_id`
   - 在新 bot 里给自己发条消息，激活会话（否则 bot 不能主动联系你）

3. **写 `config.json`**（参考 `config.json` 模板，见下文"多用户配置"）

4. **加载 launchd**
   ```bash
   ./start.sh
   ```
   会立刻跑一次（拉一遍现有成绩做基线，不推送），之后每 20 分钟自动触发。

---

## 日常操作

```bash
./status.sh    # 查状态（是否已加载、最近日志）
./restart.sh   # 改代码 / 改 config 后重新加载
./stop.sh      # 完全卸载
```

**手动跑一次**（不等 20 分钟）：
```bash
.venv/bin/python -m grade_monitor
```

**用老的循环模式**（持续运行，不通过 launchd；调试用）：
```bash
.venv/bin/python -m grade_monitor loop
```

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
- **`name`**：任意昵称，仅用于给缓存/cookies 文件命名和推送消息加前缀。两人不能重名
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
.venv/bin/python -m grade_monitor         # 手动跑一次（看输出）
rm grades_cache.<name>.json               # 清缓存 → 下次会把所有成绩当新的推一遍
rm cookies.<name>.json                    # 清 cookies → 下次会重新登录
```

**常见情况**

| 现象 | 原因 / 解法 |
|---|---|
| Telegram 收不到任何消息 | 1) `bot_token` / `chat_id` 配错；2) 没在 bot 里发过消息激活会话 |
| 日志大量 SSL EOF | CAS 限流（一般是短时多次手动登录触发）。等 10-15 分钟，或换 IP（手机热点） |
| 推送 `[name] 连续 N 次失败` | 真出问题了，检查日志末尾的具体异常 |
| 缓存里出现没见过的字段 | 旧字段会在 load 时自动清掉，无需手动处理 |
| 想换 20 分钟为其他间隔 | 编辑 `config.json` 的 `interval_minutes`，下一轮循环自动生效 |

---

## 关于耗电 / 后台运行

- **合盖断电不会跑**：launchd 不会唤醒电脑来执行任务（plist 未设 `WakeFromSleep`），睡眠期间错过的会合并为唤醒后追跑一次
- **不插电开盖**会跑：每次 ~5 秒 CPU + 10KB 流量，可忽略
- **KeepAlive 自动重启**：进程异常退出后 launchd 会自动重启，间隔至少 60 秒

---

## 代码架构

```
grade_monitor/
├── constants.py         所有 URL、路径、阈值常量
├── logging_config.py    日志配置 → 其他模块 import log
├── config.py            配置文件读取
├── crypto.py            AES-CBC 密码加密
├── session.py           JwxtSession（CAS 登录 + API 调用）
├── cache.py             成绩缓存（原子写入 + 旧格式迁移）
├── formatting.py        学期解析 + 成绩格式化 + 加权平均
├── notify.py            Telegram 推送
├── polling.py           单次轮询（对比缓存 → 推送变更）
├── monitor.py           用户处理 + 失败告警
└── __main__.py          CLI 入口
```

接口细节详见各模块 docstring：

- **CAS 登录**: `https://wxjw.bistu.edu.cn/authserver/login` （AES-CBC 加密密码）
- **成绩列表**: `POST /jwapp/sys/cjzhcxapp/modules/wdcj/cxwdcj.do`，body `pageSize=200&pageNumber=1`
- **分项成绩**: `POST /jwapp/sys/cjzhcxapp/api/wdcj/details.do`，body `WID=<row.WID>`
  - 返回 `datas.details.itemScores[]`，`code=PSCJ` 是平时，`code=QMCJ` 是期末
- 注意：CAS 登录成功后必须 GET 一次 `/jwapp/sys/cjzhcxapp/*default/index.do?forceApp=cjzhcxapp` 才能注册应用上下文，否则 cxwdcj.do 会 403
