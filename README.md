# Find-Score

北京信息科技大学（BISTU）成绩监控的简化版。

程序只做四件事：登录教务系统、查询成绩、与本地缓存比较，并在发现新成绩或成绩变化时通过 **Bark** 推送。

## 特点

- Python 3.13
- 单用户
- Bark-only
- macOS launchd 定时执行
- 每轮查询完成后退出，不常驻 Python
- cookies 持久化，失效后自动重新登录
- 成绩接口自动分页
- Bark 发送失败时不更新成绩缓存，下次查询会重新通知
- 本地缓存与 cookies 使用 `0600` 权限

## 环境要求

- macOS
- `uv`
- Python 3.13
- Bark

仓库中的 `.python-version` 已固定为 Python 3.13，因此在项目目录直接执行 `uv sync` 即可。

## 安装

```bash
uv sync --frozen
```

创建项目根目录下的 `config.json`：

```json
{
  "jwxt": {
    "username": "你的学号",
    "password": "你的教务密码"
  },
  "bark": {
    "key": "你的 Bark key",
    "server": "https://api.day.app",
    "group": "Find-Score",
    "sound": "bell"
  },
  "interval_minutes": 20
}
```

保护配置文件：

```bash
chmod 600 config.json
```

`interval_minutes` 是自动查询间隔，单位为分钟，例如 `20` 表示每 20 分钟启动一次查询。

## 手动查询

执行一次成绩检查并退出：

```bash
uv run find-score
```

也可以直接运行模块：

```bash
uv run python -m grade_monitor
```

首次运行会读取当前全部成绩作为基线，并通过 Bark 发送初始化通知。

## macOS 后台定时运行

Find-Score 使用 macOS 原生 **launchd LaunchAgent**。不需要长期保持 Python 进程运行；launchd 会按 `interval_minutes` 周期启动一次查询，查询完成后进程退出。

### 启动后台任务

```bash
uv run find-score-service start
```

`start` 会生成并加载：

```text
~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist
```

任务设置了 `RunAtLoad`，加载后会立即执行一次，之后按配置的间隔继续执行。

### 查看状态和最近日志

```bash
uv run find-score-service status
```

两次查询之间 launchd 显示进程当前没有运行是正常的，因为 Find-Score 是定时启动的 one-shot 任务，而不是常驻服务。

### 修改查询间隔或重新加载

修改 `config.json` 中的 `interval_minutes` 后执行：

```bash
uv run find-score-service restart
```

修改代码或依赖后，也可以先同步环境再重启：

```bash
uv sync --frozen
uv run find-score-service restart
```

### 停止后台任务

```bash
uv run find-score-service stop
```

这会卸载 launchd 任务并删除对应的 LaunchAgent plist。

### 查看将生成的 plist

```bash
uv run find-score-service render
```

## Mac mini 长期运行注意事项

当前使用的是 **LaunchAgent**，因此：

- Mac mini 应避免进入系统睡眠，否则定时任务可能无法按预期间隔执行。
- 当前 macOS 用户需要保持登录；注销后 LaunchAgent 不会继续运行。
- Mac 重启并重新登录该用户后，LaunchAgent 会重新加载并执行。

如果 Mac mini 长期开机、用户保持登录并关闭自动睡眠，这种方式适合持续运行 Find-Score。

## 常用命令

```bash
# 安装 / 同步锁定依赖
uv sync --frozen

# 手动检查一次
uv run find-score

# 启动 launchd 定时任务
uv run find-score-service start

# 查看任务状态和日志
uv run find-score-service status

# 重新生成配置并重启任务
uv run find-score-service restart

# 停止并删除任务
uv run find-score-service stop

# 查看生成的 launchd plist
uv run find-score-service render
```

## 运行时文件

```text
config.json          配置
cookies.json         CAS cookies
grades_cache.json    上一次成绩快照
grade_monitor.log    应用日志
launchd.stderr.log   launchd 标准错误日志
.grade_monitor.lock  防止查询任务重叠运行
```

默认运行时目录是项目目录。也可以通过环境变量指定：

```bash
FIND_SCORE_HOME=/path/to/data uv run find-score
```

## 项目结构

```text
grade_monitor/
├── __main__.py   单次查询、成绩比较、格式化、缓存
├── config.py     单用户配置
├── session.py    CAS 登录与成绩 API
├── notify.py     Bark 推送
├── service.py    macOS launchd 管理
├── storage.py    运行时路径与原子 JSON 写入
└── crypto.py     CAS 密码加密
```

项目根目录只保留 Python CLI，不再使用 `start.sh`、`stop.sh`、`restart.sh` 或 `status.sh` 包装脚本。

## 测试和代码检查

运行单元测试：

```bash
uv run python -m unittest discover -s tests -v
```

CI 还会执行 Ruff、Pyright 和 coverage，并使用 `uv sync --frozen` 验证锁文件部署路径。
