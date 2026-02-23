# Logging（kimi-cli）

> 新手要点：先分清“谁启用日志（入口）”和“日志写到哪（CLI/Web 两条路径）”。

本文基于：
- `kimi-cli/src/kimi_cli/__init__.py`
- `kimi-cli/src/kimi_cli/cli/__init__.py`
- `kimi-cli/src/kimi_cli/app.py`
- `kimi-cli/src/kimi_cli/utils/logging.py`
- `kimi-cli/src/kimi_cli/web/app.py`

---

## 1. 全局图：日志启用总流程

```text
import kimi_cli
  -> 默认 disable("kimi_cli")

CLI 启动
  -> enable_logging(debug, redirect_stderr=False)
  -> 初始化成功后再 redirect_stderr_to_logger()
  -> 文件日志 ~/.kimi/logs/kimi.log

Web 启动
  -> web/app.py 顶层直接配置 logger 到 stderr
```

代码锚点：
- 默认禁用：`kimi-cli/src/kimi_cli/__init__.py:6`
- CLI 中启用：`kimi-cli/src/kimi_cli/cli/__init__.py:318`
- CLI 延迟重定向：`kimi-cli/src/kimi_cli/cli/__init__.py:499`
- `enable_logging()`：`kimi-cli/src/kimi_cli/app.py:35`
- Web 日志初始化：`kimi-cli/src/kimi_cli/web/app.py:37`

---

## 2. CLI 路径：为什么“先启用文件日志，再接管 stderr”

```text
CLI callback
  -> logger.remove()
  -> logger.enable("kimi_cli")
  -> logger.add(~/.kimi/logs/kimi.log, rotation=06:00, retention=10 days)
  -> (可选) 运行时接管 stderr(fd=2)
```

设计意图：
- 启动期错误优先可见（不要被重定向吞掉）。
- 运行期噪声统一进日志文件，便于排障。

工程 trade-off：
- 优点：CLI 用户看到的启动报错更清晰。
- 代价：日志管线初始化分两阶段，链路理解稍复杂。

代码锚点：
- 文件日志参数：`kimi-cli/src/kimi_cli/app.py:43`
- 轮转与保留：`kimi-cli/src/kimi_cli/app.py:47`
- 延迟重定向的注释：`kimi-cli/src/kimi_cli/app.py:36`

---

## 3. stderr 重定向子流程

```text
redirect_stderr_to_logger()
  -> StderrRedirector.install()
     -> os.pipe()
     -> os.dup2(write_fd, 2)
     -> 后台线程 drain read_fd
     -> 每行 logger.log(level, line)
```

代码锚点：
- 重定向入口：`kimi-cli/src/kimi_cli/utils/logging.py:101`
- `os.pipe()`：`kimi-cli/src/kimi_cli/utils/logging.py:38`
- `os.dup2(..., 2)`：`kimi-cli/src/kimi_cli/utils/logging.py:39`
- 后台线程启动：`kimi-cli/src/kimi_cli/utils/logging.py:42`
- 逐行写 loguru：`kimi-cli/src/kimi_cli/utils/logging.py:88`

---

## 4. Web 路径：为什么直接打到 stderr

```text
web/app.py import
  -> logger.remove()
  -> logger.enable("kimi_cli")
  -> logger.add(sys.stderr, level=LOG_LEVEL)
```

设计意图：
- Web 部署通常由进程管理器/容器接管 stderr，方便集中采集。

工程 trade-off：
- 优点：云原生部署接入简单。
- 代价：默认不落地本地文件，离线排查需依赖外部日志系统。

代码锚点：
- Web logger 配置：`kimi-cli/src/kimi_cli/web/app.py:38`
- 读取 `LOG_LEVEL`：`kimi-cli/src/kimi_cli/web/app.py:37`

---

## 5. 关键结论

- 策略是“库默认静默、入口显式启用”。
- CLI 和 Web 采用不同落地策略：CLI 文件日志，Web stderr。
- stderr 重定向是运行期能力，不应在最早初始化阶段强行启用。
