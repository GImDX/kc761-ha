# KC761 文档索引

本文档目录按用途分为当前规范、证据账本和历史记录。安装者优先阅读 README、发布说明和安全模型；实现者再阅读架构与协议证据。

## 当前规范

- [ARCHITECTURE.md](ARCHITECTURE.md)：`0.2.0-rc.1` 的模块、连接、配置、谱图和生命周期架构。
- [ENTITY_MODEL.md](ENTITY_MODEL.md)：当前创建的传感器、控制实体、默认门禁和 Recorder 策略。
- [SAFETY.md](SAFETY.md)：读取/写入命令边界、双层控制门禁、隐私与生产部署限制。
- [TEST_RELEASE_PLAN.md](TEST_RELEASE_PLAN.md)：RC 的自动化、测试 HA、真实设备与发布门禁。
- [PERFORMANCE.md](PERFORMANCE.md)：已实现的缓存、归档索引和 ECharts 增量交互更新。
- [RELEASE_NOTES_0.2.0_RC1.md](RELEASE_NOTES_0.2.0_RC1.md)：本次候选的变更、验证结果、升级说明和剩余风险。

## 协议与资料

- [PROTOCOL_EVIDENCE.md](PROTOCOL_EVIDENCE.md)：手册、上位机流、TCP/BLE 实机证据及未确认项。它是证据账本，不等于跨型号保证。
- [KC761x编程手册.md](KC761x编程手册.md)：便于代码审阅的本地转录；不替代授权原件，也不受仓库 MIT 许可覆盖。
- [DECISIONS.md](DECISIONS.md)：关键架构决策及从 v0.1 只读基线到 v0.2 RC 的变更边界。

## 历史记录

- [RC_TEST_RESULTS.md](RC_TEST_RESULTS.md)：`0.1.0-rc.1` 的历史模拟/实机/稳定性记录，不作为当前 RC 的 CI 结果。
- [V0_2_EXPANSION.md](V0_2_EXPANSION.md)：`0.2.0-alpha.*` 开发和浏览器验收日志；当前行为以本页“当前规范”文档和代码为准。

原始 PDF、上位机截图、通信捕获、完整设备 ID、测试凭据和部署地址保留在私有工作区，不进入公开仓库。
