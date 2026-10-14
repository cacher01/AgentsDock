# AgentsDock

AgentsDock 是一个面向团队工作区的多智能体任务执行与 AI 治理平台。平台接收自然语言任务，通过路由器选择工作流，调用模型与工具，并返回回答、引用、执行事件和文件产物。

系统由 Go Gateway、Python Runtime、Python Worker、PostgreSQL 和任务文件存储组成。Gateway 负责认证与治理接口，Runtime 负责 API 和任务编排入口，Worker 负责异步任务执行。

## 功能

| 模块 | 功能 |
| --- | --- |
| 任务编排 | 根据任务特征选择 direct、research、ReAct、plan-execute、supervisor、DAG 或 swarm 工作流 |
| 模型网关 | 管理 OpenAI-compatible 模型连接、模型变体、调用记录、价格和 fallback |
| 工具治理 | 按工作区启用工具，限制 Agent 可调用工具及调用次数 |
| 文档输入 | 解析 TXT、Markdown、CSV、TSV、JSON、PDF、DOCX 和 XLSX |
| 结果产物 | 生成 Markdown、TXT、JSON、CSV 和 ZIP 文件 |
| 团队协作 | 管理员与成员角色、私有任务、显式分享、评论、提及和站内通知 |
| 运行控制 | 异步调度、租约、取消、Checkpoint、失败恢复和执行回放 |
| 治理数据 | Token、估算成本、软预算、工具调用、任务状态和审计记录 |
| 事件接口 | 通过 SSE 推送任务执行事件和终态结果 |

## 系统架构

```mermaid
flowchart LR
    U[Browser] -->|HTTP / SSE| G[Go Gateway]

    subgraph ControlPlane[接入与治理]
        G --> A[认证与会话]
        G --> M[模型网关]
        G --> T[工具与预算管理]
        G --> L[审计接口]
    end

    G -->|受保护 API 代理| R[Python Runtime]
    R -->|创建任务与写入调度队列| P[(PostgreSQL)]

    subgraph ExecutionPlane[任务执行]
        W[Python Worker] -->|领取租约任务| P
        W --> O[Multi-Agent Orchestrator]
        O --> F[Workflow Runtime]
        F --> X[Tool Executor]
        F -->|内部模型调用| G
    end

    M -->|OpenAI-compatible API| V[Model Provider]
    X -->|受控访问| E[External Tools]
    R --> S[(Input Files / Artifacts)]
    W --> S
    W -->|事件、结果与 Checkpoint| P
    R -->|查询任务与协作数据| P
```

### 组件职责

| 组件 | 实现 | 职责 |
| --- | --- | --- |
| Gateway | Go | 登录、会话、成员管理、模型配置、模型调用、工具配置、预算、审计、Runtime 代理 |
| Runtime | FastAPI | Web UI、任务 API、文件上传、协作 API、SSE 和编排接口 |
| Worker | Python | PostgreSQL 任务领取、编排执行、恢复、事件镜像、结果和产物持久化 |
| Orchestrator | Python | 任务路由、Agent 创建、工作流执行、工具调用和最终综合 |
| PostgreSQL | PostgreSQL | 工作区、用户、任务、运行、事件、Checkpoint、模型、工具、分享和审计 |
| 文件存储 | 本地目录 | 上传文档、任务产物和归档文件 |

## 任务执行流程

```mermaid
sequenceDiagram
    participant User as Browser
    participant Gateway as Go Gateway
    participant Runtime as Python Runtime
    participant DB as PostgreSQL
    participant Worker as Python Worker
    participant Orchestrator
    participant Model as Model Provider
    participant Tool as Tool

    User->>Gateway: 提交任务
    Gateway->>Gateway: 验证会话与工作区
    Gateway->>Runtime: 转发受保护请求
    Runtime->>DB: 创建 Task 并进入调度队列
    Runtime-->>User: task_id

    Worker->>DB: 领取任务租约
    Worker->>Orchestrator: 构造执行上下文
    Orchestrator->>Orchestrator: 选择工作流并创建 Agent

    loop 工作流步骤
        Orchestrator->>Gateway: 模型调用
        Gateway->>Model: OpenAI-compatible 请求
        Model-->>Gateway: 模型响应
        Gateway-->>Orchestrator: 响应与成本信息
        opt 需要工具
            Orchestrator->>Tool: 执行允许的工具
            Tool-->>Orchestrator: 结构化结果
        end
        Worker->>DB: 保存事件和 Checkpoint
    end

    Worker->>DB: 保存结果、引用和产物元数据
    User->>Gateway: 建立 SSE 连接
    Gateway->>Runtime: 代理事件流
    Runtime->>DB: 查询增量事件
    Runtime-->>User: 进度与终态
```

## 工作流

| 工作流 | 处理方式 |
| --- | --- |
| `direct` | 单轮模型回答 |
| `research` | 查询规划、检索、证据整理和引用综合 |
| `react` | 模型推理与工具调用循环 |
| `plan_execute` | 先生成步骤计划，再顺序执行 |
| `supervisor` | Supervisor 分派子任务并汇总结果 |
| `dag` | 根据依赖关系并发执行任务节点 |
| `swarm` | 多 Agent 多轮协作并综合输出 |

路由结果包含复杂度、联网需求、Agent 数量、执行步数、风险标记和运行限制。平台配置软上限与硬上限，用于控制 Agent 数量、并发量、工具调用和执行轮次。

## 数据与权限边界

- 用户通过工作区成员关系获得 `admin` 或 `member` 角色。
- 任务、输入文件、产物、评论和通知包含 `workspace_id` 与所有者信息。
- 未共享任务只允许所有者读取。
- 分享权限分为读取和评论。
- 管理员可以查看治理元数据，但不因管理员角色自动获得私有任务正文读取权限。
- Gateway 使用 HttpOnly、SameSite Cookie 保存会话。
- Gateway 向 Runtime 注入经过验证的用户、工作区和角色上下文。
- 模型 API Key 使用主密钥加密后存储。
- Web 抓取拒绝非 HTTP(S) 地址、私有网络地址和非文本响应。
- 上传和产物路径执行目录约束、文件名校验和大小限制。

## 项目结构

```text
.
├── app/                    Python Runtime、Worker、编排和工具
│   ├── api/                HTTP、SSE、文件和协作接口
│   ├── application/        Worker、授权策略和应用端口
│   ├── orchestration/      路由、Prompt 和 Orchestrator
│   ├── workflows/          工作流实现
│   ├── persistence/        PostgreSQL 与兼容存储适配器
│   ├── documents/          文档解析与摄取
│   ├── tools/              工具注册、策略和内置工具
│   └── static/             Web UI
├── gateway/                Go Gateway
├── infra/migrations/       PostgreSQL 迁移
├── contracts/              数据库、事件和 API 契约
├── scripts/                启动、迁移、Worker 和验证脚本
├── tests/                  Python 单元与集成测试
├── tests_contract/         契约测试
├── compose.yml             Compose 开发配置
└── platform.cmd            Windows 启动入口
```

## 环境要求

| 依赖 | 版本 |
| --- | --- |
| Python | 3.10+ |
| Go | 1.26+ |
| PostgreSQL | 16+ |
| 操作系统 | Windows 10/11，或可分别运行各组件的环境 |

Docker 不是本地启动的必需项。`compose.yml` 提供 Compose 运行配置。

## 本地启动

### 1. 安装依赖

```powershell
python -m pip install -e ".[dev,platform]"
```

### 2. 准备 PostgreSQL

创建一个可连接的 PostgreSQL 数据库和用户。初始化命令会要求填写连接 URL，例如：

```text
postgresql://agent_platform:password@127.0.0.1:5432/agent_platform_dev
```

### 3. 初始化配置

```powershell
platform.cmd init
```

该命令从 `.env.example` 创建或补全 `.env`，并配置：

- PostgreSQL URL
- 初始管理员密码
- Runtime 与 Gateway 内部令牌
- 模型凭据加密主密钥
- 本地服务地址

### 4. 检查环境并启动

```powershell
platform.cmd check
platform.cmd start
```

`check` 验证 Python 依赖、Go 工具链和 PostgreSQL 连接。`start` 执行数据库迁移、构建 Go 二进制、初始化工作区，并启动 Runtime、Gateway 和 Worker。

Web UI：

```text
http://127.0.0.1:8080
```

进程管理：

```powershell
platform.cmd status
platform.cmd stop
```

日志写入 `.run/logs/`。

## 配置

主要环境变量位于 `.env.example`。

| 环境变量 | 用途 |
| --- | --- |
| `PLATFORM_DATABASE_URL` | PostgreSQL 连接 URL |
| `INTERNAL_API_TOKEN` | Gateway、Runtime 和 Worker 的内部调用令牌 |
| `MODEL_MASTER_KEY` | 模型凭据加密主密钥，Base64 编码的 32 字节值 |
| `BOOTSTRAP_ADMIN_EMAIL` | 初始管理员邮箱 |
| `BOOTSTRAP_ADMIN_PASSWORD` | 初始管理员密码 |
| `BOOTSTRAP_WORKSPACE_NAME` | 初始工作区名称 |
| `LLM_BASE_URL` | 兼容模式下的模型 API 地址 |
| `LLM_API_KEY` | 兼容模式下的模型 API Key |
| `LLM_MODEL` | 兼容模式下的默认模型 |
| `TAVILY_API_KEY` | 搜索工具凭据 |
| `WEATHER_API_KEY` | 天气工具凭据 |
| `INPUT_FILE_MAX_BYTES` | 单个输入文件大小限制 |
| `ARTIFACT_MAX_FILE_BYTES` | 单个产物文件大小限制 |
| `MAX_TOOL_CALLS` | 单任务工具调用上限 |

团队模式下可以由管理员在治理界面配置 OpenAI-compatible 模型连接。`.env` 中的模型配置用于兼容运行路径和验证脚本。

## HTTP 接口

Gateway 默认监听 `http://127.0.0.1:8080`。

| 路径 | 用途 |
| --- | --- |
| `/`、`/ui` | Web UI |
| `/health/live` | 进程存活检查 |
| `/health/ready` | Gateway 与数据库就绪检查 |
| `/api/v1/auth/*` | 登录、退出和邀请接受 |
| `/api/v1/me` | 当前用户与工作区 |
| `/api/v1/workspace/*` | 工作区成员查询和成员邀请 |
| `/api/v1/tasks*` | 任务创建、查询、取消、事件、结果和回放 |
| `/api/v1/files*` | 文档上传与列表 |
| `/api/v1/notifications*` | 通知查询和已读状态 |
| `/api/v1/models` | 可用模型 |
| `/api/v1/admin/*` | 模型、工具、预算、审计和治理数据 |

## 数据库迁移

迁移文件位于 `infra/migrations/`，按编号顺序执行。

```powershell
python -m scripts.postgres_migrate --validate-only
python -m scripts.postgres_migrate --dry-run
python -m scripts.postgres_migrate
```

已应用的迁移不修改，通过新增编号迁移演进数据库结构。

## 测试

Python：

```powershell
python -m ruff check .
python -m compileall -q app scripts tests tests_contract
python -m pytest
```

Go：

```powershell
cd gateway
gofmt -w .
go vet ./...
go test ./...
```

PostgreSQL 集成测试通过 `TEST_POSTGRES_URL` 指定测试数据库。CI 使用 PostgreSQL 服务运行迁移与集成测试，并生成 Python 和 Go 覆盖率产物。

## 部署配置

生产环境需要在 Gateway 前配置 TLS 终止，并设置：

- `SESSION_COOKIE_SECURE=true`
- 独立的 PostgreSQL 用户与数据库
- 外部密钥管理
- Gateway、Runtime、Worker 和数据库的网络访问控制
- 进程资源限制
- 数据库与文件存储备份

Runtime 和 Worker 不对公网暴露，外部请求通过 Gateway 进入系统。

## License

本仓库当前未声明开源许可证。
