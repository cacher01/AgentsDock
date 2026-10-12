# EZcode

EZcode 是一个面向团队的多智能体任务与 AI 治理平台。用户可以直接提交自然语言任务，系统根据任务复杂度动态选择执行策略、调用受控工具、处理文档并生成回答或文件产物；管理员可以统一管理成员、模型、工具、软预算和审计信息。

项目采用 Python Runtime、Go Gateway 和 PostgreSQL 的分层架构，保留任务恢复与回放、模型 fallback、成本控制、权限隔离、可观测事件和显式资源分享等能力。

## 主要能力

- 动态路由 direct、research、ReAct、supervisor、DAG 和 swarm 工作流
- 团队空间、管理员与成员权限、私有任务和显式分享
- OpenAI-compatible 模型接入、模型选择与失败降级
- 工具注册和工作区启停，内置搜索、网页抓取、天气和计算工具
- TXT、Markdown、CSV、TSV、JSON、PDF、DOCX、XLSX 文档输入
- Markdown、TXT、JSON、CSV 和 ZIP 产物生成
- 任务事件、执行图、SSE 进度、Checkpoint 恢复与回放
- Token、成本软预算、调用审计和治理视图

## 技术架构

~~~text
Browser
   |
Go Gateway        认证、会话、模型与工具治理、预算、审计
   |
Python Runtime    多智能体编排、文档处理、工具执行、产物生成
   |
PostgreSQL        用户、工作区、任务、执行事实与治理数据
~~~

## 环境要求

- Windows 10/11
- Python 3.10+
- Go 1.26+
- PostgreSQL 16+

Docker 不是必需项。仓库中的 compose.yml 仅用于开发或演示环境。

## 快速启动

在项目根目录执行：

~~~powershell
platform.cmd init
platform.cmd check
platform.cmd start
~~~

init 会生成或补全本地 .env，并询问 PostgreSQL 连接和初始管理员信息。启动成功后访问：

~~~text
http://127.0.0.1:8080
~~~

常用管理命令：

~~~powershell
platform.cmd status
platform.cmd stop
~~~

如需手动安装 Python 依赖：

~~~powershell
python -m pip install -e ".[dev,platform]"
~~~

## 模型配置

管理员登录后可以在治理界面配置 OpenAI-compatible 模型连接。也可以在 .env 中设置：

~~~dotenv
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=your-model
~~~

不要提交真实 .env、API Key、数据库密码或生成的模型主密钥。

## 测试

~~~powershell
python -m pytest
python -m ruff check .
python -m scripts.postgres_migrate --validate-only

cd gateway
go vet ./...
go test ./...
~~~

PostgreSQL 集成测试通过 TEST_POSTGRES_URL 指定测试数据库。

## 当前边界

EZcode 当前定位为工程型 MVP，适合本地演示、功能验证和二次开发。生产部署前仍需配置 HTTPS、Secure Cookie、外部密钥管理、备份恢复、资源限制和正式监控告警。

## License

当前仓库尚未声明开源许可证。未经许可，请勿将代码用于再分发。
