import { useMemo, type HTMLAttributes, type ReactNode } from "react";
import { Anchor, Spin, Typography } from "antd";
import type { AnchorLinkItemProps } from "antd/es/anchor/Anchor";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import MermaidBlock from "./MermaidBlock";

/** 页头 64 + 与其它页一致的标题条 56。 */
const TOC_OFFSET = 120;

function slugify(text: string) {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fff\- ]+/g, "")
    .replace(/\s+/g, "-");
}

function headingText(children: ReactNode): string {
  if (children == null || typeof children === "boolean") return "";
  if (typeof children === "string" || typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(headingText).join("");
  if (typeof children === "object" && "props" in (children as object)) {
    return headingText((children as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

function stripMd(title: string) {
  // 只去掉强调记号。下划线属于参数名（input_dir），不能删，否则和右侧标题 id 对不上。
  return title.replace(/[`*]/g, "").trim();
}

/** 左侧目录：二级=章/阶段，三级=阶段或参数，四级=参数名。 */
export function markdownToc(md: string): AnchorLinkItemProps[] {
  const items: AnchorLinkItemProps[] = [];
  let inFence = false;
  for (const raw of md.split("\n")) {
    if (raw.trim().startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^(#{2,4})\s+(.+)$/.exec(raw);
    if (!m) continue;
    const depth = m[1].length;
    const title = stripMd(m[2]);
    const id = slugify(title);
    const item: AnchorLinkItemProps = { key: `${depth}-${id}`, href: `#${id}`, title };
    if (depth === 2) {
      items.push(item);
      continue;
    }
    const h2 = items[items.length - 1];
    if (!h2) continue;
    const kids = (h2.children ??= []);
    if (depth === 3) {
      kids.push(item);
      continue;
    }
    const h3 = kids[kids.length - 1];
    if (h3) {
      (h3.children ??= []).push(item);
    } else {
      kids.push(item);
    }
  }
  return items;
}

function Heading({
  level,
  children,
  ...props
}: HTMLAttributes<HTMLHeadingElement> & { level: 2 | 3 | 4; node?: unknown }) {
  const Tag = `h${level}` as "h2" | "h3" | "h4";
  return (
    <Tag {...props} id={slugify(headingText(children))}>
      {children}
    </Tag>
  );
}

export default function MarkdownDoc({
  md,
  loading,
  intro,
}: {
  md: string;
  loading?: boolean;
  intro?: ReactNode;
}) {
  const body = useMemo(() => md.replace(/^#\s+[^\n]+\n+/, ""), [md]);
  const toc = useMemo(() => markdownToc(body), [body]);

  if (loading) {
    return (
      <div style={{ padding: 48, textAlign: "center" }}>
        <Spin />
      </div>
    );
  }

  return (
    <>
      {intro}
      <div className="mosaic-cli-doc-row">
        {/* 在列内 sticky。不要用 Affix：它会按整列高度 fixed，白底会盖住上方 Tab。 */}
        <div className="mosaic-cli-toc">
          <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 8 }}>
            目录
          </Typography.Text>
          <Anchor
            affix={false}
            targetOffset={TOC_OFFSET}
            items={toc}
            onClick={(event, link) => {
              const id = decodeURIComponent(link.href.replace(/^#/, ""));
              const target = document.getElementById(id);
              if (!target) return;
              event.preventDefault();
              const top = target.getBoundingClientRect().top + window.scrollY - TOC_OFFSET;
              window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
            }}
          />
        </div>
        <div className="mosaic-cli-doc-main">
          <div className="mosaic-markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                h2: ({ children, node: _node, ...props }) => (
                  <Heading level={2} {...props}>
                    {children}
                  </Heading>
                ),
                h3: ({ children, node: _node, ...props }) => (
                  <Heading level={3} {...props}>
                    {children}
                  </Heading>
                ),
                h4: ({ children, node: _node, ...props }) => (
                  <Heading level={4} {...props}>
                    {children}
                  </Heading>
                ),
                a: ({ href, children, ...props }) => (
                  <a href={href} target={href?.startsWith("http") ? "_blank" : undefined} rel="noreferrer" {...props}>
                    {children}
                  </a>
                ),
                pre: ({ children }) => <>{children}</>,
                code: ({ className, children, ...props }) => {
                  const lang = /language-(\w+)/.exec(className || "")?.[1];
                  const text = String(children).replace(/\n$/, "");
                  if (lang === "mermaid") return <MermaidBlock chart={text} />;
                  if (lang) {
                    return (
                      <pre>
                        <code className={className} {...props}>
                          {children}
                        </code>
                      </pre>
                    );
                  }
                  return (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {body}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    </>
  );
}
