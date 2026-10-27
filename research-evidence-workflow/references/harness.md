# Harness接入

本包同时提供DeepSeek Harness原生Skill目录适配，以及供其他执行框架接入的一次性JSON工具桥。`harness-contract.json`是本项目自定义的桥接描述，不是DeepSeek官方插件配置，也不保证任意同名框架直接识别。

## DeepSeek Harness

官方仓库的`skill-filesystem`支持`<项目>/.dsh/skills`及`.agents/skills`，用户级默认为`$DSH_HOME/skills`或`~/.dsh/skills`。Skill目录内保留SKILL.md及相对资源；不是把压缩包直接放在扫描根目录。

```text
python -B -X utf8 install.py --target harness --project "<已有项目>"
```

用户级安装去掉`--project`。不覆盖已有同名目录，已有安装先另存备份并比较变化。

如果自己的Harness组合尚未加载Skill能力，按官方组合配置启用`@deepseek-ai/dsh-skill`、`@deepseek-ai/dsh-skill-filesystem`和`@deepseek-ai/dsh-tool-skill`。本包不自动修改组合配置。标准消费者可以通过`/research-evidence-workflow`显式调用，或由模型使用`skill`工具加载；目录存在不等于当前组合已暴露这些工具。

实际验收应先看到原生目录/catalog中该名称，再加载正文和资源，最后执行一次有真实产物的任务。CLI检测、文件拷贝或自定义清单校验不能替代这三步。当前若未安装dsh或没有可用模型连接，保持“目录和协议适配，原生运行待验”。

## 其他Harness的本地工具桥

工具桥无第三方依赖，不调用模型、不联网、不写研究状态。宿主按本次用户范围固定项目根目录；模型只能传项目内的相对文件名，不能修改根目录或提交任意shell命令。

```text
python -B -X utf8 "<skill>/scripts/workflow.py" harness-tools --list
python -B -X utf8 "<skill>/scripts/workflow.py" harness-tools --root "<项目>" --input "<单次工具请求.json>"
```

单次请求示例：

```json
{"id":"q1","tool":"research_status","arguments":{"state":"research_state.json"}}
```

接口包含状态核验、文献元数据分页、图谱摘要和随包参考阅读。结果包含请求ID、ok、真实返回和范围；检查失败保留非零状态，输出过大则拒绝并提示缩小分页。没有常驻服务、循环监测或额度消耗。

如果使用DeepSeek Chat Completions工具调用，宿主从模型的tool_calls取出函数名和JSON参数，转成上述请求，实际调用后以同一tool_call_id回传JSON结果，再让模型继续回答。推理模式消息字段按官方规范保留。整个往返由宿主负责；本地工具桥不替代模型调度器。

API请求可用`portable-context --mode api --with-tools --model <实际模型> --task <任务> --output <新文件>`导出。它声明上述四个函数，但不会发送请求或执行函数；调用方需连接执行器。其余写文件、收文献、建库和训练由宿主已有工具在授权范围内执行，不能误用此只读桥声称完成。

## 研究状态与恢复

研究进度以research_state.json及其绑定证据为准；Harness会话历史负责聊天与工具往返。换模型或恢复会话时先核验状态摘要，失效文件和未完成阶段继续保留。科学结论、阶段完成、进程结束与模型finish_reason各自核验，不相互代替。

桥接端没有后台重试；失败记录及下一步由宿主保存。不能因为使用Harness就自动扩大为多代理、定时监控或无限训练任务。

## 核对依据（2026-10-25）

- [官方文件系统Skill提供器](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)：核对目录与frontmatter；Git blob `7d99666ca3ae0673128272ce7479bcd6f1c319f4`。
- [官方Skill消费者](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/tool-skill/README.md)：核对catalog、加载与显式调用；Git blob `fa342799a1d1b0efb279b8f231e6f06c478a04ef`。
- [DeepSeek工具调用](https://api-docs.deepseek.com/guides/tool_calls/)：函数的实际执行由客户端实现。

官方指南站本轮返回404，已改用官方GitHub仓库文档和源码核对。以上Git blob指核对文件的版本，不是整个仓库的commit。后续Harness变更需重新核对。
