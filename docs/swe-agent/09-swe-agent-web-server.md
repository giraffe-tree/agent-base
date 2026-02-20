# Web Server（swe-agent）

本文基于 `sweagent/inspector/` 源码，解释 swe-agent 的只读 trajectory 可视化服务器（inspector）如何设计和实现。该服务器用于在训练和推理过程中实时查看 agent 的执行轨迹。

---

## 1. 先看全局（流程图）

### 1.1 服务器生命周期流程图

```text
┌─────────────────────────────────────────────────────────────────┐
│  START: 用户启动 swe-agent                                       │
│  ┌─────────────────┐                                            │
│  │ --serve         │ ◄──── 命令行参数启用 inspector             │
│  └────────┬────────┘                                            │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  配置阶段                                                        │
│  ┌────────────────────────────────────────┐                     │
│  │ MainConfig                             │                     │
│  │  ├── serve: ServeConfig                │ ──► 主机/端口配置   │
│  │  │       ├── host: str = "0.0.0.0"     │                     │
│  │  │       └── port: int = 8000          │                     │
│  │  └── output_dir: Path                  │ ──► 轨迹存储目录    │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  启动 HTTP 服务器                                                │
│  ┌────────────────────────────────────────┐                     │
│  │ start_server()                         │                     │
│  │  ├── socketserver.TCPServer            │ ──► TCP 服务器     │
│  │  │   └── SimpleHTTPRequestHandler      │ ──► 请求处理器     │
│  │  └── serve_forever()                   │ ──► 阻塞运行       │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  请求处理（单线程）                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ do_GET() / do_POST()                   │                     │
│  │  ├── /directory_info                   │ ──► 目录结构       │
│  │  ├── /files                            │ ──► 文件内容       │
│  │  ├── /trajectory/<path>                │ ──► 轨迹数据       │
│  │  └── /check_update                     │ ──► 轮询更新       │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 模块/配置  ──► 流程  ──► 数据流向
```

### 1.2 架构组件关系图

```text
┌────────────────────────────────────────────────────────────────────┐
│                        swe-agent Training/Inference                 │
│                              │                                      │
│                              ▼                                      │
│                    ┌─────────────────┐                              │
│                    │ 输出 trajectory │                              │
│                    │ (JSON 格式)      │                              │
│                    └────────┬────────┘                              │
│                             │                                       │
│                             ▼                                       │
│              ┌──────────────────────────────┐                       │
│              │      output_dir/             │                       │
│              │  ├── run_001/traj.jsonl      │                       │
│              │  ├── run_002/traj.jsonl      │                       │
│              │  └── ...                     │                       │
│              └──────────────┬───────────────┘                       │
│                             │                                       │
└─────────────────────────────┼───────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                      Inspector HTTP Server                          │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  socketserver.TCPServer + CustomHTTPRequestHandler         │    │
│  │                                                            │    │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │    │
│  │  │ /directory_ │  │ /files      │  │ /trajectory/<path>  │ │    │
│  │  │    info     │  │             │  │                     │ │    │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │    │
│  │         │                │                    │            │    │
│  │         ▼                ▼                    ▼            │    │
│  │    scan_dirs()      read_file()          parse_traj()     │    │
│  │         │                │                    │            │    │
│  │         ▼                ▼                    ▼            │    │
│  │    JSON 响应        文件内容              trajectory       │    │
│  │    (目录列表)       (raw/plain)           数据             │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────┬──────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│                          浏览器前端                                 │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  index.html + fileViewer.js + style.css                    │    │
│  │                                                            │    │
│  │  ┌─────────────────┐      ┌─────────────────────────────┐  │    │
│  │  │ 轮询机制        │◄────►│ setInterval(check_update)   │  │    │
│  │  │ (5秒间隔)       │      │ 检测 trajectory 变化         │  │    │
│  │  └────────┬────────┘      └─────────────────────────────┘  │    │
│  │           │                                                │    │
│  │           ▼                                                │    │
│  │  ┌─────────────────────────────────────────────────────┐   │    │
│  │  │  UI 渲染:                                           │   │    │
│  │  │  ├── 左侧: 目录树 (directory_info)                  │   │    │
│  │  │  ├── 中部: 轨迹查看器 (trajectory JSON 格式化)      │   │    │
│  │  │  └── 右侧: 文件查看器 (files 内容展示)              │   │    │
│  │  └─────────────────────────────────────────────────────┘   │    │
│  └────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘

图例: ┌─┐ 组件  ──► 数据流  ◄──► 双向通信
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `2.1`（知道这是一个只读的 trajectory 可视化服务器）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道 API 端点和前端轮询机制）。
- **10 分钟版**：通读 `3~7`（能定位服务器启动和文件访问问题）。

### 2.1 一句话定义

swe-agent 的 Web Server 是一个**基于 Python 标准库的简单 HTTP 服务器**，用于**只读展示**训练/推理过程中生成的 trajectory 文件，采用**轮询机制**实现准实时更新。

---

## 3. 核心组件

### 3.1 http.server 基础架构

swe-agent 使用 Python 标准库实现，无外部 HTTP 框架依赖：

```python
# sweagent/inspector/server.py
import socketserver
from http.server import SimpleHTTPRequestHandler

class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    """自定义请求处理器，添加 CORS 和 JSON API 支持"""

    def end_headers(self):
        # 添加 CORS 头，允许跨域访问
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_GET(self):
        # 路由分发到对应的 API 端点
        if self.path == '/directory_info':
            self.handle_directory_info()
        elif self.path.startswith('/files'):
            self.handle_files()
        elif self.path.startswith('/trajectory'):
            self.handle_trajectory()
        elif self.path == '/check_update':
            self.handle_check_update()
        else:
            # 静态文件服务（前端资源）
            super().do_GET()
```

**关键设计决策**：
- 使用 `socketserver.TCPServer`：简单、无依赖、易于嵌入
- 单线程模型：足够应对只读场景，无需并发处理
- 内置 CORS 支持：允许前端独立开发部署

### 3.2 Handler 类详解

`CustomHTTPRequestHandler` 处理四类请求：

| 方法 | 路径 | 功能 |
|------|------|------|
| `do_GET` | `/directory_info` | 扫描 output_dir，返回目录结构 |
| `do_GET` | `/files?path=xxx` | 读取指定文件内容 |
| `do_GET` | `/trajectory/<path>` | 解析并返回 trajectory JSON |
| `do_GET` | `/check_update` | 返回最后修改时间，用于轮询 |
| `do_POST` | `/feedback` | 接收用户反馈（可选） |

---

## 4. API 端点详解

### 4.1 `/directory_info` - 目录结构

**功能**：扫描配置的 `output_dir`，返回所有 trajectory 文件的层级结构。

**响应格式**：
```json
{
  "directories": [
    {
      "name": "run_001",
      "path": "run_001",
      "trajectories": [
        {
          "name": "traj_001.jsonl",
          "path": "run_001/traj_001.jsonl",
          "size": 12345,
          "modified": 1699123456.789
        }
      ]
    }
  ]
}
```

**实现代码**：
```python
def handle_directory_info(self):
    """扫描 output_dir 返回目录结构"""
    result = {"directories": []}
    output_dir = self.server.output_dir

    for entry in os.scandir(output_dir):
        if entry.is_dir():
            dir_info = {"name": entry.name, "path": entry.name, "trajectories": []}
            for subentry in os.scandir(entry.path):
                if subentry.name.endswith('.jsonl'):
                    stat = subentry.stat()
                    dir_info["trajectories"].append({
                        "name": subentry.name,
                        "path": f"{entry.name}/{subentry.name}",
                        "size": stat.st_size,
                        "modified": stat.st_mtime
                    })
            result["directories"].append(dir_info)

    self.send_json_response(result)
```

### 4.2 `/files` - 文件内容

**功能**：读取并返回指定文件的原始内容。

**请求参数**：
- `path`: 相对于 output_dir 的文件路径
- `format`: 可选，`raw` 或 `plain`

**响应**：文件内容（文本）或错误信息

```python
def handle_files(self):
    """读取文件内容"""
    query = parse_qs(urlparse(self.path).query)
    file_path = query.get('path', [''])[0]

    # 安全检查：确保路径在 output_dir 内
    full_path = os.path.abspath(os.path.join(self.server.output_dir, file_path))
    if not full_path.startswith(os.path.abspath(self.server.output_dir)):
        self.send_error(403, "Access denied")
        return

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        self.send_text_response(content)
    except FileNotFoundError:
        self.send_error(404, "File not found")
```

### 4.3 `/trajectory/<path>` - 轨迹数据

**功能**：解析 trajectory JSONL 文件，返回结构化数据。

**响应格式**：
```json
{
  "trajectory": [
    {
      "step": 0,
      "action": "think",
      "content": "...",
      "timestamp": "2024-01-01T12:00:00"
    },
    {
      "step": 1,
      "action": "cmd",
      "command": "ls -la",
      "output": "...",
      "exit_code": 0
    }
  ],
  "metadata": {
    "total_steps": 10,
    "resolved": true
  }
}
```

### 4.4 `/check_update` - 更新检查

**功能**：返回目录最后修改时间戳，前端用于轮询检测更新。

**响应**：
```json
{
  "last_modified": 1699123456.789,
  "has_update": true
}
```

---

## 5. 前端通信

### 5.1 轮询机制

由于服务器使用简单的 HTTP 协议，前端采用**轮询**实现准实时更新：

```javascript
// sweagent/inspector/fileViewer.js (简化)
class TrajectoryViewer {
    constructor() {
        this.lastModified = 0;
        this.pollInterval = 5000; // 5 秒轮询间隔
    }

    startPolling() {
        setInterval(() => this.checkUpdate(), this.pollInterval);
    }

    async checkUpdate() {
        const response = await fetch('/check_update');
        const data = await response.json();

        if (data.last_modified > this.lastModified) {
            this.lastModified = data.last_modified;
            await this.reloadTrajectory(); // 有更新，重新加载
        }
    }
}
```

**轮询间隔**：默认 5 秒，平衡实时性与服务器负载。

### 5.2 前端架构

前端由三部分组成：

| 文件 | 功能 |
|------|------|
| `index.html` | 页面结构：三栏布局（目录树、轨迹、文件） |
| `fileViewer.js` | 核心逻辑：API 调用、轮询、渲染 |
| `style.css` | 样式：代码高亮、轨迹步骤样式 |

**关键交互**：
1. 左侧目录树点击 → 加载 trajectory
2. 轨迹步骤点击 → 右侧显示相关文件
3. 自动轮询 → 检测并提示新数据

---

## 6. 排障速查

| 问题 | 检查点 | 解决方案 |
|------|--------|----------|
| 端口被占用 | `ServeConfig.port` | 修改配置或关闭占用程序 |
| 文件访问 403 | 路径安全检查 | 确保文件在 `output_dir` 内 |
| 前端无法连接 | CORS 头 | 检查 `end_headers()` 中的 CORS 设置 |
| 轮询不生效 | `/check_update` 响应 | 确认 `last_modified` 时间戳正确 |
| 中文乱码 | 文件编码 | 确保使用 UTF-8 编码读取 |

---

## 7. 架构特点总结

- **零依赖**：仅使用 Python 标准库，无安装负担
- **只读安全**：API 设计为只读，不影响训练进程
- **简单轮询**：无需 WebSocket，降低复杂度
- **嵌入友好**：易于集成到 swe-agent 主程序中
- **单线程够用**：只读场景下无需并发处理
