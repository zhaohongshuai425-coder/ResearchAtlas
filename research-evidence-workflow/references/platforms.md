# Codex、DeepSeek与执行客户端

本工作流的文献、图谱、精读、比较、验证和写作规则共用一套。模型负责理解与规划；本地客户端提供文件读写、命令、联网或附件能力。切换模型不改变研究证据状态，也不扩大用户已授权范围。

## Codex

保留原SKILL.md及agents/openai.yaml入口、现有调用方式和标准库工具。`run-task`是Codex专用的独立任务辅助器，普通会话不嵌套启动；它不是其他平台的必需依赖。

## DeepSeek加本地客户端

可在支持DeepSeek模型和Agent Skills的客户端使用，例如OpenCode。将整个Skill安装到该客户端的发现目录，在客户端内连接DeepSeek并选择当前账户可用的模型。向客户端说明“请加载research-evidence-workflow，读取该项目并按当前阶段继续”。不要假定每个客户端都识别Codex的`$skill-name`语法或agents/openai.yaml。

OpenCode的项目目录为`.opencode/skills/<name>/SKILL.md`，用户目录为`~/.config/opencode/skills/<name>/SKILL.md`；它也支持`.agents/skills`。本地工具仍需要Python3.10+及按任务选用的依赖。用哪个模型由用户或客户端配置决定，本Skill不改全局模型、不安装客户端、不复制API密钥。

首次检查实际工具：是否能读项目文件、运行Python、联网检索和展示/生成文档。具备的能力直接使用，缺少的能力明确记录并选择可实施步骤。计划、命令文本、工具执行、真实产物与科学结论分别报告。

## DeepSeek普通对话界面

使用交付包中的`DeepSeek_直接复制使用.txt`，将内容粘贴到新对话，再给研究方向或该界面实际收到的材料。若界面允许上传文本，也可以附该指令文件；不假定所有版本都支持同一种附件。

仅凭对话指令不能获得本地磁盘权限、自动执行Python或持久写入本机知识库。能使用附件、联网或执行工具时按实际能力推进；不能执行时可以分析已提供内容、准备结构化登记/精读/图谱及修改建议，说明哪些内容待用户保存或由本地客户端执行。不要根据一个D盘路径宣称已经读取文件，也不要把给出的命令当作已经运行。

继续时使用用户提供的上轮成果、决定和缺件，不假定能访问另一个平台的聊天或本地状态。优先复用同一项目的现有库和图谱，不为换模型再建一个替代历史全库的小库。

## API开发接入

DeepSeek Harness及其他执行框架的目录、调用、只读工具桥和状态衔接见[harness接入](harness.md)。

`workflow.py portable-context --mode api --model <实际模型名> --task <本次任务> --output <新请求.json>`只生成Chat Completions格式请求文件，不发请求、不读取密钥、不附带研究数据或模型权重。调用方自行向DeepSeek兼容端点提交，保存返回和使用量。

默认导出的请求没有注册本地工具，因此不声称具备自动操作本机文件的能力。显式增加`--with-tools`可声明本包只读工具，调用方仍须实际连接执行器并回传工具结果；需要保存和核对原文、状态及权限边界。推理模式工具往返的reasoning_content等字段按当时官方API规则处理，不能仅改模型名字就宣称全部代理功能已适配。

DeepSeek当前Files API文档针对图片，不据此推断能上传PDF/Word作为通用研究文件。PDF仍可在本地解析后由调用方按授权发送选定文本或使用有相应附件能力的客户端。本包不自动上传项目内容。

## 来源和验证范围

2026-10-25核对：

- DeepSeek接入OpenCode：https://api-docs.deepseek.com/quick_start/agent_integrations/opencode/
- OpenCode Skill结构与发现：https://opencode.ai/docs/skills/
- DeepSeek兼容API：https://api-docs.deepseek.com/
- 工具调用由客户端执行：https://api-docs.deepseek.com/guides/tool_calls/
- Files API范围：https://api-docs.deepseek.com/zh-cn/guides/files_api/

本地上下文生成、格式、安装路径及通用工具测试不等于真实DeepSeek模型或网页附件的端到端验证。报告分别标注离线通过、宿主实际加载、API实际调用和研究内容核验。
