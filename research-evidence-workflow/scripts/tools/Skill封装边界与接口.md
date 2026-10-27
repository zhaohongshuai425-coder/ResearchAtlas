# 封装与维护

当前操作规范以包根SKILL.md和references/下的阶段指引为准。Python入口为scripts/workflow.py，各命令用--help查看。核心工具保持模型无关，平台配置单独在references/platforms.md及harness.md。

本地批量入库、可恢复导出和安装升级/回退的异常路径有独立测试；实际研究与模型闭环验收另记。历史开发记录保存在发行维护档案，不能作为当前已完成事实。
