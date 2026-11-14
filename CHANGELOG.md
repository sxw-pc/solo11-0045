# Changelog

## Unreleased

### 新增

- `sensitive` 子命令：对 `precision`、`rounding`、`range` 三类待试改动做行级影响试算。
  - 试算 YAML 结构同 overlay，但顶层仅允许 `fields`、字段条目仅允许 `field` /
    `precision` / `rounding` / `range`；越界键复用既有契约错误码退出 2，
    值合法性仍走原 `build_contract` 链路。
  - 行数据与基线校验 / 换算结果只读取一次；每份试算独立合并、独立生成不可变契约与
    校验结果，不共享可变状态；温度偏移仍走既有 affine converters。
  - 按字段输出 `pass_to_violation`、`violation_to_pass`、`rounding_text_only`
    （仅渲染文本变化且仍合规，单列）与 `unchanged` 四类行桶，行号可截断但保留总数，
    并附改动前后值与错误码计数。
  - 支持多份试算按顺序逐份叠加到同一基线，各自落盘 JSON 摘要并汇总
    `sensitivity_summary.json` 总表；同一试算重复运行逐字节一致。
  - 退出码沿用既有约定：成功 0、契约 / 数据错误 2、试算发现受影响行 1。
  - 既有 `validate` / `convert` / `report`、错误码与报告字段保持不变。

## v0.1.0 — 初始版本

### 已实现

- 声明式 YAML 量纲契约：字段、量纲、规范单位、允许单位、精度、舍入模式、物理范围。
- 契约 schema 严格校验，稳定错误码并定位到文件、字段和下标。
- 精确有理数单位注册表（`fractions.Fraction`），覆盖长度、质量、能量、功率、时间、体积、计数与温度。
- 独立的 C/K/F 偏移换算路径，含绝对零度校验；温度范围在规范单位下判断。
- CSV / JSONL 逐行校验：缺失、非数值、单位缺失、单位未知、量纲不匹配、单位不允许、越界、精度警告。
- 确定性换算、三种舍入模式与规范化 JSONL 输出。
- 差异归因审计：错误码目录、分类计数、字段 Top-N、通过/违规行统计。
- SQLite 台账（`runs`、`findings`），记录契约 SHA-256、数据 SHA-256 和报告目录，支持按 run_id 查询。
- 稳定 JSON、Markdown、自包含 HTML 三份报告；报告无脚本、无外部链接、无远程样式。
- 本地 Flask 只读服务，仅监听 loopback；首页、运行列表、运行详情和报告下载。
- `demo`、`validate`、`convert`、`report`、`serve` 五个子命令。
- 240 行确定性合成演示数据、18 行回归 fixture、golden JSON 片段与 Markdown 报告。
- 离线 pytest 用例覆盖 schema、注册表、温度、校验、换算、审计、台账、报告、CLI 与服务冒烟。

### 明确不实现

- 复合单位与派生量纲自动推导。
- 货币、汇率与金融口径。
- 时区语义换算。
- 跨文件 schema 推断与自动补契约。
- 流式/增量处理、消息队列、分布式执行与调度。
- 用户体系、鉴权、多租户、云端存储和任何联网能力。
- 图形化契约编辑器。
- 百万行以上数据集的专门性能优化。
