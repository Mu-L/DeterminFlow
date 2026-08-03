import { useId, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { formatTechnicalValue, isLongTechnicalValue } from "./conversationModel";
import CopyButton from "./CopyButton";

interface TechnicalDisclosureProps {
  label: string;
  value: string;
  tone?: "neutral" | "error";
  emptyLabel?: string;
}

export default function TechnicalDisclosure({
  label,
  value,
  tone = "neutral",
  emptyLabel = "空结果",
}: TechnicalDisclosureProps) {
  const contentId = useId();
  const formatted = useMemo(() => formatTechnicalValue(value), [value]);
  const collapsible = isLongTechnicalValue(formatted);
  const [expanded, setExpanded] = useState(false);
  const visibleValue = formatted || `(${emptyLabel})`;
  const textColor = tone === "error" ? "text-red-300" : "text-slate-400";

  return (
    <section className="mt-2" aria-label={label}>
      <div className="mb-1 flex min-h-8 items-center gap-1">
        {collapsible ? (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            aria-controls={contentId}
            className="inline-flex min-h-8 items-center gap-1 rounded text-xs text-slate-500 transition-colors hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50"
          >
            {expanded ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
            <span>{label}</span>
          </button>
        ) : (
          <span className="text-xs text-slate-500">{label}</span>
        )}
        <CopyButton value={value} label={label} className="ml-auto" />
      </div>
      <pre
        id={contentId}
        className={`overflow-x-auto whitespace-pre-wrap break-words rounded bg-slate-950/70 p-2 text-xs leading-relaxed ${textColor} ${collapsible && !expanded ? "max-h-20 overflow-y-hidden [mask-image:linear-gradient(to_bottom,black_55%,transparent)]" : "max-h-80 overflow-y-auto"}`}
      >
        {visibleValue}
      </pre>
    </section>
  );
}
