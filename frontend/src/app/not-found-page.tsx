import { Link } from "react-router-dom";
import { EmptyPanel } from "@/components/async-state";
import { buttonVariants } from "@/components/ui/button";

export function NotFoundPage() {
  return (
    <EmptyPanel
      title="页面不存在"
      description="这个地址不属于当前控制台。"
      action={
        <Link to="/" className={buttonVariants({ variant: "outline" })}>
          返回概览
        </Link>
      }
    />
  );
}
