import MarkdownRenderer from "../MarkdownRenderer";

export interface MarkdownContentProps {
  content: string;
  className?: string;
}

export default function MarkdownContent({ content, className = "" }: MarkdownContentProps) {
  if (!content) return null;
  return <MarkdownRenderer content={content} className={className} />;
}
