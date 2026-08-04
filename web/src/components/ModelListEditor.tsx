import { useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, X } from "lucide-react";

import { mergeUniqueModels } from "../lib/model-options";

interface Props {
  models: string[];
  onChange: (models: string[]) => void;
  inputLabel?: string;
}

function SortableModelChip({
  model,
  onRemove,
}: {
  model: string;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: model });

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`flex h-9 max-w-full items-center rounded-lg border pl-1 pr-1.5 text-sm transition-colors ${
        isDragging
          ? "z-10 border-indigo-400 bg-indigo-500/20 shadow-lg shadow-slate-950/40"
          : "border-slate-600/80 bg-slate-700/70 text-slate-200"
      }`}
    >
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={`拖动排序 ${model}`}
        className="flex h-8 w-7 shrink-0 cursor-grab items-center justify-center rounded-md text-slate-500 hover:bg-slate-600/70 hover:text-slate-300 active:cursor-grabbing"
      >
        <GripVertical size={14} aria-hidden="true" />
      </button>
      <span className="max-w-52 truncate font-mono text-xs">{model}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`移除模型 ${model}`}
        className="ml-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-red-500/10 hover:text-red-400"
      >
        <X size={13} aria-hidden="true" />
      </button>
    </div>
  );
}

export default function ModelListEditor({
  models,
  onChange,
  inputLabel = "输入模型",
}: Props) {
  const [draft, setDraft] = useState("");
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const addDraft = () => {
    const nextModels = mergeUniqueModels(models, [draft]);
    if (nextModels.length !== models.length) onChange(nextModels);
    setDraft("");
  };

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const oldIndex = models.indexOf(String(active.id));
    const newIndex = models.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    onChange(arrayMove(models, oldIndex, newIndex));
  };

  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={models} strategy={rectSortingStrategy}>
        <div className="flex min-h-14 w-full flex-wrap items-center gap-2 rounded-xl border border-slate-600 bg-slate-800/65 p-2 focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20">
          {models.map((model) => (
            <SortableModelChip
              key={model}
              model={model}
              onRemove={() => onChange(models.filter((item) => item !== model))}
            />
          ))}
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={addDraft}
            onKeyDown={(event) => {
              if (event.key !== "Enter" && event.key !== ",") return;
              event.preventDefault();
              addDraft();
            }}
            aria-label={inputLabel}
            placeholder={models.length === 0 ? "输入模型，按 Enter 添加" : "添加模型"}
            className="h-9 min-w-40 flex-1 bg-transparent px-2 font-mono text-sm text-slate-200 outline-none placeholder:text-slate-500"
          />
        </div>
      </SortableContext>
    </DndContext>
  );
}
