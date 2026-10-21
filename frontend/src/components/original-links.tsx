import { Download, ExternalLink, FileWarning } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { downloadEndpoint, originalEndpoint } from "@/api/dashboard";
import type { OriginalDescriptor } from "@/api/schemas";

export function OriginalLinks({
  original,
}: {
  original: OriginalDescriptor | null | undefined;
}) {
  if (!original?.stored) {
    return (
      <Alert>
        <FileWarning aria-hidden="true" />
        <AlertDescription>
          该引用保留了来源身份和采集时间，但对应原件未存入当前仓库。
        </AlertDescription>
      </Alert>
    );
  }

  if (!original.readable || !original.document_id) {
    return (
      <Alert>
        <FileWarning aria-hidden="true" />
        <AlertDescription>
          原件已记录在仓库索引中，但服务器未配置可读取的对象存储。
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      <a
        href={originalEndpoint(original.document_id)}
        target="_blank"
        rel="noreferrer"
        className={buttonVariants({ variant: "outline", size: "sm" })}
      >
        <ExternalLink aria-hidden="true" />
        查看安全原件
      </a>
      <a
        href={downloadEndpoint(original.document_id)}
        download
        className={buttonVariants({ variant: "ghost", size: "sm" })}
      >
        <Download aria-hidden="true" />
        下载原始字节
      </a>
    </div>
  );
}
