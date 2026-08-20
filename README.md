# Find-Score

北京信息科技大学（BISTU）成绩监控的简化版。

只做四件事：

1. 登录教务系统
2. 查询全部成绩
3. 与本地缓存比较
4. 发现新成绩或成绩变化时通过 **Bark** 推送

## 特点

- Python 3.13
- 单用户
- Bark-only
- macOS launchd 定时执行
- 每轮运行一次后退出，不常驻 Python
- cookies 持久化，失效后自动重新登录
- Bark 发送失败时不更新成绩缓存，下次查询会重新通知
- 本地缓存与 cookies 使用 `0600` 权限

## 安装

```bash
uv sync --frozen --python 3.13
```

创建 `config.json`：

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

建议：

```bash
chmod 600 config.json
```

## 使用

手动查询一次：

```bash
uv run find-score
```

安装 macOS 后台任务：

```bash
./start.sh
```

其他命令：

```bash
./status.sh
./restart.sh
./stop.sh
```

修改 `interval_minutes` 后执行 `./restart.sh`。

## 运行时文件

```text
config.json          配置
cookies.json         CAS cookies
grades_cache.json    上一次成绩快照
grade_monitor.log    应用日志
```

可用 `FIND_SCORE_HOME=/path/to/data` 指定运行时目录。

## 项目结构

```text
grade_monitor/
├── __main__.py   核心流程、成绩对比、格式化、缓存
├── config.py     单用户配置
├── session.py    CAS 登录与成绩 API
├── notify.py     Bark
├── service.py    macOS launchd
├── storage.py    运行时路径与原子 JSON 写入
└── crypto.py     CAS 密码加密
```

## 测试

```bash
uv run python -m unittest discover -s tests -v
```
