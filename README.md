# Find-Score

北京信息科技大学（BISTU）成绩监控 CLI。

程序负责登录教务系统、查询成绩、与本地缓存比较，并在发现新成绩或成绩变化时通过 **Bark** 推送。

## 特点

- Python 3.13
- 单用户
- Bark-only
- 统一 `find-score` 命令行入口
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

仓库中的 `.python-version` 已固定为 Python 3.13。

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

`interval_minutes` 是自动查询间隔，单位为分钟。

## 统一 CLI

查看帮助：

```bash
uv run find-score --help
```

### 查询成绩

```bash
uv run find-score check
```

为兼容原来的用法，直接执行下面的命令仍然等价于 `check`：

```bash
uv run find-score
```

首次运行会读取当前全部成绩作为基线，并通过 Bark 发送初始化通知。

### 后台任务

```bash
uv run find-score start
uv run find-score status
uv run find-score restart
uv run find-score stop
uv run find-score render
```

`start` 会生成并加载：

```text
~/Library/LaunchAgents/com.hhhhollow.gradeMonitor.plist
```

任务设置了 `RunAtLoad`，加载后会立即执行一次，之后按配置的间隔继续执行。

两次查询之间 launchd 显示进程当前没有运行是正常的，因为 Find-Score 是定时启动的 one-shot 任务，而不是常驻服务。

### 查看日志

默认查看最后 30 行：

```bash
uv run find-score logs
```

指定行数：

```bash
uv run find-score logs -n 100
```

`-n 0` 表示输出全部日志。

### 检查配置

```bash
uv run find-score config
```

该命令只显示配置文件位置、有效性、脱敏后的学号、查询间隔和 Bark 的非敏感配置，不输出教务密码或 Bark key。

### 查看版本

```bash
uv run find-score --version
```

## 安装为全局 CLI

如果希望离开项目目录后也能直接执行，在包含 `config.json` 的项目根目录运行：

```bash
uv tool install .
export FIND_SCORE_HOME="$PWD"
```

要让新终端也生效，将 `export FIND_SCORE_HOME="/项目的绝对路径"` 加入 `~/.zshrc`。全局安装不会复制配置文件。

之后可以直接使用：

```bash
find-score check
find-score status
find-score logs
```

## Mac mini 长期运行注意事项

当前使用的是 **LaunchAgent**，因此：

- Mac mini 应避免进入系统睡眠，否则定时任务可能无法按预期间隔执行。
- 当前 macOS 用户需要保持登录；注销后 LaunchAgent 不会继续运行。
- Mac 重启并重新登录该用户后，LaunchAgent 会重新加载并执行。

## 常用命令

```bash
# 安装 / 同步锁定依赖
uv sync --frozen

# 手动检查一次
uv run find-score check

# 启动 launchd 定时任务
uv run find-score start

# 查看任务状态和最近日志
uv run find-score status

# 重新生成配置并重启任务
uv run find-score restart

# 停止并删除任务
uv run find-score stop

# 查看生成的 launchd plist
uv run find-score render

# 查看应用日志
uv run find-score logs -n 50

# 检查配置
uv run find-score config
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

运行时目录优先使用 `FIND_SCORE_HOME`；未设置时，使用含有 `config.json` 的源码项目目录，否则使用当前工作目录。可显式指定：

```bash
FIND_SCORE_HOME=/path/to/data find-score check
```

## 项目结构

```text
grade_monitor/
├── cli.py        统一 CLI 入口与命令分发
├── __main__.py   单次查询、成绩比较、格式化、缓存
├── config.py     单用户配置
├── session.py    CAS 登录与成绩 API
├── notify.py     Bark 推送
├── service.py    macOS launchd 管理
├── storage.py    运行时路径与原子 JSON 写入
└── crypto.py     CAS 密码加密
```

## 测试和代码检查

运行单元测试：

```bash
uv run python -m unittest discover -s tests -v
```

CI 还会执行 Ruff、Pyright 和 coverage，并使用 `uv sync --frozen` 验证锁文件部署路径。
