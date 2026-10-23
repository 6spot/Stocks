# V0-01 原始文档版本验收记录

- 验收日期：2026-10-20（原始验收）
- 后续修正：2026-10-21 由 [V0-08](../10-20-v0-08-document-collection-fixes/prd.md) 修复两处缺陷，
  见文末「归档审查后的修正」。

## 结论

AC-1、AC-2、AC-3 通过。验证由远程隔离 PostgreSQL + pgvector 空库上的
`tests/test_repository.py` 与对象存储固定测试替身完成。

## AC-1：原始字节版本语义

结果：通过。

同一资料重复写入相同字节不增版；字节变化时保留两个可读取的版本；只更新元数据时间
不增版。由 `tests/test_repository.py` 覆盖，写入路径为 `Document` 内容版本表与
`content_hash` 比对。

## AC-2：写失败与原件可核对

结果：通过。

S3 写入失败时不产生虚假的可用 Document；时间、哈希、MIME、字节完整性与原件引用
可逐项核对。由 Repository 与 S3 测试覆盖；本次使用固定对象存储替身，未使用真实
bucket。

## AC-3：全新库初始化

结果：通过。

在远程隔离 pgvector 空库执行 `alembic upgrade head` 成功，`alembic check` 与当前模型
一致，无待生成操作。

## 未执行与限制

- 未接入真实对象存储；S3 行为由固定替身验证。
- 未执行真实外部采集与研究闭环，该闭环属 V0-07 AC-1，登记为 PA-1。

## 归档审查后的修正

2026-10-20 的归档审查在后续核对中发现本任务遗留两处缺陷，已由
[V0-08](../10-20-v0-08-document-collection-fixes/prd.md) 修复，本记录保留当时的实际
验证边界，不追改为「当时即正确」：

1. 最近内容指针按最大版本号读取，而非按写入顺序；
2. 部分内容重试依赖时间字段判断。

两处修复后的验证与回归场景记录在 V0-08 的
[acceptance.md](../10-20-v0-08-document-collection-fixes/acceptance.md)。
