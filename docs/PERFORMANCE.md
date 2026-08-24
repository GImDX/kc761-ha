# KC761 `0.2.0-alpha.36` 性能记录

状态：alpha.36 性能候选，仅完成开发机静态、单元和合成基准门禁；没有把本机结果写成树莓派、Chrome 或真实 Home Assistant 性能证据。alpha.35 的部署哈希、测试栈和浏览器历史证据保留在 [0.2 扩展设计](V0_2_EXPANSION.md) 的历史章节中，不在本文件改写。

当前 alpha.38 在 alpha.37 Saved spectrum 普通视图资源版本收尾基础上，增加 Config Flow 的 TCP/BLE 分步端点选择；卡片和 ECharts URL 继续共享同一个 cache-busting 版本，历史谱图保持按需惰性加载，不依赖 Lovelace 配置保存。以下基准和 alpha.36 优化记录保持不变。

## 已完成的优化

- 谱图卡在数据集/点数组变化时一次性规范化有效点，并缓存 channel、energy、counts、峰值、总计数、X 轴范围和折线数据；排序通道的 ROI 总数使用前缀和，数据游标使用直接索引或二分查找，异常/非排序输入保留有界回退路径。
- ROI、Data cursor、图表绘制和摘要更新使用 requestAnimationFrame 合帧；断开时取消待执行 frame，并沿用既有视图状态 flush 语义。固定摘要字段只在数据缓存变化时重算。
- 普通视图不再无条件请求历史谱图；编辑预览或选择历史时惰性加载，重复请求共享同一个 Promise。隐藏文档停止自动刷新，恢复可见时最多补一次；主题颜色按 frame 合并刷新，并读取实际 CSS 变量内容，不只依赖主题名称。
- 谱图片段完成状态改为增量跟踪，历史谱图归档的 `.metadata.json` 索引只在首次访问、文件变化或恢复时扫描完整 gzip 内容。

这些修改没有改变卡片公开配置、实体、WebSocket 类型、协议命令、localStorage 视图状态 schema 或 HA extra-JS 的接口；alpha.36 只使资源查询参数产生新的缓存键。

## 合成基准

基准均在本机使用合成数据或临时归档运行，比较同一输入下的旧实现与 alpha.36 候选实现。数值为墙钟毫秒，适合比较相对变化，不是生产 SLA；没有在 Raspberry Pi、Chrome Performance Profile 或真实 ECharts DOM 上测量。

| 场景 | 旧实现 | alpha.36 | 相对结果 |
| --- | ---: | ---: | ---: |
| `history256`，256 条临时归档记录 | 2511.187 ms | 4.590 ms | 约 547× |
| `frontend2048/3000`，2048 点、3000 次转换循环 | 1120.12 ms | 107.19 ms | 约 10.45× |
| fragment，1 道 | 51.399 ms | 0.751 ms | 约 68.44× |
| fragment，16 道 | 3.723 ms | 0.282 ms | 约 13.20× |
| fragment，128 道 | 0.575 ms | 0.300 ms | 约 1.92× |
| fragment，512 道 | 0.251 ms | 0.252 ms | 约 0.996×，不宣称收益 |

`frontend2048/3000` 的重点是确认点转换、峰值/ROI/游标和折线数据缓存的重复工作下降；fragment 场景显示增量跟踪主要改善小片段高频完成路径，大批量片段的绝对时间已经很小，不能仅凭该表决定进一步重构。

## 谱图 metadata index 与升级兼容

`SpectrumArchive` 的每个配置条目目录使用以下两类文件：

- `*.json.gz` 保存完整谱图，是数据权威来源；`load(snapshot_id)` 只按经过校验的 snapshot ID 读取 gzip，不依赖索引。
- `.metadata.json` 为版本 `1` 的旁车索引，保存 snapshot metadata、gzip 文件大小和 `mtime_ns`。它只服务于历史列表和变化检测，不复制或替代 2048 点数组。

保存路径先将 gzip 写入同目录临时文件并 `os.replace` 到目标文件，再以临时 JSON 加 flush/fsync/`os.replace` 原子更新索引。索引写失败不会撤销已经落盘的 gzip；实例保留 `_index_dirty`，下一次列表访问重试索引写入。临时文件在成功或异常后清理。

首次访问、索引缺失、JSON 损坏、版本不支持或条目校验失败时，系统扫描安全命名的 gzip 文件，解压并验证 snapshot ID，跳过损坏/不匹配文件后重建 `.metadata.json`。已有 gzip 不需要迁移；升级到 alpha.36 不改变归档文件名、快照格式、索引版本或最多 256 条的保留策略。索引丢失可以重建，索引不是恢复数据的单点；恢复后下一次 `list_metadata()` 会按文件大小和修改时间增量刷新新增、删除或变化文件。

浏览器视图状态仍使用原有 `kc761-spectrum:view-state:v1` schema；清除或禁用 localStorage 仍安全回到默认视图。alpha.35 到 alpha.36 的唯一预期缓存变化是 `/kc761/kc761-spectrum-card.js?v=...` 和 ECharts 资源 URL 的版本查询参数，HA 配置条目、实体 unique ID 和谱图归档不需迁移。

## 暂缓项目

以下事项不由本提交隐式开启，均需要独立设计、真实 profile 和单独验收：

- 将完整 `points` 从 Home Assistant state 移出；
- ECharts 增量 `setOption`/数据更新或切换 Canvas renderer；
- transport priority scheduler；
- 进一步收缩 Lovelace 错误卡扫描预算或改变其补救语义。
