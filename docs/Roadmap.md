# 开发路线图与 TODO

> **架构说明**：本版本引入 Controller 层作为 Agent 调度中枢。所有 Agent 实现为无状态纯函数，由 Controller 调用。智能模式只需新增控制器，无需修改 Agent 核心代码。
> **版本管理**：每个阶段末尾的核心里程碑完成后，合并到 `dev` 并打 tag，同时更新 `Changelog.md`。

## 阶段一：项目初始化

- [x] `schemas.py` 接口数据结构定义
- [x] `Naming.md` 命名约定
- [x] `Tools.md` 工具能力定义
- [x] `dispute_rules.json` 规则库初版
- [x] 项目脚手架搭建（FastAPI + 前端空工程）
- [x] 目录结构创建
- [x] 通过初始化验收测试
- [x] **合并到 dev，打 tag `v0.1.0-init`，更新 Changelog**

## 阶段二：核心 Agent 串行开发（按需搭建依赖）

### Agent 1 事实还原员

- [x] 实现 `agents/agent1/` 模块
- [x] 接入多模态 API 和物流查询工具
- [x] 编写内嵌测试用例并全部通过
- [x] 核心里程碑：Agent 1 独立可运行
- [x] **合并到 dev，打 tag `v0.2.1-agent1`，更新 Changelog**

### Agent 2 策略参谋员

- [x] 买家画像查询（可先用 Mock 数据）
- [x] 实现 `agents/agent2/` 模块
- [x] 编写内嵌测试用例并全部通过
- [x] 核心里程碑：Agent 2 独立可运行
- [x] **合并到 dev，打 tag `v0.2.2-agent2`，更新 Changelog**

### Agent 3 话术生成员

- [x] 实现 `agents/agent3/` 模块
- [x] 编写内嵌测试用例并全部通过
- [x] 核心里程碑：Agent 3 独立可运行
- [x] **合并到 dev，打 tag `v0.2.3-agent3`，更新 Changelog**

### 辅助模式控制器

- [x] 实现 `controllers/assisted_controller.py`
- [x] 负责：拉取材料（首次全量 + 缓存，后续增量追加）→ 调用 Agent1 → Agent2 → Agent3 → 组装报告
- [x] 编写集成测试用例并全部通过
- [x] 核心里程碑：辅助模式完整链路可运行
- [x] **合并到 dev，打 tag `v0.2.4-controller`，更新 Changelog**

## 阶段三：辅助 Agent 开发

### Agent 4 情绪监控员

- [ ] 下载并配置本地 BERT 模型
- [ ] 实现 `agents/agent4/` 模块
- [ ] 编写内嵌测试用例并全部通过
- [ ] 核心里程碑：Agent 4 独立可运行
- [ ] **合并到 dev，打 tag `v0.3.1-agent4`，更新 Changelog**

### Agent 5 复盘分析师

- [ ] 搭建 Celery + Redis 任务队列
- [ ] 实现 `agents/agent5/` 模块
- [ ] 编写内嵌测试用例并全部通过
- [ ] 核心里程碑：Agent 5 独立可运行
- [ ] **合并到 dev，打 tag `v0.3.2-agent5`，更新 Changelog**

## 阶段四：后端 API 服务

- [ ] MySQL 表结构创建（商家配置含 `mode` 字段、判例库、买家画像、规则库、话术模板）
- [ ] FastAPI 端点实现
- [ ] `/analyze` 端点内部调用 AssistedController
- [ ] 后端接口测试全部通过
- [ ] 核心里程碑：后端 API 完整可用
- [ ] **合并到 dev，打 tag `v0.4.0-api`，更新 Changelog**

## 阶段五：前端界面

- [ ] 左右分栏布局（聊天窗口 + AI 策略面板）
- [ ] 「请求 AI 帮助」按钮：商家手动触发分析
- [ ] 侧边栏展示分析报告、话术选项（点击使用填入聊天框）
- [ ] 前端与后端 API 联调测试
- [ ] 核心里程碑：辅助模式前端完整可用
- [ ] **合并到 dev，打 tag `v0.5.0-frontend`，更新 Changelog**

## 阶段六：端到端集成测试

- [ ] 全链路集成测试（模拟碎片化对话：首次分析 + 多轮纯文本后再次请求帮助）
- [ ] 验收标准见 `Evaluation.md`
- [ ] 核心里程碑：辅助模式 MVP 可交付
- [ ] **合并到 dev，打 tag `v0.6.0-integration`，更新 Changelog**
- [ ] **合并 dev 到 main，打 tag `v1.0.0-mvp`，Changelog 记录正式发布**

## 阶段七：智能模式

- [ ] 实现 `controllers/intelligent_controller.py`（对话管理引擎 + 决策层 + 按需调用 Agent）
- [ ] 新增 WebSocket 端点
- [ ] 前端新增自动化仪表盘 + 接管按钮 + 模式切换按钮
- [ ] 移交摘要生成逻辑
- [ ] 智能模式集成测试
- [ ] **合并到 dev，打 tag `v0.7.0-intelligent`，更新 Changelog**
- 说明：此阶段不修改 Agent 1~5 任何代码
