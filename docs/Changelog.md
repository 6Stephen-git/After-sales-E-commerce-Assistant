# 变更记录

## 与 Git 的关联

- 每次提交遵循 `Git_workflow.md` 的提交信息规范。
- 完成模块或重要功能合并到 `dev` 后，在此追加一条变更记录。
- 版本号格式：`v<主版本>.<阶段>.<迭代>`，与 Git tag 对应。
- 每条记录包含对应关键 commit 或 tag，可追溯。

## 记录粒度

- 每个功能模块（Agent / Controller / 后端端点 / 前端页面）开发完成并通过测试后，在此记录一条变更。
- 阶段里程碑合并到 `main` 时，在此记录一条版本发布。
- 文档体系重大修订后，在此记录一条。
- 不记录细碎的代码风格调整、单次提交、Roadmap 状态更新。

## 版本规则

- 格式：`[日期] [版本号] [变更类型] 简述`
- 变更类型：新增 / 修改 / 修复 / 删除
- 每次变更记录影响范围，便于回滚定位

## 记录


| 日期         | 版本                | 类型  | 简述                                                                                                          | 影响范围                                                                                           |
| ---------- | ----------------- | --- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 2026-04-29 | v0.1.0            | 新增  | Git 工作流规范文件 `Git_workflow.md`，明确分支策略与提交规范                                                                   | 项目治理                                                                                           |
| 2026-05-04 | v0.1.0-init       | 新增  | 阶段一项目初始化：schemas.py、Naming.md、Tools.md、dispute_rules.json、FastAPI 后端脚手架、Vue 3 前端空工程、完整目录结构、初始化验收测试（46项全部通过） | 全局                                                                                             |
| 2026-05-04 | v0.2.1-agent1     | 新增  | Agent1 事实还原员：extract() 纯函数、analyze_image/query_logistics 工具、4 类测试全部通过                                       | backend/agents/agent1, backend/tools/agent1_tools, tests/test_agent1                           |
| 2026-05-04 | v0.2.2-agent2     | 新增  | Agent2 策略参谋员：recommend() 纯函数、match_rules/query_buyer_profile/search_similar_cases 工具、4+3 类测试全部通过            | backend/agents/agent2, backend/tools/agent2_tools, tests/test_agent2                           |
| 2026-05-04 | v0.2.3-agent3     | 新增  | Agent3 话术生成员：generate() 纯函数、get_script_template 工具、4+1 类测试全部通过                                              | backend/agents/agent3, backend/tools/agent3_tools, tests/test_agent3                           |
| 2026-05-04 | v0.2.4-controller | 新增  | AssistedController 完成缓存增量合并与 Agent1→2→3 全链路调度，新增 4 类集成测试并通过                                                 | backend/controllers/assisted_controller.py, tests/test_assisted_controller.py, docs/Roadmap.md |
