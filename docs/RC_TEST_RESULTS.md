# KC761 v0.1.0-rc.1 验证记录

日期：2026-08-10  
分支：`feat/kc761-v0.1.0-rc1`  
架构基线：`a56c01a8daa0cfc31fc122caa90ea9d5c2a47c64`

本文记录 RC 候选的可复现实验和外部验收证据。协议端序、FP16、累计量单位、设备兼容性及 BLE 服务 UUID 仍按 `hardware-unverified` 处理；模拟器和 fixture 证据不得冒充真实 KC761 实机证据。

## 固定镜像验证

外部验收在固定 Home Assistant 2026.7.4 镜像内完成，使用 Python 3.14.6。镜像与运行约束如下：

- image digest：`sha256:5a531753cea96444200158fc2b0ac7ccd739291ec50414877b396de6e0bb29b3`
- image ID：`sha256:6cec48e3c2f6c43ca7f8a69c47d1ed56527b17bfc66fb16f073f3c0a3077c9c7`
- v3 输入 tar：41 个文件，1,554,432 字节，SHA-256 `7e671db2b8b15a8b8ed0707fae2f965dc7acd5b3a0b9e1f83b3160aa472bbcb2`
- 运行约束：`network-none`、只读、`--pull never`
- 静态检查：18 个 Python、5 个 JSON、13 个模块导入均通过
- `pytest -p no:cacheprovider`：32 passed，1 warning，4.25 秒；唯一 warning 来自 HA 2026.7.4 自身 `homeassistant/components/http/__init__.py:347` 对 aiohttp `web.Application` 继承的 `DeprecationWarning`，不是集成代码告警
- branch coverage：98.8035892323031%（841 statements，missing 4；162 branches，missing 8），门禁通过；无文件低于 95%

开发机 `.venv` 的 Ruff 检查通过；开发机 pytest 为 28 passed、1 skipped，唯一 skip 原因是本机未安装 Home Assistant。开发机不能从当前索引安装 `homeassistant==2026.7.4`，因此 HA 运行时结果以固定镜像记录为准。

复验命令：

```text
python -m ruff check custom_components tests
python -m compileall -q custom_components tests
python -m pytest -q
coverage run --branch -m pytest -q
coverage report --fail-under=95
```

## 持久栈验收快照

以下哈希和运行结果来自外部验收记录，用于确认部署内容和回滚链路；它们不提升协议字段的实机证据等级：

- deployed tree SHA-256：`5494568068d20f2daf1d47a82e0a2bc21c2e14830d3be45876ef5c60d5b60549`
- manifest SHA-256：`06a8daab63a8852ca5352b3d7764bb17960eda29fd79733334d1f6b1cc3377c0`
- compose SHA-256：`c11490f9ee2b6a6c5793744bcdb8f56cdb31757bf290b0566bb2536c69d7d671`
- serve 脚本 SHA-256：`6f57099c79210137fa339a0d430294564abe8a27c0dd80ab92afdcf7ca021f85`
- HTTP：200；Config Entry：loaded；设备数：1；实体数：13
- 启用传感器样本：1013 hPa、87%、23.5 °C、100.0 cps
- diagnostics：`hardware_verified=false`、连接正常、协议错误 0、重连 0、敏感字段 0
- 重启次数：0；网络检查：2 次；TCP 7601：通过；最终启动命令仅出现只读 `CMD_GET_STA`/`CMD_GET_DEVICE_INFO`，`0x62/0x63/0x64/0x66` 和 `FORBIDDEN` 均为 0；HA/KC 错误样本：0
- 独立客户端历史运行：100/100 重连成功，200 responses/0 failures；断线后 4 个传感器先不可用、随后恢复；HA 重启和 Compose stop/start 回滚均恢复；连续观察超过 10 分钟

服务 UUID 仍未知；BLE 扫描只表明设备在线，未确认可用服务。上述持久栈和模拟器结果不能替代真实设备抓包。

## 当前结论与剩余门禁

- HACS 根配置 `hacs.json`、全包 branch coverage 门禁、静态检查、固定镜像 HA 生命周期和只读安全门禁已固化。
- 首版仍只读；不发送校准、清除统计、设备时间、状态设置或其它写命令。
- 真实 KC761 L 级证据尚未完成：端序、FP16、累计单位、固件兼容、BAG_LEN、ACK 回显、谱图完成条件、默认端口和 BLE UUID 均不得写成已确认事实。
- 实机抓包、测试环境长稳和发布前回滚证据完成后，才能另行提升兼容性声明或开放实验性功能。
