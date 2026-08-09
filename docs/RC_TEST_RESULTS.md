# KC761 v0.1.0-rc.1 验证记录

日期：2026-08-10

分支：`feat/kc761-v0.1.0-rc1`

基线：`a56c01a8daa0cfc31fc122caa90ea9d5c2a47c64`

## 已通过

- `python -m compileall -q custom_components tests`：通过。
- Ruff `0.12.8`：`All checks passed!`。
- `pytest 8.4.1`：26 个测试通过。
- 协议层 coverage（`custom_components/kc761/protocol`）：`96.23%`，达到 95% 门禁。
- JSON：`manifest.json`、`strings.json` 和两份翻译文件均可解析。
- 模拟器记录的客户端命令集合仅为 `0x53`、`0x54`；禁止写命令断言通过。

覆盖内容包括：固定/可变帧长度、逐字节分片、粘包、未知 FLAG、有界重同步、半帧/EOF、超时、迟到同步值、状态/设备信息候选解析、非法电池/NaN/ID、自动上传交错和本地 TCP 生命周期。

## 当前阻塞或未执行

- 开发机 PyPI 索引无法解析 `homeassistant==2026.7.4`（当前可见版本最高为 `2026.2.3`，安装返回 `No matching distribution found`），因此没有在开发机启动 HA 进程或执行 Hassfest。
- 目标隔离 HA 2026.7.4 栈尚未连接；需要在其镜像内执行 Config Flow、Coordinator、实体、诊断、reload/unload 和 Recorder 门禁。
- 没有真实 KC761 帧；端序、FP16、累计单位、7601 端口、固件兼容、BAG_LEN、ACK 回显、谱图完成条件和 BLE UUID 均保持 `hardware-unverified`。
- 本模拟 RC 不得部署到正式环境；实机 L 级证据和测试环境 24 小时稳定性门禁通过后才可生成发布候选。
