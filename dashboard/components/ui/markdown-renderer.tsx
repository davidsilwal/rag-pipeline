"use client";

import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

interface MarkdownRendererProps {
  content: string;
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <div className="prose prose-zinc dark:prose-invert max-w-none">
      <ReactMarkdown
        components={{
          code(props) {
            const { children, className, ...rest } = props;
            const match = /language-(\w+)/.exec(className || "");
            const inline = !match && !String(children).includes("\n");
            if (inline) {
              return (
                <code
                  className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-sm font-mono"
                  {...rest}
                >
                  {children}
                </code>
              );
            }
            return (
              <SyntaxHighlighter
                style={oneDark}
                language={match ? match[1] : "text"}
                PreTag="div"
                className="rounded-lg !my-4"
              >
                {String(children).replace(/\n$/, "")}
              </SyntaxHighlighter>
            );
          },
          table({ children }) {
            return (
              <div className="overflow-x-auto my-4">
                <table className="min-w-full border border-zinc-200 dark:border-zinc-700">
                  {children}
                </table>
              </div>
            );
          },
          th({ children }) {
            return (
              <th className="px-3 py-2 text-left text-sm font-semibold bg-zinc-50 dark:bg-zinc-800 border-b border-zinc-200 dark:border-zinc-700">
                {children}
              </th>
            );
          },
          td({ children }) {
            return (
              <td className="px-3 py-2 text-sm border-b border-zinc-100 dark:border-zinc-800">
                {children}
              </td>
            );
          },
          h1({ children }) {
            return (
              <h1 className="text-3xl font-bold mt-8 mb-4 pb-2 border-b border-zinc-200 dark:border-zinc-800">
                {children}
              </h1>
            );
          },
          h2({ children }) {
            return (
              <h2 className="text-2xl font-semibold mt-6 mb-3">{children}</h2>
            );
          },
          h3({ children }) {
            return (
              <h3 className="text-xl font-semibold mt-4 mb-2">{children}</h3>
            );
          },
          a({ children, href }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-600 hover:text-indigo-800 underline"
              >
                {children}
              </a>
            );
          },
          blockquote({ children }) {
            return (
              <blockquote className="border-l-4 border-indigo-300 dark:border-indigo-700 pl-4 italic text-zinc-600 dark:text-zinc-400 my-4">
                {children}
              </blockquote>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
