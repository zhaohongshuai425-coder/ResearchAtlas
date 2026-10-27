# 来源与第三方说明

本项目参考了文献检索留痕、原文回查、观察与假设区分、实验设计和选定范围审阅等工作流。可选外部Skill不随本仓库安装，也不是核心运行依赖。

## Scientific Agent Skills

来源：[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)，MIT。历史来源名称为Claude Scientific Skills；许可证仍以[上游LICENSE.md](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/LICENSE.md)为准。

参考的工作流包括literature-review、hypothesis-generation及experimental-design。本项目没有捆绑上游完整Skill、训练器或研究数据；为保留可能的工作流表达改编署名，完整上游MIT声明随[工具目录的第三方说明](research-evidence-workflow/scripts/tools/THIRD_PARTY_NOTICES.md)分发。

Timothy Kassis, Vinayak Agarwal, Yuhuan He, Darshil Patel, Aubrey M. Brueckner. 2026. *Scientific Agent Skills: A Library of Procedural Knowledge for Research Agents*. [arXiv:2609.00065](https://arxiv.org/abs/2609.00065). 作者、题名及当前版本于2026-10-27核对；引用工作流资源不表示本项目科研有效性已由该论文证明。

## 接口和可选依赖

Crossref、OpenAlex、Semantic Scholar、arXiv、Microsoft OCR、Codex、DeepSeek Harness等仅通过其公开接口或使用者已有环境接入，不包含其实现。Python可选依赖、模型及外部文献分别适用其许可证和服务条款。

其他历史参考Skill仅作为名称和思想来源保留在来源登记；未将其完整文件打包或对其授予本项目的MIT许可。
