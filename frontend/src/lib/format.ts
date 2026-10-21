const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
});

const numberFormatter = new Intl.NumberFormat("zh-CN", {
  maximumFractionDigits: 2,
});

const costFormatter = new Intl.NumberFormat("en-US", {
  // Significant digits keep a tiny non-zero cost visible instead of rounding
  // it to a misleading "$0".
  maximumSignificantDigits: 6,
});

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "未记录";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return dateTimeFormatter.format(date);
}

export function formatNumber(
  value: number | null | undefined,
  fallback = "未记录",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return fallback;
  }
  return numberFormatter.format(value);
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "未记录";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KiB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (
    milliseconds === null ||
    milliseconds === undefined ||
    !Number.isFinite(milliseconds)
  ) {
    return "未记录";
  }
  if (milliseconds < 1000) {
    return `${Math.round(milliseconds)} ms`;
  }
  return `${(milliseconds / 1000).toFixed(1)} s`;
}

/**
 * Provider-reported cost in USD. A missing cost stays "未知" so the console
 * never shows an unreported cost as a zero amount.
 */
export function formatCost(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "未知";
  }
  return `$${costFormatter.format(value)}`;
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "未记录";
  }
  if (typeof value === "string") {
    return value || "空字符串";
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "无法显示该值";
  }
}

export function shortId(value: string): string {
  return value.length > 12 ? value.slice(0, 8) : value;
}
