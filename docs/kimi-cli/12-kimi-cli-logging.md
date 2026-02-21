# Kimi CLI 日志记录机制

## 引子：当用户把你的 CLI 当作 Python 库 import 时...

想象一下这个场景：你开发了一个功能强大的 AI CLI 工具，社区开发者很喜欢，开始在他们的项目中这样使用：

```python
from my_awesome_cli import Agent

agent = Agent()
result = agent.run("帮我分析这段代码")
```

然后他们投诉说：**"你的工具在我运行时不停地输出日志，干扰了我的程序！"**

这是 CLI 工具变成库时常见的问题。你需要：
- 作为 CLI 运行时：显示完整的日志输出
- 作为库被 import 时：保持静默，不污染用户的输出

Kimi CLI 的解决方案是**库友好设计**：默认禁用日志，只在 CLI 入口点显式启用。

```python
# kimi_cli/__init__.py
logger.disable("kimi_cli")  # 默认禁用

# kimi_cli/cli.py (入口点)
logger.enable("kimi_cli")   # CLI 运行时启用
```

本章深入解析 Kimi CLI 如何使用 `loguru` 实现优雅的库友好日志系统。

---

## 结论先行

Kimi CLI 使用 `loguru` 作为核心日志库，采用"库友好"设计（默认禁用），并通过 `StderrRedirector` 实现子进程 stderr 输出捕获，提供简洁强大的结构化日志能力。

---

## 技术类比：loguru vs 标准库 logging

选择 `loguru` 而不是标准库 `logging`，就像选择 `requests` 而不是 `urllib`：

| 特性 | `logging` (stdlib) | `loguru` | 类比 |
|------|-------------------|----------|------|
| 配置复杂度 | 繁琐（Handler/Formatter） | 开箱即用 | `urllib` vs `requests` |
| 结构化日志 | 需手动实现 | 原生支持 `{var}` | 原始字符串 vs f-string |
| 异常追踪 | 需配置 | 自动捕获完整堆栈 | 手动 vs 自动 |
| 彩色输出 | 需第三方库 | 内置支持 | 黑白 vs 彩色 |
| 文件轮转 | 需 TimedRotatingFileHandler | 简单配置 | 手动 vs 一键 |
| 类型安全 | 弱 | 更好的 IDE 支持 | 动态 vs 静态 |

### 基础用法对比

```python
# 标准库 logging
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("User %s logged in from %s", user_id, ip_address)

# loguru
from loguru import logger
logger.info("User {user_id} logged in from {ip}", user_id=user_id, ip=ip_address)
```

### Unix 哲学：一切皆文件

Kimi CLI 的 `StderrRedirector` 体现了 Unix 哲学：
- 文件描述符（fd）是统一的 I/O 抽象
- 管道（pipe）用于进程间通信
- 线程用于异步处理

```python
# Unix 哲学示例
def install(self):
    # 创建管道（进程间通信）
    read_fd, write_fd = os.pipe()

    # 重定向 stderr 到管道写端
    os.dup2(write_fd, 2)

    # 线程读取管道，写入 logger
    threading.Thread(target=self._drain).start()
```

---

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    应用入口 (cli.py)                         │
│              logger.enable("kimi_cli")                       │
│                    显式启用日志                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Loguru Logger (库友好设计)                       │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  默认禁用: logger.disable("kimi_cli")                   │  │
│  │  避免作为库使用时污染输出                                │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
        ▼                                           ▼
┌───────────────────┐                   ┌─────────────────────┐
│    常规日志        │                   │  StderrRedirector   │
│                   │                   │    子进程输出捕获    │
│ • {var} 插值      │                   │                     │
│ • 结构化日志      │                   │  ┌───────────────┐  │
│ • 彩色输出        │                   │  │  os.pipe()    │  │
│ • 自动轮转        │                   │  │  os.dup2()    │  │
│                   │                   │  │  线程读取     │  │
│                   │                   │  │  → loguru     │  │
│                   │                   │  └───────────────┘  │
└───────────────────┘                   └─────────────────────┘
```

---

## loguru 简介与标准库对比

### 为什么选择 loguru？

| 特性 | logging (stdlib) | loguru |
|------|------------------|--------|
| 配置复杂度 | 繁琐（Handler/Formatter） | 开箱即用 |
| 结构化日志 | 需手动实现 | 原生支持 `{var}` |
| 异常追踪 | 需配置 | 自动捕获完整堆栈 |
| 彩色输出 | 需第三方库 | 内置支持 |
| 文件轮转 | 需 TimedRotatingFileHandler | 简单配置 |
| 类型安全 | 弱 | 更好的 IDE 支持 |

### 基础用法对比

```python
# 标准库 logging
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info("User %s logged in from %s", user_id, ip_address)

# loguru
from loguru import logger
logger.info("User {user_id} logged in from {ip}", user_id=user_id, ip=ip_address)
```

---

## 库友好设计

**✅ Verified**: `kimi-cli/src/kimi_cli/__init__.py`

```python
from loguru import logger

# Disable logging by default for library usage.
# Application entry points (e.g., kimi_cli.cli) should call logger.enable("kimi_cli")
# to enable logging.
logger.disable("kimi_cli")
```

### 启用时机

```python
# kimi_cli/cli.py 或主入口
from loguru import logger

def main():
    # 应用启动时显式启用
    logger.enable("kimi_cli")

    # 可选：配置输出文件
    logger.add("kimi_{time}.log", rotation="10 MB", retention="1 week")

    logger.info("Kimi CLI started")
```

### 设计优势

- **作为 CLI 工具**: 启用日志，提供完整输出
- **作为库依赖**: 禁用日志，不污染调用方输出
- **灵活控制**: 可按模块粒度启用/禁用

---

## StderrRedirector 详解

**✅ Verified**: `kimi-cli/src/kimi_cli/utils/logging.py`

### 核心作用

捕获子进程（如 shell 命令执行）的 stderr 输出，重定向到 loguru logger，实现统一日志管理。

### 实际场景：git clone 进度条

当 Agent 执行 `git clone` 时，进度信息输出到 stderr。如果不捕获，这些信息会：
1. 直接显示在终端，干扰 Agent 的交互式输出
2. 或者丢失，无法追踪执行过程

`StderrRedirector` 的解决方案：
```python
# 重定向 stderr 到 logger
redirect_stderr_to_logger(level="DEBUG")

# 执行 git clone（进度条被捕获到日志）
subprocess.run(["git", "clone", url])

# 恢复原始 stderr
restore_stderr()
```

### 完整实现

```python
import codecs
import contextlib
import locale
import os
import sys
import threading
from collections.abc import Iterator
from typing import IO

from loguru import logger


class StderrRedirector:
    def __init__(self, level: str = "ERROR") -> None:
        self._level = level
        self._encoding: str | None = None
        self._installed = False
        self._lock = threading.Lock()
        self._original_fd: int | None = None
        self._read_fd: int | None = None
        self._thread: threading.Thread | None = None

    def install(self) -> None:
        """安装重定向，将 stderr 重定向到 logger"""
        with self._lock:
            if self._installed:
                return

            # 1. 刷新当前 stderr 缓冲区
            with contextlib.suppress(Exception):
                sys.stderr.flush()

            # 2. 复制原始 stderr 文件描述符
            if self._original_fd is None:
                with contextlib.suppress(OSError):
                    self._original_fd = os.dup(2)

            # 3. 获取编码
            if self._encoding is None:
                self._encoding = (
                    sys.stderr.encoding or locale.getpreferredencoding(False) or "utf-8"
                )

            # 4. 创建管道并替换 stderr
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, 2)    # 将 fd 2 (stderr) 指向管道写端
            os.close(write_fd)
            self._read_fd = read_fd

            # 5. 启动读取线程
            self._thread = threading.Thread(
                target=self._drain, name="kimi-stderr-redirect", daemon=True
            )
            self._thread.start()
            self._installed = True

    def uninstall(self) -> None:
        """恢复原始 stderr"""
        with self._lock:
            if not self._installed:
                return
            if self._original_fd is not None:
                os.dup2(self._original_fd, 2)  # 恢复原始 fd
            self._installed = False

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _drain(self) -> None:
        """后台线程：从管道读取并记录到 logger"""
        buffer = ""
        read_fd = self._read_fd
        if read_fd is None:
            return

        encoding = self._encoding or "utf-8"
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")

        try:
            while True:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    break

                buffer += decoder.decode(chunk)

                # 按行分割并记录
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self._log_line(line)
        except Exception:
            logger.exception("Failed to read redirected stderr")
        finally:
            # 处理剩余内容
            buffer += decoder.decode(b"", final=True)
            if buffer:
                self._log_line(buffer)
            with contextlib.suppress(OSError):
                os.close(read_fd)

    def _log_line(self, line: str) -> None:
        """记录单行日志"""
        text = line.rstrip("\r")
        if not text:
            return
        # depth=2 确保日志显示调用者位置而非 _log_line
        logger.opt(depth=2).log(self._level, text)

    def open_original_stderr_handle(self) -> IO[bytes] | None:
        """获取原始 stderr 的句柄（用于子进程继承）"""
        if self._original_fd is None:
            return None
        dup_fd = os.dup(self._original_fd)
        os.set_inheritable(dup_fd, True)
        return os.fdopen(dup_fd, "wb", closefd=True)
```

### 使用场景

```python
from kimi_cli.utils.logging import redirect_stderr_to_logger, restore_stderr

# 启动重定向
redirect_stderr_to_logger(level="ERROR")

# 执行子进程（其 stderr 会被捕获到 logger）
import subprocess
subprocess.run(["some-command", "--arg"], stderr=subprocess.STDOUT)

# 恢复原始 stderr
restore_stderr()
```

---

## 结构化日志与 {var} 插值

### 基础用法

```python
from loguru import logger

# 简单消息
logger.info("Application started")

# {var} 插值（自动类型转换）
user_id = 12345
logger.info("User {user_id} logged in", user_id=user_id)
# 输出: User 12345 logged in

# 结构化数据
logger.info("API request", extra={"method": "POST", "endpoint": "/chat", "duration_ms": 150})

# 异常自动捕获
@logger.catch
def risky_operation():
    1 / 0
```

### 高级特性

```python
# 绑定上下文
with logger.contextualize(request_id="abc-123"):
    logger.info("Processing request")  # 自动包含 request_id

# 日志级别方法
logger.trace("Detailed trace info")
logger.debug("Debug information")
logger.info("General info")
logger.success("Success message")  # loguru 特有
logger.warning("Warning message")
logger.error("Error occurred")
logger.critical("Critical failure")
```

---

## 快速上手：Kimi CLI 日志实战

### 1. 粒度控制：enable/disable

```python
from loguru import logger
import kimi_cli  # 自动调用 logger.disable("kimi_cli")

# 启用所有日志
logger.enable("kimi_cli")

# 只启用特定模块
logger.enable("kimi_cli.agent")
logger.disable("kimi_cli.utils")  # 但禁用 utils

# 使用
from kimi_cli.agent import Agent
agent = Agent()  # 只有 agent 模块的日志会输出
```

### 2. 配置文件日志轮转

```python
from loguru import logger

# 启用 Kimi CLI 日志
logger.enable("kimi_cli")

# 添加文件输出，自动轮转
logger.add(
    "kimi_{time:YYYY-MM-DD}.log",
    rotation="10 MB",      # 文件达到 10MB 时轮转
    retention="1 week",    # 保留一周
    compression="zip",     # 压缩旧日志
    level="DEBUG"
)
```

### 3. 捕获子进程输出

```python
from kimi_cli.utils.logging import redirect_stderr_to_logger, restore_stderr
import subprocess

# 启用重定向
redirect_stderr_to_logger(level="DEBUG")

try:
    # git clone 的进度条会被捕获
    subprocess.run(["git", "clone", "https://github.com/example/repo.git"])

    # npm install 的输出也会被捕获
    subprocess.run(["npm", "install"], cwd="./project")
finally:
    # 确保恢复
    restore_stderr()
```

### 4. 结构化日志与 JSON 输出

```python
from loguru import logger
import sys

# 添加 JSON 格式的 sink
logger.add(
    sys.stdout,
    serialize=True,  # 输出 JSON 格式
    format="{message}"
)

# 记录结构化数据
logger.info("API request", extra={
    "method": "POST",
    "endpoint": "/v1/chat",
    "duration_ms": 150,
    "status_code": 200
})

# 输出:
# {"text": "API request", "record": {"extra": {"method": "POST", ...}}}
```

### 5. 作为库使用时的最佳实践

```python
# 你的库代码 (my_library.py)
from loguru import logger

# 库不应该配置日志，只记录
logger.disable("my_library")  # 默认禁用

def do_something():
    logger.debug("Doing something")  # 默认不输出
    return result

# 用户的应用代码 (user_app.py)
from loguru import logger
from my_library import do_something

# 用户控制是否启用库日志
logger.enable("my_library")
logger.add("app.log")

do_something()  # 现在会输出日志
```

### 6. 常见问题排查

**Q: 为什么 import 后没有日志输出？**
```python
# 这是设计如此！Kimi CLI 默认禁用日志
import kimi_cli

# 需要显式启用
from loguru import logger
logger.enable("kimi_cli")
```

**Q: 如何查看所有可用模块？**
```bash
# loguru 的 enable/disable 基于模块名通配符
# 启用所有以 kimi_ 开头的模块
logger.enable("kimi_")

# 或者启用所有
logger.enable("")
```

**Q: StderrRedirector 不工作？**
```python
# 确保在子进程启动前调用 install()
redirect_stderr_to_logger()

# 然后启动子进程
# 注意：某些子进程可能直接写入 /dev/tty，绕过重定向
```

**Q: 日志文件占用空间太大？**
```python
# 配置保留策略
logger.add(
    "kimi.log",
    rotation="100 MB",
    retention=10,  # 只保留 10 个文件
    compression="gz"  # 压缩旧文件
)
```

---

## 证据索引

| 组件 | 文件路径 | 行号 | 关键职责 |
|------|----------|------|----------|
| 日志禁用 | `kimi-cli/src/kimi_cli/__init__.py` | 1-6 | 库友好设计，默认禁用 |
| StderrRedirector | `kimi-cli/src/kimi_cli/utils/logging.py` | 15-125 | stderr 重定向实现 |
| 重定向启用 | `kimi-cli/src/kimi_cli/utils/logging.py` | 101-105 | `redirect_stderr_to_logger()` |
| 重定向恢复 | `kimi-cli/src/kimi_cli/utils/logging.py` | 108-110 | `restore_stderr()` |

---

## 边界与不确定性

- **⚠️ Inferred**: 具体的日志文件轮转配置（rotation/retention）在代码中未找到，可能由调用方配置
- **⚠️ Inferred**: `StderrRedirector` 的使用位置（具体在哪些子进程调用中使用）未完全确认
- **❓ Pending**: 是否存在其他日志配置（如 sinks、filters）需进一步确认
- **✅ Verified**: `logger.disable("kimi_cli")` 和 `StderrRedirector` 完整实现已确认

---

## 设计亮点

1. **库友好**: 默认禁用日志，避免作为依赖时污染输出
2. **子进程捕获**: `StderrRedirector` 巧妙利用 `os.pipe` 和线程实现 stderr 捕获
3. **简洁 API**: loguru 的 `{var}` 插值比标准库更直观
4. **线程安全**: 使用锁保护安装/卸载过程，支持并发场景
5. **编码处理**: 使用 `codecs.getincrementaldecoder` 处理可能的编码问题
