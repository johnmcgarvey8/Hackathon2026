"use client";

import {
  Children,
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { splitCitationReferences } from "@/lib/chat-markdown";
import type { ChatCitation } from "@/lib/types";

interface AssistantMarkdownProps {
  content: string;
  citations: ChatCitation[];
  onCitationClick: (citation: ChatCitation) => void;
}

function renderCitationReferences(
  children: ReactNode,
  citations: ChatCitation[],
  onCitationClick: (citation: ChatCitation) => void,
): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") {
      return splitCitationReferences(child, citations).map((segment, index) => (
        segment.kind === "text"
          ? segment.value
          : (
            <button
              className="markdown-citation"
              type="button"
              key={`${segment.citation.source_id}-${index}`}
              title={`Open source: ${segment.citation.title}`}
              aria-label={`Open source ${segment.citation.title}`}
              onClick={() => onCitationClick(segment.citation)}
            >
              {segment.value}
            </button>
          )
      ));
    }

    if (!isValidElement<{ children?: ReactNode; href?: string }>(child)) return child;
    if (child.type === "a" || child.type === "code" || child.type === "pre" || child.props.href) return child;
    if (child.props.children === undefined) return child;

    return cloneElement(
      child as ReactElement<{ children?: ReactNode }>,
      undefined,
      renderCitationReferences(child.props.children, citations, onCitationClick),
    );
  });
}

export function AssistantMarkdown({
  content,
  citations,
  onCitationClick,
}: AssistantMarkdownProps) {
  const citationContent = (children: ReactNode) => (
    renderCitationReferences(children, citations, onCitationClick)
  );

  const components = {
    p: ({ node, children, ...props }) => {
      void node;
      return <p {...props}>{citationContent(children)}</p>;
    },
    h1: ({ node, children, ...props }) => {
      void node;
      return <h1 {...props}>{citationContent(children)}</h1>;
    },
    h2: ({ node, children, ...props }) => {
      void node;
      return <h2 {...props}>{citationContent(children)}</h2>;
    },
    h3: ({ node, children, ...props }) => {
      void node;
      return <h3 {...props}>{citationContent(children)}</h3>;
    },
    h4: ({ node, children, ...props }) => {
      void node;
      return <h4 {...props}>{citationContent(children)}</h4>;
    },
    h5: ({ node, children, ...props }) => {
      void node;
      return <h5 {...props}>{citationContent(children)}</h5>;
    },
    h6: ({ node, children, ...props }) => {
      void node;
      return <h6 {...props}>{citationContent(children)}</h6>;
    },
    li: ({ node, children, ...props }) => {
      void node;
      return <li {...props}>{citationContent(children)}</li>;
    },
    td: ({ node, children, ...props }) => {
      void node;
      return <td {...props}>{citationContent(children)}</td>;
    },
    th: ({ node, children, ...props }) => {
      void node;
      return <th {...props}>{citationContent(children)}</th>;
    },
    a: ({ node, href, children, ...props }) => {
      void node;
      const external = Boolean(href && /^https?:\/\//i.test(href));
      return (
        <a
          {...props}
          href={href}
          target={external ? "_blank" : undefined}
          rel={external ? "noreferrer" : undefined}
        >
          {children}
        </a>
      );
    },
    table: ({ node, children, ...props }) => {
      void node;
      return <div className="markdown-table-scroll"><table {...props}>{children}</table></div>;
    },
  } satisfies Components;

  return (
    <div className="agent-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {content}
      </ReactMarkdown>
    </div>
  );
}
