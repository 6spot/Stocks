export type StatusTone =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "success"
  | "warning"
  | "info"
  | "ghost";

const labels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已结束",
  pending: "待处理",
  completed: "已完成",
  skipped: "已跳过",
  unsupported: "不支持",
  missing: "未创建",
  no_attempt: "尚未尝试",
  in_progress: "采集中",
  success: "成功",
  no_update: "无更新",
  partial: "部分完成",
  source_failure: "来源故障",
  fresh: "新鲜",
  stale: "已过期",
  no_content: "无内容",
  unknown_content_time: "披露时间未知",
  unconfigured: "未配置新鲜度",
  current: "当前",
  accepted: "已排队",
  rejected: "未接受",
  complete: "完整",
  partial_content: "部分内容",
};

function normalizedStatus(status: string | null | undefined): string {
  return (status ?? "unknown").trim();
}

export function statusLabel(status: string | null | undefined): string {
  const normalized = normalizedStatus(status);
  return labels[normalized] ?? normalized.replaceAll("_", " ");
}

export function statusTone(
  status: string | null | undefined,
): StatusTone {
  const normalized = normalizedStatus(status);
  if (
    ["succeeded", "success", "fresh", "completed", "current"].includes(
      normalized,
    )
  ) {
    return "success";
  }
  if (
    [
      "failed",
      "source_failure",
      "unsupported",
      "unconfigured",
    ].includes(normalized)
  ) {
    return "destructive";
  }
  if (
    [
      "running",
      "in_progress",
      "partial",
      "partial_content",
      "pending",
      "queued",
      "stale",
      "unknown_content_time",
    ].includes(normalized)
  ) {
    return "warning";
  }
  if (["no_update", "no_content", "skipped"].includes(normalized)) {
    return "info";
  }
  return "outline";
}

export function trustTierLabel(tier: string | null | undefined): string {
  const normalized = (tier ?? "").trim();
  return (
    {
      T1: "T1 · 官方原始",
      T2: "T2 · 原始发布者",
      T3: "T3 · 二手媒体",
      T4: "T4 · 未核实",
    }[normalized] ?? (normalized || "未记录")
  );
}

export function stageLabel(stage: string): string {
  return (
    {
      parse: "解析",
      classify: "分类",
      facts: "事实抽取",
      index: "向量索引",
    }[stage] ?? stage
  );
}

export function gapLabel(gap: string): string {
  return (
    {
      document_not_stored: "报告引用的 Document 版本已不在仓库中",
      chunk_unavailable: "报告引用的 Chunk 无法读取",
      partial_source_content: "引用来源只采集到部分内容",
      snapshot_storage_unconfigured: "服务器未配置原件存储",
      chunk_not_in_current_parse_batch:
        "该 Chunk 不属于当前成功解析批次",
      chunk_not_in_current_index: "该 Chunk 不在当前向量索引中",
      run_not_succeeded: "生成该报告的研究 Run 未成功结束",
      no_background_matches: "没有找到可用的背景资料",
    }[gap] ?? gap.replaceAll("_", " ")
  );
}
