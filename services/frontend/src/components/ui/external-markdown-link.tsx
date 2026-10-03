import type { AnchorHTMLAttributes, ReactNode } from "react";
import { sanitizeHref } from "@/lib/content-sanitizer";
import { cn } from "@/lib/utils";

type ExternalMarkdownLinkProps = Omit<
  AnchorHTMLAttributes<HTMLAnchorElement>,
  "href"
> & {
  children?: ReactNode;
  href?: string | null;
  node?: unknown;
};

export function ExternalMarkdownLink({
  children,
  href,
  className,
  node: _node,
  ...props
}: ExternalMarkdownLinkProps) {
  const safeHref = sanitizeHref(href);

  if (!safeHref || safeHref === "#") {
    return (
      <span className={cn("break-words text-current", className)}>
        {children}
      </span>
    );
  }

  return (
    <a
      {...props}
      href={safeHref}
      target="_blank"
      rel="noopener noreferrer"
      className={cn("break-all underline-offset-2", className)}
    >
      {children}
    </a>
  );
}
