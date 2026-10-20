# V0-01 原件持久化与 Document 不可变版本

## 目标与用户价值

让同一资料的原始字节、来源和内容版本可可靠保存并长期引用。

## 背景与依据

阶段：V0。归属契约：`docs/data.md`, `docs/validation.md`。代码现状见[基线](../../../10-20-stocks-roadmap/research/baseline.md)，现有模型/helper 不等于业务闭环。

## 前置依赖

无业务任务前置；可先行细化。

依赖是实施顺序，不由 Trellis 父子关系自动执行。跨阶段依赖先通过前一阶段验收。

## 范围与要求

- 统一采集输入包含来源、Publisher、URL、真实时间、MIME、原始字节及完整性；未知字段留空。
- (source_id, dedupe_key) 是资料身份，每个内容版本有独立 document_id；不能继续使用现有二字段唯一约束禁止多版本。
- S3 原件可靠保存后再登记 Document；同原始字节复用当前版本，字节变化生成新版本，单纯更新时间变化不增版。
- 失败保留已保存证据与错误；版本不可原位覆盖，开发模型与初始化定义同步修改。

## 验收条件

- [x] AC-1: 同资料重复写入原始字节不增版，修改字节保留两个可读取版本；仅更新时间改变不增版。已由 `tests/test_repository.py` 验证。
- [x] AC-2: S3 写失败不产生虚假的可用 Document；时间、哈希、MIME、完整性及原件引用可核对。已由 Repository/S3 测试验证。
- [x] AC-3: 全新 PostgreSQL 初始化成功且 alembic check 与当前模型一致。已在远程隔离 pgvector 空库验证。

## 不在范围内

采集 Connector、Fact、增量研究水位、历史库迁移或兼容层。

## 约束与规划状态

只改当前设计与初始化，不做旧库迁移或旧版兼容；运行期证据和历史引用仍须保留。本任务已实现并归档，结果见 [acceptance.md](acceptance.md)。2026-10-20 归档审查发现最近内容指针与失败重试等缺陷，修复归属 [V0-08](../../10-20-v0-08-document-collection-fixes/prd.md)（含 `get_latest_document()` 语义澄清）；原验收记录保留当时的实际边界，不补写未执行的检查。
