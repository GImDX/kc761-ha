# KC761 文档索引

本文档目录按用途分为当前规范、证据账本和历史记录。当前版本为 `0.2.0`；是否已公开发布以 GitHub Release 为准。安装者优先阅读 README、当前版本说明和安全模型；实现者再阅读架构与协议证据。

## 当前规范

- [ARCHITECTURE.md](ARCHITECTURE.md)：当前模块、连接、配置、谱图和生命周期架构。
- [ENTITY_MODEL.md](ENTITY_MODEL.md)：当前创建的传感器、控制实体、默认门禁和 Recorder 策略。
- [SAFETY.md](SAFETY.md)：读取/写入命令边界、双层控制门禁、隐私与生产部署限制。
- [TEST_RELEASE_PLAN.md](TEST_RELEASE_PLAN.md)：自动化、测试 HA、真实设备与发布门禁。
- [PERFORMANCE.md](PERFORMANCE.md)：已实现的缓存、归档索引和 ECharts 增量交互更新。
- [RELEASE_NOTES_0.2.0.md](RELEASE_NOTES_0.2.0.md)：0.2.0 的当前行为、升级说明和验证边界。
- [PRE_RELEASE_COVERAGE.md](PRE_RELEASE_COVERAGE.md)：当前阶段摘要以及按时间保留的设计、测试、部署和用户验收记录。
- [CODE_REVIEW_RC9.md](CODE_REVIEW_RC9.md)：第 5 项完整代码审查的覆盖范围、问题与修复证据；未完成部分不代表通过。

## 协议与资料

- [PROTOCOL_EVIDENCE.md](PROTOCOL_EVIDENCE.md)：手册、上位机流、TCP/BLE 实机证据及未确认项。它是证据账本，不等于跨型号保证。
- [KC761x编程手册.md](KC761x编程手册.md)：便于代码审阅的本地转录；不替代授权原件，也不受仓库 MIT 许可覆盖。
- [DECISIONS.md](DECISIONS.md)：关键架构决策及从 v0.1 只读基线到 v0.2 RC 的变更边界。

## 历史记录

- [RELEASE_NOTES_0.2.0_RC9.md](RELEASE_NOTES_0.2.0_RC9.md)：rc9 开发、测试及验收过程；其中待执行描述为当时检查点，不替代当前版本说明。
- [RELEASE_NOTES_0.2.0_RC1.md](RELEASE_NOTES_0.2.0_RC1.md)：rc1 候选时期的说明和测试结果，不作为 rc9 操作指南或远端 CI 结论。
- [RC_TEST_RESULTS.md](RC_TEST_RESULTS.md)：`0.1.0-rc.1` 的历史模拟/实机/稳定性记录，不作为当前 RC 的 CI 结果。
- [V0_2_EXPANSION.md](V0_2_EXPANSION.md)：`0.2.0-alpha.*` 开发和浏览器验收日志；当前行为以本页“当前规范”文档和代码为准。

原始 PDF、上位机截图、通信捕获、完整设备 ID、测试凭据和部署地址保留在私有工作区，不进入公开仓库。
