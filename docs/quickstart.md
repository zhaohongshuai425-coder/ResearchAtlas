# 从方向或已有材料开始

首次在宿主中加载Skill后，直接给研究方向即可。文献来源未知时由执行者一次询问一个问题：先提供PDF/ZIP/本地目录，还是自动检索。用户选择仅用现有材料时沿用其范围。

## 新建项目与本地文献

将下列尖括号内容换成实际路径；项目材料与仓库代码分别保存。

```text
python -B research-evidence-workflow/scripts/workflow.py research-flow init <新项目> --topic "研究方向" --id project-a
python -B research-evidence-workflow/scripts/workflow.py literature-tools init --library <新项目>/literature.json --topic "研究方向" --id project-a
python -B research-evidence-workflow/scripts/workflow.py local-ingest --library <新项目>/literature.json --input <已有文献.pdf>
```

默认仅收件；显式--index才解析。需要时先安装requirements-pdf.txt并用doctor --require pdf --probe核对环境。没有提供题名/DOI时文件名保持未核占位，不用下载或索引状态冒充精读。

已有v2等格式的完整知识库先适配，不用新建小库替代历史材料。已有半成品初稿、代码和结果先清点现状、约束和未完成项，再决定直接修订或设计验证。

## 续接和移交

```text
python -B research-evidence-workflow/scripts/workflow.py brief status --state <项目>/research_state.json
python -B research-evidence-workflow/scripts/workflow.py research-flow pack --state <项目>/research_state.json --output <新交接包.zip> --include-artifacts
python -B research-evidence-workflow/scripts/workflow.py research-flow restore <交接包.zip> <新接收目录>
python -B <新接收目录>/tools/receiver_check.py --folder <新接收目录> --output <新验收.json>
```

完整包只包含明确登记且满足已知依赖检查的成果。缺件或字节变化先解决，不把未知材料自动收进包。接收报告区分字节检查、依赖完整、证据回放和可选能力。
