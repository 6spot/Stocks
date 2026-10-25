import { describe, expect, it } from "vitest";
import { renderMarkdown } from "@/lib/markdown";

describe("renderMarkdown", () => {
  it("renders headings and lists", () => {
    const html = renderMarkdown("## 结论\n\n- 要点一\n- 要点二\n");

    expect(html).toContain("<h2>结论</h2>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<li>要点一</li>");
    expect(html).toContain("<li>要点二</li>");
  });

  it("strips script tags and event handler attributes", () => {
    const html = renderMarkdown(
      '你好<script>window.alert("x")</script><img src="a.png" onerror="alert(1)">',
    );

    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).toContain("你好");
    expect(html).toContain('src="a.png"');
  });

  it("leaves plain text untouched", () => {
    const html = renderMarkdown("普通段落文本，没有 Markdown 结构。");
    expect(html).toContain("普通段落文本，没有 Markdown 结构。");
    expect(html).not.toMatch(/<(h\d|ul|ol|blockquote|code)/);
  });
});

it("neutralizes javascript: hrefs", () => {
  const html = renderMarkdown('[x](javascript:alert(1))');

  expect(html).not.toContain("javascript:");
});

it("strips style tags, onclick handlers and inline styles", () => {
  const html = renderMarkdown(
    '<style>.x{color:red}</style><p onclick="alert(1)" style="color:red">y</p>',
  );

  expect(html).not.toContain("<style");
  expect(html).not.toContain("onclick");
  expect(html).not.toContain("color:red");
});
