# KC761 `0.2.0-rc.1` 性能记录

本文记录实现策略和开发机合成基准。它不是树莓派、浏览器或生产 Home Assistant 的 SLA；真实前端帧率与资源占用仍需目标硬件 profile。

## 前端热路径

- 谱图数据变化时规范化一次 2048 点，并缓存 channel、energy、counts、峰值、总计数、X 轴范围和折线数据。
- 排序通道的 ROI 总数使用前缀和；数据游标优先直接索引或二分查找，异常输入使用有界回退。
- 绘制、摘要、ROI 和 Data cursor 更新用 `requestAnimationFrame` 合帧；卡片断开时取消待执行 frame。
- 数据、X/Y 轴、主题或完整配置变化使用完整 `setOption(..., notMerge:true)`。
- 仅 ROI/Data cursor 变化时使用带稳定 series id 的增量 option，只更新 `markArea`、`markLine` 和 `markPoint`，设置 `notMerge:false`、`lazyUpdate:true`，不再提交 2048 点 `series.data`。
- 隐藏文档停止自动刷新；恢复可见时最多补一次。历史列表和谱图按需惰性加载，重复请求共享 Promise。

完整更新仍保留 `notMerge:true`，因为数据源、线性/对数轴、能量/道址轴和主题切换需要清除旧组件状态；把所有更新都改为增量会增加残留配置风险。

## Home Assistant 状态与 Recorder

- 标量实体只读取同一个不可变 Coordinator 快照。
- 谱图对象、可用性和身份均未变化时，谱图实体跳过大属性重复序列化。
- 最新谱图的 2048 点仍在当前实体属性中供卡片使用，但 `_unrecorded_attributes={"points"}` 阻止 Recorder 写入点阵。
- raw 实体默认不创建；用户启用前应考虑 5 秒轮询下的 Recorder 写入量。
- 累计值可重置，不创建长期统计 state class。

## 谱图片段与存档

- A1/A0 assembler 以固定 2048 道数组和增量完成计数跟踪片段，避免每次小片段都扫描全部道址。
- 每个条目最多保存 256 个 `*.json.gz` 快照。
- `.metadata.json` 旁车索引只存元数据、gzip 大小和 `mtime_ns`；历史列表通常无需解压全部快照。
- 索引缺失、损坏、版本错误或文件变化时才扫描 gzip 并重建。gzip 是权威数据，索引可删除重建。
- gzip 和索引都先写同目录临时文件，再 `os.replace` 原子切换；索引失败不会撤销已成功保存的谱图。

## 历史合成基准

alpha.36 在同一开发机、同一输入下记录过以下相对基准；RC 保留对应算法，并新增前端 overlay 增量更新。绝对数值不代表目标 HA 主机。

| 场景 | 旧实现 | alpha.36 候选 | 相对结果 |
| --- | ---: | ---: | ---: |
| 256 条存档历史列表 | 2511.187 ms | 4.590 ms | 约 547× |
| 2048 点、3000 次前端转换 | 1120.12 ms | 107.19 ms | 约 10.45× |
| 1 道片段完成检查 | 51.399 ms | 0.751 ms | 约 68.44× |
| 16 道片段完成检查 | 3.723 ms | 0.282 ms | 约 13.20× |
| 128 道片段完成检查 | 0.575 ms | 0.300 ms | 约 1.92× |
| 512 道片段完成检查 | 0.251 ms | 0.252 ms | 无可声明收益 |

## 升级兼容

- 谱图 gzip 格式、文件名、索引版本和 256 条保留上限不变。
- 浏览器视图状态继续使用 `kc761-spectrum:view-state:v1`；ROI、Data cursor 和缩放状态在同一实体/卡片配置下持久。
- 卡片和 ECharts URL 使用 `0.2.0-rc.1` 查询参数形成新缓存键；不迁移 Config Entry 或实体 unique ID。

## 后续 profile 项

以下优化必须先取得目标硬件 profile，不能只凭合成基准实施：

- 将完整 `points` 从 HA 当前 state machine 移到独立订阅流；
- ECharts Canvas/SVG renderer 的目标设备取舍；
- transport 请求优先级调度；
- 浏览器错误卡诊断扫描预算的进一步收缩。
