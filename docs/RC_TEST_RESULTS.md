# KC761 v0.1.0-rc.1 验证记录

日期：2026-08-21（持续更新）

分支：`ci/kc761-v0.1.0-rc1-validation`

架构基线：`a56c01a8daa0cfc31fc122caa90ea9d5c2a47c64`

本文记录 RC 候选的可复现实验和外部验收证据。真实设备证据只提升已经逐帧对齐的本设备字段；跨固件表示、累计量单位、自动上传开启态、设备兼容性及 BLE 服务 UUID 仍按 `hardware-unverified` 处理；模拟器和 fixture 证据不得冒充真实 KC761 实机证据。

## 2026-08-20 真实设备首轮只读确认（脱敏）

本轮使用 ARP 身份门控后连接隔离测试设备；公开记录不包含目标地址、MAC、完整 `DEVICE_ID` 或原始帧。探针在 Python 进程内只调用 `iphlpapi.SendARP` 和 TCP socket，未使用 PowerShell 网络 API。

取证质量说明：首次临时探针的本地 `struct` 通道格式存在错位，该次输出作废，未进入证据或文档；本节全部帧哈希和数值均来自修正字段布局后的第二次只读取证。

- Python：`3.13.5`；ARP 门控成功，随后 TCP 连接成功。
- 应用层发送：6 次，1 次 `0x54`、5 次 `0x53`；禁止命令 `0x52/0x55/0x62/0x63/0x64/0x66` 均为 0。
- 6/6 应答关联成功：A5 设备信息 1 帧，A2 状态 5 帧；SYNC 依次为 0、1、2、3、4、5；BAG_LEN 为 100、81、81、81、81、81；包头长度按小端解释。
- 帧 SHA-256（按顺序）：
  - `22c004b5680ad1dccdc7e72192cf8767273d887d59bae74ab6bc8cd9dd041b0f`
  - `c896bba372f8666d640a62025d88e6a88b5af490f941edac079b8217d9d7aff2`
  - `4705e13e1169a86208e2a1f28a60a6377ed33627bd99727518f4ff32fe8f248a`
  - `9d4d94d545b08988041f1ab3188e7bf436b670bbbaa0611b13346eead7c143d5`
  - `dbf05fa7d7ff53d2839dfda0efed8a59dc0f72838156254e633df48c3bad10a4`
  - `0c604d55a577a4333173c683250fcf243ab0e10bad65c2e952951743db09e803`
- 观测到 6 个接收块、505 字节；本次为一块一帧，未观察到分片完成、粘包、迟到查询帧或 A3/A1/A4 自动上传。
- 设备信息候选：型号值 10，硬件 `V1.0`，主固件 `V1.99`，`CO_FW_VER` 原始值 11，传感器候选 `0x01/0x00/0x05`；ID 仅确认 16 字节 ASCII 槽，公开文档不保存其内容。官方上位机随后显示协处理器 `v1.1`，因此代码不再把该字段按主固件的 `/100` 规则格式化。
- 屏幕对照清单：气压候选 `998 hPa`、温度候选 `33.8 °C`、电池候选无效（超出 0–100）、RAD0 RAW CPS 候选 `16..22`、RAW 剂量率候选约 `9.894e-05..2.145e-04`、RAW 剂量当量率候选约 `1.099e-04..2.382e-04`、RAD1/RAD2 测量字段为 `-1`/unknown、设备时间候选 UTC `2026-08-18T23:02:56..58Z`。

本轮只读证据不改变代码中的 `hardware_verified=false`；截图已对部分字段提供逐项对照，但不确认跨固件通用的 FP16/FP32 最终编码、剂量/累计量单位、电池特殊值、屏幕时区或型号兼容矩阵。

## 2026-08-20 官方上位机截图逐字段对照（脱敏）

- 型号值 `10` 与 UI `KC761`、主固件 `199` 与 `v1.99`、传感器 `0x01/0x00/0x05` 与 `KC7601.21 CsI/None/PIN` 一致；硬件候选 `V1.0` 与 UI `v1` 一致，均按本设备字段 `confirmed` 记录。
- 协处理器原始值 `11` 与 UI `v1.1` 一致，代码改用独立 `CO_FW_VER` 格式化；这只确认本设备 `11 -> V1.1`，手册对该字段的 `/100` 示例作为矛盾保留。
- `RAD_SENSOR_STATUS=0x04`、`VOLUME_STATUS=0x0D`、`LED_SCREEN_STATUS=0x07` 与 Gamma 选择、小音量、按键音/计数音开、密集效果关、背光/计数闪光开基本相符；LED 亮度位仍是 `partially confirmed`。
- `998 hPa` 与 `33.8 °C` 得到 UI 的 `998 hPa`、`33.80/33.90 °C` 对照；电池实帧候选越界而 UI 为 `0%`，保持 `unknown`，不将越界值猜成 0。
- 两张剂量主页分别显示 `0.127/0.044 µSv/h、14 CPS` 与 `0.130/0.068 µSv/h、16 CPS`；计数图光标另示 `Avg 17.43 CPS / Inst 21 CPS`。低计数率 CPS 的个位数差异属于正常随机波动；这些截图与此前 RAD0 候选也不是同步样本，只用于记录字段/UI 语义与合理范围，不以差值判断异常，不用于确认缩放系数或逐值一致性。显示设置的平滑时间为 `30 s`，不等同于协议字段。
- About 页 Gamma 累计时间为 `4小时28分38秒`（16118 s）和 `12天16小时23分9秒`（1095789 s），累计量为 `36.89 µGy` 和 `40.99 µSv`。相对前次修正帧候选，两个时间字段各同增 `754 s`，支持连续小端秒计数，但没有独立墙钟采样，不称精确 elapsed-time 对齐；FP32 小端候选的数值量级与 UI 单位相符，手册正文 `mGy/mSv` 矛盾未消除，累计量继续不建实体。
- `hardware_verified` 仍为 `false`；完整字段级表和 `confirmed/partially confirmed/unconfirmed` 边界见 [协议证据账本](PROTOCOL_EVIDENCE.md#102-2026-08-20-官方上位机截图逐字段对照脱敏)。

## 2026-08-20 五分钟官方流与 CSV 逐帧确认（脱敏）

本轮不再用单次截图近似同步，而是让官方上位机通过字节透明观察端正常连接同一隔离设备。观察端只原样转发并记录两个方向的 TCP 字节，生成、修改、拦截协议字节均为 0；正式窗口连续 300 秒。完整私有捕获 JSONL 为 3,299,872 字节，SHA-256 `d2b3f652ee70b1f8163ac6abdcc3888a422838373c06bf84eb805f547c1434a2`；公开仓库不保存原始流、地址、MAC 或完整设备 ID。

- 原始上行流离线重放：1,807 帧，拆帧错误 0，逐帧哈希与 JSONL 记录全部一致。
- 正式窗口：560 个事务、1,327 个设备帧；A0 1,032、A2 239、A5 29、A6 22、AA 5。
- `0x52`：261 次请求中 258 次返回完整 4 帧谱图，3 次无响应；`0x53`：243 次中 239 次返回 A2，4 次无响应；`0x54` 29/29、`0x55` 22/22 均匹配。
- 官方上位机产生 5 次 `0x63` 校时，均收到 status=0 且回显 `0x63` 的 AA；集成与观察端没有生成任何写命令。窗口内没有 A1/A3/A4，A2 的自动上传状态也均为 0。
- 所有响应都按 SYNC、FLAG、谱图 source/offset 或 ACK 回显完成归属，无孤立/迟到响应；15 个谱图事务在四帧收齐前已经出现后续请求，实证同一流中的请求/响应交错。
- 258 个完整 Gamma 谱图组均为 source=0、offset=0/512/1024/1536、ratio=1、每帧 512 道，即 2048 道；没有 1-3 帧的部分组。

官方上位机导出三个 CSV，私有原件 SHA-256 分别为：计数率 `94ce88fb4180488c23f222b977739d330ce7ce3c0e77c9064d3c83e841167962`、剂量率 `9643a5bf7c272f4bf146eb3cb7178aad8189fc881e3774d4c6d5a6436bb30b3c`、能谱 `707d47bec44689a465960c8cc6a7752434d91a7d11a997289e47970f448f3129`。

- 捕获与时间序列 CSV 的共同范围有 324 个 A2/CSV 样本，最长公共子序列 324/324，RAW CPS 精确匹配 324/324。
- `RAD0_RAW_DOSE_EQ_RATE` 按小端 IEEE-754 binary16 解码为 mSv/h 并乘 1000 后，与上位机 µSv/h CSV 的最大绝对差仅 `2.7755575615628914e-17`；确认本设备字段和缩放。
- A2 设备时间与 CSV 时间 324/324 相差 0 秒；该设备同期被官方上位机定时校时，不把此结果推广为断电后的时区策略。
- 最终能谱导出的 1,971 个计数与一个唯一完整 A0 组的道址 77..2047 逐道相同；用同期 A6 工厂校准参数复算，1,971 个能量值按两位小数逐点一致。
- 239 个正式窗口 A2 的电池字节均为 `0x80`，低 7 位为 0，与 UI `0%` 一致；代码改为低 7 位百分比并保留 101..127 越界保护。bit 7 的具体含义仍未知。
- Gamma 三个 AVG 字段在 239/239 A2 中均为 0，而上位机显示非零 30 秒平均，证明该上位机在本地用 raw 序列计算平均。该差异登记为已知问题 `KI-AVG-001`：当前 HA 默认 AVG 实体会显示设备返回的 0，用户可启用 raw 实体查看实时测量；按此限制接受，不作为本轮实机验收失败。

五分钟分析摘要 SHA-256 为 `80462a1fad40edf3fb2a310f40536f57de3eb56e63a5a218b366e650af347ff1`，CSV 对齐摘要 SHA-256 为 `06ff589797127a54a3f0247156dc3a1c9a7af352b0f79e2a1532ffa765b6e93a`。完整字段边界见 [协议证据账本](PROTOCOL_EVIDENCE.md#103-2026-08-20-五分钟透明流与-kcsdi-导出对齐脱敏)。在 `KI-AVG-001` 限制下，本设备本轮实机验收通过；该结论不改变全局 `hardware_verified=false`，也不授权首版生成 `0x52/0x55/0x63`。

## 2026-08-20 真实 Home Assistant 条目与生命周期验收（当前候选）

本轮把通过逐帧核对的代码候选安装到持久隔离测试栈，并严格通过 Home Assistant 正常登录、Config Flow 和 Options Flow 创建及修改条目；没有直接编辑 `.storage`。测试凭据仅以 `0600` 只读挂载到一次性、无额外 capability、根文件系统只读的容器，临时 token 在每次成功或失败路径均撤销。公开记录不保存地址、完整设备 ID、token 或实体 ID。

### 固定构建与自动化

- 固定镜像 digest：`sha256:5a531753cea96444200158fc2b0ac7ccd739291ec50414877b396de6e0bb29b3`；image ID：`sha256:6cec48e3c2f6c43ca7f8a69c47d1ed56527b17bfc66fb16f073f3c0a3077c9c7`，ARM64。
- 候选 tar 共 50 个条目、500,224 字节，SHA-256 `84b4c4e39eaa986bed0950a094c0d4bf3b2fef4c3d69577c3b2ea42d7b122ffb`；传输前后哈希一致，且不含 `private`、`kcsdi_data`、`.git` 或 `.venv`。
- 固定 HA 2026.7.4 / Python 3.14 容器：41 passed、1 warning，4.77 秒；branch coverage `95.99%`，95% 门禁通过。唯一 warning 来自 HA 自身对 aiohttp `web.Application` 继承的弃用提示。
- 开发机 Ruff 与 compileall 通过；开发机系统 Python 未安装 pytest，本轮完整 HA 测试以固定镜像结果为准。

### 失败证据与修复

真实 HA 首次重启依次暴露两个模拟测试未覆盖的生命周期缺陷，失败记录均保留，没有被成功结果覆盖：

1. Coordinator 关闭调用了 HA 2026.7.4 不存在的 `async_stop_update()`，产生 `AttributeError` 并遗留读任务；修复为幂等调用基类 `DataUpdateCoordinator.async_shutdown()` 后再关闭客户端，并增加基类关闭只执行一次的测试。
2. TCP 长寿命读循环由 `hass.async_create_task()` 创建，被 HA 纳入启动阶段等待，产生 bootstrap timeout 和 pending `kc761-reader`；修复为 `ConfigEntry.async_create_background_task()`，使任务不阻塞启动并绑定条目卸载生命周期，增加 API 调用回归断言。

另有两次一次性验收容器因宿主 `0600` 文件所有者不同而在读取脚本或凭据前退出，以及一次复核脚本因 HA 条目列表不返回连接 `data` 而误发起重复 Config Flow、得到 `cannot_connect`；三者均未修改已有条目，后续使用内容哈希相同的 root-owned `0600` 脚本副本和允许公开的设备 ID 尾段复用条目。最终验收脚本 SHA-256 为 `825b605b208fde3b5ba15340fbc3f6e46ddf05b3131cdc28e34a17793876e381`，脚本和全部原始输出留在私有测试目录，不进入仓库。

### 正常 API 与运行验收

- 首次 Config Flow 创建实机条目成功；Options Flow 启用 raw 实体成功。重启后复用同一条目，没有创建重复设备。
- 测试栈共有 2 个 KC761 Config Entry，2/2 为 `loaded`。实机条目注册 21 个实体，其中 15 个活动；`unavailable=0`、`unknown=6`，15 个活动实体均保持 `hardware_verified=false` 和候选协议档案。
- Gamma 三个 raw 实体均为正数；未运行的 PIN 三个 raw 实体均为 `unknown`。Gamma 三个 AVG 实体为设备实际返回的 0，按 `KI-AVG-001` 接受；电池低 7 位显示 0%。
- 修复后 HA 重启 4 秒达到 HTTP 200；继续观察 30 秒，ERROR/CRITICAL/Traceback、bootstrap timeout、启动阻塞、KC761 pending task 和旧关闭异常均为 0。随后 Options Flow reload 再观察 15 秒，各项仍为 0。
- 同步运行源码镜像后重启模拟器，HA 自动恢复并在观察窗产生 5 次 `0x53`、1 次 `0x54`，禁止或未授权命令为 0，HA 错误和 pending task 均为 0。
- 运行代码替换前保存独立权限受限备份；一次因运行源码镜像没有 `tests` 目录而在任何覆盖前停止的部分备份也保留。测试 HA 与模拟器最终均保持运行，正式环境未连接、修改或重启。

本节证明当前候选在这台实机和固定 HA 版本上的配置、实体与生命周期行为通过；它不提升跨设备 `hardware_verified`，也不替代自动上传开启态和其它型号/固件门禁。本节当时尚未完成的 24 小时门禁由下一节补足。

## 2026-08-20 至 2026-08-21 二十四小时稳定性验收（脱敏）

本轮在持久隔离测试栈连续观察 86,400 秒。监控目录、原始 HA/模拟器日志和实体健康输出均为权限受限的私有测试证据；公开记录不包含主机地址、完整设备 ID、实体 ID、凭据、token 或协议帧。正式环境未连接、修改或重启。

### 连续运行与只读安全

- 监控从 `2026-08-19T20:59:14Z` 运行至 `2026-08-20T20:59:14Z`，`COMPLETE=1`、`EXIT_REQUEST=0`、`ELAPSED_SECONDS=86400`，取得 1,435 个分钟级样本。
- HA HTTP 失败、HA 非 running、模拟器非 running 均为 0；HA 与模拟器重启计数均为 `0 -> 0`，窗口结束状态均为 running，HA HTTP 为 200。
- HA 的 ERROR/CRITICAL/Traceback、bootstrap/blocking、KC761 pending task 和旧 `async_stop_update` 异常均为 0。
- 模拟器记录 `0x53` 17,281 次、`0x54` 1,371 次；`0x52/0x55/0x62/0x63/0x64/0x66`、`forbidden` 和 `invalid` 总计为 0。
- 窗口结束约 9.6 小时后的只读复核仍为：HA 与模拟器 running、重启计数均为 0、HA HTTP 200，结束后 HA 错误/pending task 和模拟器禁止命令均为 0；同期又记录 `0x53` 6,919 次、`0x54` 549 次。

### 实体证据与监控认证故障

分钟级运行采样本身完整，但独立实体健康子检查存在一项取证工具故障，不能静默写成 0 失败：前两次检查通过，从第 31 个样本起，测试账户的普通用户名/密码登录 flow 不再完成，导致随后 95 次检查（含最终检查）在认证阶段退出；这些失败没有执行实体断言，也没有产生或撤销临时 token。故原始汇总中的 `HEALTH_FAILURES=95`、`FINAL_HEALTH_EXIT=1` 表示监控认证失效，不表示 95 次实体不可用。

测试结束后使用本地权限受限文件中的长期 access token 进行只读补验；token 未输出、未复制到测试机、未写入日志或仓库，也未调用撤销接口：

- 当前测试栈有 2 个 KC761 Config Entry，2/2 loaded；目标实机条目注册 21 个实体、活动 15 个、预期 unknown 6 个、unavailable 0 个。
- Gamma 三个 raw 实体均为正，最大状态年龄 0 秒；`hardware_verified=false` 保持不变。
- Recorder 对完整 24 小时窗口返回 15/15 个活动实体历史，共 56,621 条记录，无缺失序列；三个 Gamma raw 序列共 50,688 条记录，三者均出现正值，窗口结束前最后 raw 更新距结束 3 秒。
- 历史中出现 30 条 unavailable 记录，按秒聚合为 2 次事件，每次同时影响 15 个活动实体并在 5 秒内恢复，总计 150 实体秒。测试人员确认这两次事件对应测试期间尝试启用代码层面未使能实体的操作，因此登记为操作诱发 reload/可用性切换，不归类为设备或集成自发掉线；事件和恢复事实均保留，不改写为零事件。

结论：在上述监控认证故障和两次已确认操作诱发事件的透明限制下，本设备 24 小时运行、自动恢复、任务生命周期、日志健康和只读命令安全门禁通过。该结论仅适用于本设备和当前固定 HA/固件组合，不提升跨型号/跨固件 `hardware_verified`，也不完成自动上传开启态门禁。

## 2026-08-20 代码候选基线官方 GitHub 验证

Draft PR #1 的代码候选基线为 `c8136424eb52ecb9c36665d5614431774308b21b`。该提交是与 `main` 建立 ancestry 的零树差异 merge commit；`main` 仍为 `30e8f52930acba4be9541e24dad61b41e1145e55`。该基线的 push 与 pull-request 两次 workflow 均成功：

- push run `32294694277`：<https://github.com/GImDX/kc761-ha/actions/runs/32294694277>
- pull-request run `32294699080`：<https://github.com/GImDX/kc761-ha/actions/runs/32294699080>
- Ruff、JSON validation、Python compileall：通过。
- pytest：41 passed、1 warning；branch coverage `95.99%`，95% 门禁通过。
- Hassfest：`Invalid integrations: 0`；HACS validation：9/9 checks passed。
- 后续提交仅更新验收文档，不改变代码树；Draft PR 最新两项 required checks 在本文发布时也均为 success。
- PR 保持 Draft、open、mergeable；未合并 PR，未创建 tag 或 Release。

## 2026-08-14 审查修复复验（历史候选）

适用代码/测试提交：`299abc9072c0b39196950ffa3e1d00712ae67f15`。本文档提交只更新证据说明，不改变该候选的运行代码。

### 固定镜像与传输完整性

- image digest：`sha256:5a531753cea96444200158fc2b0ac7ccd739291ec50414877b396de6e0bb29b3`
- image ID：`sha256:6cec48e3c2f6c43ca7f8a69c47d1ed56527b17bfc66fb16f073f3c0a3077c9c7`，架构 ARM64
- 最终测试 tar：34 个文件，266,240 字节，SHA-256 `7faf5cbe2c62e35fc59b9a6ecbaa1448a01ea5dffbd78088e18e92d691f7888e`
- tar 传输前后 SHA-256 一致；独立候选目录只读挂载，容器 `--pull never`、`--network none`、根文件系统只读、退出即删除
- 集成部署树 SHA-256：`dc26bf36f3de32e04bbcac3e7e8e39709368956e11f7be34732e27fa72f0c7fc`

### 自动化结果

- 开发机 Ruff：通过；pytest：36 passed、1 skipped，skip 原因仅为开发机未安装 Home Assistant
- 固定 HA 2026.7.4 / Python 3.14.6 容器：40 passed、1 warning，4.62 秒
- branch coverage：95.89%，95% 门禁通过
- 唯一 warning 仍来自 HA 自身 `homeassistant/components/http/__init__.py:347` 对 aiohttp `web.Application` 继承的 `DeprecationWarning`，不是集成代码告警
- 翻译 key、中英文 JSON 形状、公开文档网络/凭据/完整模拟 ID 脱敏均有静态门禁
- 第一轮固定镜像测试发现“空设备信息被错误归类为 `AttributeError`”，随后以独立提交修复并复验；失败结果未被覆盖成成功证据

### 持久隔离栈

- 升级前原子备份 source、HA config 中的集成目录和模拟器文件；最终备份标识为 `299abc9-20260814T132856Z`
- HA HTTP：200；协议档案日志：1；集成错误：0
- 既有配置条目中 4 个已启用 KC761 状态全部 available；这是升级场景实体数，不代表干净安装默认实体总数
- 启动观察到 `0x53` 5 次、`0x54` 1 次、禁止写命令 0 次
- 断开 simulator 后 4 个实体在 6.2 秒内全部 unavailable；恢复 simulator 后 6.0 秒内全部 available
- 独立客户端 100/100 次重连成功，200 responses、0 failures、0 forbidden commands
- 故障注入完成后，两容器共同连续运行 688 秒；HA HTTP 200，观察窗口内集成错误 0、禁止命令 0，集成部署树哈希保持不变
- API 验收通过 Home Assistant 正常登录 flow 获取当次 token，并在查询后立即撤销；未直接编辑 `.storage`，未输出凭据或 token
- 成功后 simulator 与 HA 两容器保留运行；正式环境未连接或修改

这组结果验证当时的审查后软件行为，不提升任何真实设备协议字段的证据等级。当时尚待重跑的 Draft PR 官方检查已由上面的 2026-08-20 代码候选基线双 workflow 成功记录取代。

## 2026-08-11 固定镜像验证（历史快照）

外部验收在固定 Home Assistant 2026.7.4 镜像内完成，使用 Python 3.14.6。镜像与运行约束如下：

- image digest：`sha256:5a531753cea96444200158fc2b0ac7ccd739291ec50414877b396de6e0bb29b3`
- image ID：`sha256:6cec48e3c2f6c43ca7f8a69c47d1ed56527b17bfc66fb16f073f3c0a3077c9c7`
- v3 输入 tar：41 个文件，1,554,432 字节，SHA-256 `7e671db2b8b15a8b8ed0707fae2f965dc7acd5b3a0b9e1f83b3160aa472bbcb2`
- 运行约束：`network-none`、只读、`--pull never`
- 静态检查：18 个 Python、5 个 JSON、13 个模块导入均通过
- `pytest -p no:cacheprovider`：32 passed，1 warning，4.25 秒；唯一 warning 来自 HA 2026.7.4 自身 `homeassistant/components/http/__init__.py:347` 对 aiohttp `web.Application` 继承的 `DeprecationWarning`，不是集成代码告警
- branch coverage：98.8035892323031%（841 statements，missing 4；162 branches，missing 8），门禁通过；无文件低于 95%

开发机 `.venv` 的 Ruff 检查通过；开发机 pytest 为 28 passed、1 skipped，唯一 skip 原因是本机未安装 Home Assistant。开发机不能从当前索引安装 `homeassistant==2026.7.4`，因此 HA 运行时结果以固定镜像记录为准。

测试宿主上的官方 Hassfest/HACS 镜像离线探测仍受架构限制：Hassfest digest 为 `sha256:8cd7bdb8f82430c2c13703290b1fc38dcc99957dd76ad3f230035ecee70b672d`，HACS digest 为 `sha256:41f6310585d9fb72c7a0e183cce0594355715bc24112b62bc4279b83412edccb`；两者均仅提供 amd64，ARM64 宿主直接运行得到 `exec /bin/sh: exec format error`。未安装或注册 binfmt、未使用 privileged，未修改 Docker daemon 或重启宿主。该限制只影响测试宿主上的离线容器复跑，不再构成官方验证结论的阻塞。

## 2026-08-11 官方 GitHub 验证（历史快照）

公开仓库 `GImDX/kc761-ha` 的 Draft PR #1 使用验证分支运行 `.github/workflows/ci.yml`。GitHub Actions run `31448849047` attempt 2 在提交 `132539d854a666ff936eb1d7c54c9b2c206769c4` 上完成，结论为 `success`；仓库默认分支 `main` 的许可证基线为 `30e8f52930acba4be9541e24dad61b41e1145e55`，GitHub 识别 SPDX 为 `MIT`。

- Ruff、JSON validation、Python compileall：通过
- 完整 pytest：32 passed，1 warning，3.39 秒
- branch coverage：98.81%（843 statements，missing 4；162 branches，missing 8），95% 门禁通过
- 唯一 pytest warning：HA 依赖自身对 aiohttp `web.Application` 继承的 `DeprecationWarning`，不是集成代码告警
- Hassfest：1 个 integration，`Invalid integrations: 0`
- HACS validation：topics、issues、archived、description、license、information、brands、integration manifest、hacs.json 共 9/9 通过
- workflow 结论：`success`；另有 GitHub runner 对 `actions/checkout@v4`、`actions/setup-python@v5` 的 Node.js 20 弃用提示，不影响各步骤结论

验证链接：<https://github.com/GImDX/kc761-ha/actions/runs/31448849047/attempts/2>

复验命令：

```text
python -m ruff check custom_components tests
python -m compileall -q custom_components tests
python -m pytest -q
coverage run --branch -m pytest -q
coverage report --fail-under=95
```

## 2026-08-11 持久栈验收快照（历史快照）

以下哈希和运行结果来自外部验收记录，用于确认部署内容和回滚链路；它们不提升协议字段的实机证据等级：

- deployed tree SHA-256：`5494568068d20f2daf1d47a82e0a2bc21c2e14830d3be45876ef5c60d5b60549`
- manifest SHA-256：`06a8daab63a8852ca5352b3d7764bb17960eda29fd79733334d1f6b1cc3377c0`
- compose SHA-256：`c11490f9ee2b6a6c5793744bcdb8f56cdb31757bf290b0566bb2536c69d7d671`
- serve 脚本 SHA-256：`6f57099c79210137fa339a0d430294564abe8a27c0dd80ab92afdcf7ca021f85`
- HTTP：200；Config Entry：loaded；设备数：1；实体数：13
- 启用传感器样本：1013 hPa、87%、23.5 °C、100.0 cps
- diagnostics：`hardware_verified=false`、连接正常、协议错误 0、重连 0、敏感字段 0
- 重启次数：0；bridge network 中容器数 2；TCP 7601：通过；最终启动命令仅出现只读 `CMD_GET_STA`/`CMD_GET_DEVICE_INFO`，`0x62/0x63/0x64/0x66` 和 `FORBIDDEN` 均为 0；HA/KC 错误样本：0
- 独立客户端历史运行：100/100 重连成功，200 responses/0 failures；断线后 4 个传感器先不可用、随后恢复；HA 重启和 Compose stop/start 回滚均恢复；连续观察超过 10 分钟

设备是否在线/可发现未确认，阴性扫描不具证明力，服务 UUID 未知。上述持久栈和模拟器结果不能替代真实设备抓包。

## 当前结论与剩余门禁

- HACS 根配置 `hacs.json`、当前全包 branch coverage 门禁、静态检查、固定镜像、真实 HA 生命周期、24 小时稳定性和只读安全门禁已通过；代码候选基线的 push 与 pull-request 两次官方 Hassfest/HACS 均成功，后续纯文档提交的 required checks 也通过。
- 首版仍只读；不发送校准、清除统计、设备时间、状态设置或其它写命令。
- 本设备 L 级证据已覆盖 A2/A5/A6、主动 A0 Gamma 谱图、RAW CPS、RAW 剂量当量率的 binary16/缩放、设备时间、电池低 7 位、2048 道谱图与 A6 能量刻度，以及官方上位机 `0x63` 的 ACK 回显；这些结论不得跨固件泛化。
- 已知问题 `KI-AVG-001`：当前默认 AVG 实体在本设备上持续为 0，官方上位机平均值由本地计算；用户可启用 raw 实体查看实时值。该限制已接受，本设备本轮实机验收通过，不作为 RC 测试发布阻塞。
- 累计量通用单位、RAW 剂量率的独立物理量对照、自动上传开启态、其它传感器与型号及 BLE UUID 仍未完成；这些门禁通过前不得提升整体兼容性声明或发布稳定版。
