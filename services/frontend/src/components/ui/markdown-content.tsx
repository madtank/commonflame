import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { ExternalMarkdownLink } from "@/components/ui/external-markdown-link";

interface MarkdownContentProps {
  content: string;
  className?: string;
  /** Compact mode reduces spacing for smaller contexts */
  compact?: boolean;
}

/**
 * Renders markdown content with proper styling for the Waystation platform.
 * Supports GitHub Flavored Markdown including:
 * - Headers, bold, italic, strikethrough
 * - Code blocks and inline code
 * - Lists (ordered, unordered, task lists)
 * - Tables
 * - Links
 * - Blockquotes
 */
export function MarkdownContent({
  content,
  className,
  compact = false,
}: MarkdownContentProps) {
  if (!content) return null;

  return (
    <div
      className={cn(
        "prose prose-sm dark:prose-invert max-w-none",
        // Reduce prose spacing in compact mode
        compact &&
          "prose-p:my-1 prose-headings:my-2 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5",
        // Ensure proper text colors
        "prose-headings:text-gray-800 dark:prose-headings:text-gray-200",
        "prose-p:text-gray-600 dark:prose-p:text-gray-400",
        "prose-strong:text-gray-700 dark:prose-strong:text-gray-300",
        "prose-li:text-gray-600 dark:prose-li:text-gray-400",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Style headings
          h1: ({ children, ...props }) => (
            <h1
              className="text-lg font-bold text-gray-800 dark:text-gray-200 mt-4 mb-2 first:mt-0"
              {...props}
            >
              {children}
            </h1>
          ),
          h2: ({ children, ...props }) => (
            <h2
              className="text-base font-bold text-gray-800 dark:text-gray-200 mt-3 mb-2 first:mt-0"
              {...props}
            >
              {children}
            </h2>
          ),
          h3: ({ children, ...props }) => (
            <h3
              className="text-sm font-semibold text-gray-700 dark:text-gray-300 mt-2 mb-1 first:mt-0"
              {...props}
            >
              {children}
            </h3>
          ),
          // Style code blocks
          code: ({ className, children, ...props }: any) => {
            const isInline = !className;
            return isInline ? (
              <code
                className="bg-gray-100 dark:bg-gray-800 px-1.5 py-0.5 rounded text-xs font-mono text-gray-800 dark:text-gray-200"
                {...props}
              >
                {children}
              </code>
            ) : (
              <code className={cn("text-xs", className)} {...props}>
                {children}
              </code>
            );
          },
          pre: ({ children, ...props }) => (
            <pre
              className="bg-gray-100 dark:bg-gray-800 p-3 rounded-md overflow-x-auto text-xs my-2"
              {...props}
            >
              {children}
            </pre>
          ),
          a: ({ children, href, ...props }) => {
            return (
              <ExternalMarkdownLink
                href={href}
                className="text-blue-600 dark:text-blue-400 hover:underline"
                {...props}
              >
                {children}
              </ExternalMarkdownLink>
            );
          },
          // Style blockquotes
          blockquote: ({ children, ...props }) => (
            <blockquote
              className="border-l-4 border-gray-300 dark:border-gray-600 pl-4 italic text-gray-600 dark:text-gray-400 my-2"
              {...props}
            >
              {children}
            </blockquote>
          ),
          // Style lists
          ul: ({ children, ...props }) => (
            <ul className="list-disc list-inside space-y-1 my-2" {...props}>
              {children}
            </ul>
          ),
          ol: ({ children, ...props }) => (
            <ol className="list-decimal list-inside space-y-1 my-2" {...props}>
              {children}
            </ol>
          ),
          li: ({ children, ...props }: any) => {
            // Handle task list items (checkboxes)
            const hasCheckbox =
              Array.isArray(children) &&
              children.some(
                (child: any) =>
                  typeof child === "object" &&
                  child?.type === "input" &&
                  child?.props?.type === "checkbox",
              );

            if (hasCheckbox) {
              return (
                <li className="list-none flex items-start gap-2" {...props}>
                  {children}
                </li>
              );
            }

            return (
              <li className="text-gray-600 dark:text-gray-400" {...props}>
                {children}
              </li>
            );
          },
          // Style checkboxes in task lists
          input: ({ type, checked, ...props }: any) => {
            if (type === "checkbox") {
              return (
                <input
                  type="checkbox"
                  checked={checked}
                  disabled
                  className="mt-1 h-4 w-4 rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
                  {...props}
                />
              );
            }
            return <input type={type} {...props} />;
          },
          // Style tables
          table: ({ children, ...props }) => (
            <div className="overflow-x-auto my-2">
              <table
                className="min-w-full border-collapse border border-gray-300 dark:border-gray-600 text-sm"
                {...props}
              >
                {children}
              </table>
            </div>
          ),
          thead: ({ children, ...props }) => (
            <thead className="bg-gray-100 dark:bg-gray-800" {...props}>
              {children}
            </thead>
          ),
          th: ({ children, ...props }) => (
            <th
              className="border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-left font-semibold text-gray-700 dark:text-gray-300"
              {...props}
            >
              {children}
            </th>
          ),
          td: ({ children, ...props }) => (
            <td
              className="border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-gray-600 dark:text-gray-400"
              {...props}
            >
              {children}
            </td>
          ),
          // Style horizontal rules
          hr: ({ ...props }) => (
            <hr
              className="border-gray-300 dark:border-gray-600 my-4"
              {...props}
            />
          ),
          // Style paragraphs
          p: ({ children, ...props }) => (
            <p className="my-2 first:mt-0 last:mb-0" {...props}>
              {children}
            </p>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
