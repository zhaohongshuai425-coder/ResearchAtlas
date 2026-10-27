# 当前运行说明

核心使用Python3.10+标准库；PDF需要pdfplumber/pypdfium2等按需包，语义检索另需本地固定模型及对应依赖。缺少可选能力不影响其他核心工具。

中英查询翻译是独立可选能力，发行包requirements-translation.txt列出依赖，doctor --require translation --probe只验实际导入，不代替本地权重及翻译效果验收。原文索引不接受未解决的文献身份冲突。

首次换电脑用workflow.py doctor；--probe实际导入本次必需的包，--require pdf或semantic选择范围。环境存在、导入通过、模型能跑和科学有效分别判断。普通会话不使用run-task嵌套模型。

平台与原生目录见platforms.md，Harness配置见harness.md；一般接续只读SKILL.md和stages内本阶段短指引，不默认加载维护历史。命令参数用对应--help。

本地入库用local-ingest，断点重跑按SHA和来源去重，未核身份和未读状态保留。导出用portable-context，失败后--recover-only按原暂存恢复；完整请求已变或用户改了结果时停止覆盖。

安装、升级、回退、状态与恢复由包根install.py提供，操作前后核对文件清单，旧版进入私有备份账本。检测到用户修改时先合并，不直接覆盖。构建和离线测试由build_release.py及run_tests.py负责，所有测试夹具随发行包提供；真实私有原文验收单独保存。

默认排版预设为可覆盖的文字配置，不捆绑学校Word模板。适用规范由使用者提供，不能将此预设认证为最新完整校方标准。

## 交接与接收验收

`research-flow status`及`brief status`显示`handoff_ready`和证据依赖漏项。检查已登记图谱、完成回执、审阅、查询、文献/语料、索引与缓存的明确绑定；未知格式只校验字节，不承诺任意文件的依赖自动分析。状态`passed`表示当前字节和阶段记录可核对，`handoff_ready`另表示已知依赖均已登记。未登记但仍在本地的依赖可使前者通过、后者失败；源文件缺失或变化会使状态失败。

完整打包`research-flow pack --state <状态> --output <新ZIP> --include-artifacts`在已知依赖缺失、改变或未登记时拒绝，列出`dependency_check`；核对项目范围后逐个使用`artifact`登记，或由文献的`bind-flow`登记快照和原件。工具不会自动扩大打包范围。默认元数据包仍可导出，清单明确记录原件遗漏和依赖不完整；`verify-pack`的字节校验通过不等于证据完整。

标准ZIP先用`research-flow restore <ZIP> <新目录>`恢复，再在新目录执行`python -B tools/receiver_check.py --folder . --output <新报告.json>`。接收器识别标准`package_manifest.json`和旧专用`receiver_manifest.json`；从绑定的登记文件定位多个或自定义名称的快照，检索并回放实际索引中的文本控制问题，以及已登记查询中的候选。可用`--query`给验收问题，`--library`选择project/下登记的相对路径。没有已索引原文时明确跳过，`evidence_replay_passed=false`，不冒称原文验收完成。

结果JSON及回传ZIP同时保存；已有结果须另取输出名称。默认不加载语义模型，显式`--semantic`且环境和固定模型具备时才尝试。索引自选题只验证工具读取和回放，不是独立准确率评估或科学支持证明；实体跨机与完整论文流程另行实测。

图谱查询的来源按其`local_path`规范解析，旧查询原件保持。历史library查询和带完整快照的corpus查询，如果工作登记更新或未随包附带，只有在每个保存候选确实通过原有resolve校验后，才把该登记变化记为`context_warnings`，在摘要显示`dependency_warning_count`。实际原文、索引、缓存和候选内容仍须匹配，显式登记的成果文件仍不可改变；graph查询及缺快照的旧corpus查询继续严格绑定原版本。不重新排名，不改写历史查询，也不自动提升科学支持状态。

corpus自动控制题来自实际索引中未被质量门槛隔离的原文行，避免从已隔离乱码页出题；全部被隔离时明确跳过。数据库格式或表结构错误会保留错误类型和阶段，输出`core_passed=false`的JSON及回传ZIP，并以非零退出码结束。字节运输完整与后端格式可用分别判断。

独立主张审阅中的`independent_review.report`属于完整交接的已知依赖：未登记、缺失、改变或越界时阻止完整打包；没有独立审阅的记录仍保持未执行状态。补读相邻页沿用已保存索引的质量隔离及`expected_script`条件，已隔离文本列入`held_passages`并保留质量说明，待原页复核；不因补读放宽门槛或提升支持状态。
