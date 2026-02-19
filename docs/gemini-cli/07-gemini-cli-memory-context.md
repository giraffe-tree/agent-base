# Memory Context 管理（gemini-cli）

本文基于 `./gemini-cli` 源码，解释 Gemini CLI 如何实现分层内存（Hierarchical Memory）和上下文管理，包括 GEMINI.md 文件发现、三层内存系统和上下文压缩。

---

## 1. 先看全局（流程图）

### 1.1 HierarchicalMemory 三层架构

```text
┌─────────────────────────────────────────────────────────────────┐
│  GEMINI.md 文件发现                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ memoryDiscovery.ts                     │                     │
│  │  ├── getGlobalMemoryPaths()            │                     │
│  │  │   └── ~/.gemini/GEMINI.md          │                     │
│  │  ├── getExtensionMemoryPaths()         │                     │
│  │  │   └── Extension.contextFiles        │                     │
│  │  └── getProjectMemoryPaths()           │                     │
│  │      ├── 向上遍历到项目根目录          │                     │
│  │      └── BFS 向下搜索子目录            │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  三层内存合并                                                     │
│  ┌────────────────────────────────────────┐                     │
│  │ categorizeAndConcatenate()             │                     │
│  │  ├── Global: ~/.gemini/GEMINI.md       │                     │
│  │  ├── Extension: 扩展提供的内存         │                     │
│  │  └── Project: 工作目录的 GEMINI.md     │                     │
│  │                                        │                     │
│  │  输出: HierarchicalMemory              │                     │
│  │  { global, extension, project }        │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Prompt 构建                                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ flattenMemory()                        │                     │
│  │  └── 合并为单个系统提示词              │                     │
│  │                                        │                     │
│  │ --- Global ---                         │                     │
│  │ [global memory content]                │                     │
│  │ --- Project ---                        │                     │
│  │ [project memory content]               │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 项目内存发现流程

```text
┌─────────────────────────────────────────────────────────────────┐
│  当前工作目录 (CWD)                                               │
│  ┌────────────────────────────────────────┐                     │
│  │ /home/user/project/src/components      │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  向上遍历 (Upward Traversal)                                      │
│  ┌────────────────────────────────────────┐                     │
│  │ 1. /home/user/project/src/             │                     │
│  │    └── GEMINI.md ? No                  │                     │
│  │ 2. /home/user/project/                 │                     │
│  │    └── GEMINI.md ? Yes ✓               │                     │
│  │       (找到项目根目录 - .git 所在)      │                     │
│  │ 3. 停止遍历                            │                     │
│  └────────┬───────────────────────────────┘                     │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│  向下搜索 (BFS Downward Search)                                   │
│  ┌────────────────────────────────────────┐                     │
│  │ 从 CWD 开始 BFS 搜索子目录             │                     │
│  │ 查找所有 GEMINI.md 变体:               │                     │
│  │ - GEMINI.md                            │                     │
│  │ - .gemini.md                           │                     │
│  │ - GEMINI.yml / GEMINI.yaml             │                     │
│  │                                        │                     │
│  │ 限制: maxDirs = 200 (默认)             │                     │
│  └────────────────────────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 阅读路径（30 秒 / 3 分钟 / 10 分钟）

- **30 秒版**：只看 `1.1` + `3.1`（知道三层内存结构和 GEMINI.md 发现机制）。
- **3 分钟版**：看 `1.1` + `1.2` + `4` + `5`（知道内存发现流程、JIT 加载和压缩）。
- **10 分钟版**：通读全文（能配置和调试分层内存系统）。

### 2.1 一句话定义

Gemini CLI 的 Memory Context 采用"**三层分层 + JIT 动态加载**"的设计：通过 Global / Extension / Project 三层 GEMINI.md 文件构建上下文，支持向上遍历和向下搜索的内存发现机制，并在需要时动态加载子目录内存。

---

## 3. 核心组件详解

### 3.1 HierarchicalMemory 接口

**文件**: `packages/core/src/config/memory.ts`

```typescript
export interface HierarchicalMemory {
  global?: string;      // ~/.gemini/GEMINI.md
  extension?: string;   // 扩展提供的内存
  project?: string;     // 工作目录的 GEMINI.md
}

/**
 * 将分层内存展平为单个字符串
 */
export function flattenMemory(memory?: string | HierarchicalMemory): string {
  if (!memory) return '';
  if (typeof memory === 'string') return memory;

  const sections: Array<{ name: string; content: string }> = [];
  if (memory.global?.trim()) {
    sections.push({ name: 'Global', content: memory.global.trim() });
  }
  if (memory.extension?.trim()) {
    sections.push({ name: 'Extension', content: memory.extension.trim() });
  }
  if (memory.project?.trim()) {
    sections.push({ name: 'Project', content: memory.project.trim() });
  }

  return sections
    .map((s) => `--- ${s.name} ---\n${s.content}`)
    .join('\n\n');
}
```

### 3.2 内存发现服务

**文件**: `packages/core/src/utils/memoryDiscovery.ts`

```typescript
export interface LoadServerHierarchicalMemoryResponse {
  memoryContent: HierarchicalMemory;
  fileCount: number;
  filePaths: string[];
}

export async function loadServerHierarchicalMemory(
  currentWorkingDirectory: string,
  includeDirectoriesToReadGemini: readonly string[],
  debugMode: boolean,
  fileService: FileDiscoveryService,
  extensionLoader: ExtensionLoader,
  folderTrust: boolean,
  importFormat: 'flat' | 'tree' = 'tree',
  fileFilteringOptions?: FileFilteringOptions,
  maxDirs: number = 200,
): Promise<LoadServerHierarchicalMemoryResponse> {
  // 1. SCATTER: 收集所有路径
  const [discoveryResult, extensionPaths] = await Promise.all([
    getGeminiMdFilePathsInternal(...),
    Promise.resolve(getExtensionMemoryPaths(extensionLoader)),
  ]);

  // 2. GATHER: 并行读取所有文件
  const allContents = await readGeminiMdFiles(allFilePaths, debugMode, importFormat);
  const contentsMap = new Map(allContents.map((c) => [c.filePath, c]));

  // 3. CATEGORIZE: 分类为 Global / Project / Extension
  const hierarchicalMemory = categorizeAndConcatenate(
    { global, extension, project },
    contentsMap,
    currentWorkingDirectory,
  );

  return { memoryContent: hierarchicalMemory, fileCount, filePaths };
}
```

### 3.3 三层内存优先级

内存合并时的优先级（低 → 高）：

```
┌─────────────────────────────────────────┐
│  Priority 1: Global Memory              │
│  (~/.gemini/GEMINI.md)                  │
│  全局适用的指令和上下文                  │
├─────────────────────────────────────────┤
│  Priority 2: Extension Memory           │
│  (Extension.contextFiles)               │
│  扩展特定的上下文                        │
├─────────────────────────────────────────┤
│  Priority 3: Project Memory             │
│  (./GEMINI.md)                          │
│  项目特定的指令（最高优先级）            │
└─────────────────────────────────────────┘
```

---

## 4. JIT 子目录内存加载

### 4.1 延迟加载机制

**文件**: `packages/core/src/utils/memoryDiscovery.ts:606-676`

```typescript
export async function loadJitSubdirectoryMemory(
  targetPath: string,
  trustedRoots: string[],
  alreadyLoadedPaths: Set<string>,
  debugMode: boolean = false,
): Promise<MemoryLoadResult> {
  // 1. 找到包含 targetPath 的最深信任根目录
  let bestRoot: string | null = null;
  for (const root of trustedRoots) {
    if (resolvedTarget.startsWith(resolvedRootWithTrailing)) {
      if (!bestRoot || resolvedRoot.length > bestRoot.length) {
        bestRoot = resolvedRoot;
      }
    }
  }

  if (!bestRoot) {
    return { files: [] };  // 不在信任根目录中
  }

  // 2. 从 targetPath 向上遍历到 bestRoot
  const potentialPaths = await findUpwardGeminiFiles(
    resolvedTarget,
    bestRoot,
    debugMode,
  );

  // 3. 过滤已加载的路径
  const newPaths = potentialPaths.filter((p) => !alreadyLoadedPaths.has(p));

  // 4. 读取新发现的内存文件
  const contents = await readGeminiMdFiles(newPaths, debugMode, 'tree');
  return { files: contents.filter(...) };
}
```

### 4.2 使用场景

当用户深入项目的子目录时，动态加载该子目录相关的上下文：

```typescript
// 用户导航到子目录
> cd src/components/Button

// 自动加载该目录及其上级目录的 GEMINI.md
// - src/components/Button/GEMINI.md
// - src/components/GEMINI.md
// - src/GEMINI.md
```

---

## 5. 上下文压缩

### 5.1 Token 阈值触发

当上下文 Token 超过模型限制时，Gemini CLI 会触发压缩：

```typescript
// 检查是否超过 50% 上下文窗口
const shouldCompress = (
  inputTokens > contextWindowSize * 0.5 ||
  estimatedOutputTokens > contextWindowSize * 0.25
);
```

### 5.2 两阶段验证压缩

**阶段 1: 生成摘要**
- 使用轻量级模型生成历史摘要
- 保留关键决策点和代码变更

**阶段 2: 验证摘要**
- 使用主模型验证摘要完整性
- 确保没有丢失关键信息

```typescript
async function compressContext(history: History): Promise<History> {
  // 阶段 1: 生成摘要
  const summary = await generateSummary(history, { model: 'flash' });

  // 阶段 2: 验证摘要
  const validation = await validateSummary(summary, history, { model: 'pro' });

  if (validation.isComplete) {
    return createCompressedHistory(summary);
  } else {
    // 保留更多上下文重试
    return compressContextWithHigherRetention(history);
  }
}
```

---

## 6. Session 管理

### 6.1 ChatRecordingService

**文件**: `packages/core/src/services/chatRecordingService.ts`

```typescript
export class ChatRecordingService {
  private sessionId: string;
  private messages: Message[] = [];
  private metadata: SessionMetadata;

  async saveMessage(message: Message): Promise<void> {
    this.messages.push(message);
    await this.persistToDisk();
  }

  async generateSessionSummary(): Promise<string> {
    // 使用模型生成会话摘要
    const summary = await this.model.generateContent({
      contents: this.messages,
      systemInstruction: 'Summarize this coding session...',
    });
    return summary.text;
  }
}
```

### 6.2 Session 恢复

```typescript
async function restoreSession(sessionId: string): Promise<Session> {
  const recording = await loadChatRecording(sessionId);
  const hierarchicalMemory = await loadServerHierarchicalMemory(
    recording.workingDirectory,
    ...
  );

  return new Session({
    id: sessionId,
    messages: recording.messages,
    memory: hierarchicalMemory,
  });
}
```

---

## 7. GEMINI.md 文件格式

### 7.1 支持的文件名

```typescript
// packages/core/src/tools/memoryTool.ts
export function getAllGeminiMdFilenames(): string[] {
  return [
    'GEMINI.md',
    '.gemini.md',
    'GEMINI.yml',
    'GEMINI.yaml',
  ];
}
```

### 7.2 内容格式示例

```markdown
# GEMINI.md

## 项目概述
这是一个 React + TypeScript 项目，使用 Vite 构建工具。

## 代码规范
- 使用函数组件和 Hooks
- 类型定义放在 types/ 目录
- 测试文件使用 .test.ts 后缀

## 常用命令
- `npm run dev` - 启动开发服务器
- `npm test` - 运行测试

## 重要文件
- src/config/ - 配置文件
- src/utils/ - 工具函数
```

### 7.3 Import 处理

支持在 GEMINI.md 中引用其他文件：

```markdown
<!-- GEMINI.md -->
@import ./shared-guidelines.md
@import ./team-standards.md
```

**处理流程**:
```typescript
export async function processImports(
  content: string,
  basePath: string,
  debugMode: boolean,
  importFormat: 'flat' | 'tree' = 'tree',
): Promise<{ content: string }> {
  // 解析 @import 指令
  // 递归加载引用的文件
  // 组装为树状或扁平结构
}
```

---

## 8. 与 Agent Loop 的集成

```text
┌──────────────────────────────────────────────────────────────────────┐
│                        Agent Loop                                     │
│  ┌─────────────────┐  ┌─────────────────────┐  ┌─────────────────┐  │
│  │ 用户输入        │──▶│ loadHierarchical    │──▶│ 构建 System     │  │
│  │                 │  │ Memory()            │  │ Instruction     │  │
│  └─────────────────┘  └─────────────────────┘  └─────────────────┘  │
│                              │                        │              │
│                              ▼                        ▼              │
│                     ┌─────────────────┐    ┌─────────────────┐      │
│                     │ Global Memory   │    │ flattenMemory() │      │
│                     │ Extension Mem   │───▶│                 │      │
│                     │ Project Memory  │    │ 合并为 Prompt   │      │
│                     └─────────────────┘    └─────────────────┘      │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ 触发 JIT 加载 (当用户导航到子目录时)                         │  │
│  │ loadJitSubdirectoryMemory(targetPath)                       │  │
│  │   └── 动态加载新的 GEMINI.md                                 │  │
│  └─────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 9. 排障速查

| 问题 | 检查点 | 文件 |
|------|-------|------|
| 内存文件未加载 | 检查文件名是否为 GEMINI.md 变体 | `memoryTool.ts` |
| 项目内存不生效 | 确认文件夹信任状态和 .git 根目录检测 | `memoryDiscovery.ts:42` |
| JIT 加载失败 | 检查 trustedRoots 是否包含目标路径 | `memoryDiscovery.ts:630` |
| 内存内容过长 | 检查 Import 循环引用 | `memoryImportProcessor.ts` |
| 压缩频繁触发 | 调整上下文窗口使用阈值 | `config/context.ts` |

---

## 10. 架构特点总结

- **三层分层**: Global / Extension / Project 三级内存，优先级递增
- **双向发现**: 向上遍历到项目根目录 + 向下 BFS 搜索子目录
- **JIT 动态加载**: 根据用户导航动态加载子目录内存
- **多格式支持**: 支持 .md / .yml / .yaml 多种文件格式
- **Import 系统**: 支持文件引用和递归组装
- **两阶段压缩**: 轻量摘要 + 主模型验证的压缩策略
