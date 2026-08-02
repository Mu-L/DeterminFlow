/**
 * ContextMenu - 右键上下文菜单
 *
 * 吸取 bk-sops ShortcutPanel 设计：
 * - 节点右键：配置 / 复制 / 断开连线 / 删除
 * - 连线右键：删除
 * - 点击画布空白处自动关闭
 */
import { useEffect, useRef } from "react";
import { Settings, Copy, Unlink, Trash2, Scissors } from "lucide-react";

interface ContextMenuProps {
  x: number;
  y: number;
  type: "node" | "edge";
  nodeId?: string;
  edgeId?: string;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  onClose: () => void;
}

interface MenuItem {
  action: string;
  label: string;
  icon: React.ReactNode;
  danger?: boolean;
  dividerAfter?: boolean;
}

export default function ContextMenu({
  x,
  y,
  type,
  nodeId,
  edgeId,
  onAction,
  onClose,
}: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  // Auto-focus first menu item on open + keyboard navigation
  useEffect(() => {
    const firstItem = menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]');
    firstItem?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (!menuRef.current) return;
      const menuItems = Array.from(menuRef.current.querySelectorAll<HTMLElement>('[role="menuitem"]'));
      const currentIdx = menuItems.indexOf(document.activeElement as HTMLElement);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        const next = currentIdx < menuItems.length - 1 ? currentIdx + 1 : 0;
        menuItems[next]?.focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const prev = currentIdx > 0 ? currentIdx - 1 : menuItems.length - 1;
        menuItems[prev]?.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Close on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [onClose]);

  // Adjust position to stay within viewport
  const adjustedX = Math.min(x, window.innerWidth - 180);
  const adjustedY = Math.min(y, window.innerHeight - 220);

  const nodeItems: MenuItem[] = [
    {
      action: "configure",
      label: "配置节点",
      icon: <Settings size={14} />,
    },
    {
      action: "copy-node",
      label: "复制节点",
      icon: <Copy size={14} />,
      dividerAfter: true,
    },
    {
      action: "disconnect-node",
      label: "断开所有连线",
      icon: <Unlink size={14} />,
      dividerAfter: true,
    },
    {
      action: "delete-node",
      label: "删除节点",
      icon: <Trash2 size={14} />,
      danger: true,
    },
  ];

  const edgeItems: MenuItem[] = [
    {
      action: "delete-edge",
      label: "删除连线",
      icon: <Scissors size={14} />,
      danger: true,
    },
  ];

  const items = type === "node" ? nodeItems : edgeItems;

  const handleClick = (item: MenuItem) => {
    const payload =
      type === "node"
        ? { nodeId }
        : { edgeId };
    onAction(item.action, payload);
  };

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[150px] rounded-lg bg-slate-900 border border-indigo-500/20 shadow-2xl shadow-black/40 overflow-hidden"
      style={{ left: adjustedX, top: adjustedY }}
      role="menu"
      aria-label={type === "node" ? "节点操作菜单" : "连线操作菜单"}
    >
      {items.map((item) => (
        <div key={item.action}>
          <button
            type="button"
            onClick={() => handleClick(item)}
            className={`flex items-center gap-2.5 w-full px-3 py-2.5 min-h-[44px] text-xs transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-indigo-500/30 focus-visible:outline-none cursor-pointer ${
              item.danger
                ? "text-red-500 hover:bg-red-500/10"
                : "text-slate-100 hover:bg-indigo-500/10"
            }`}
            role="menuitem"
            aria-label={item.label}
          >
            <span className="shrink-0" aria-hidden="true">{item.icon}</span>
            <span>{item.label}</span>
          </button>
          {item.dividerAfter && (
            <div className="border-t border-indigo-500/10 my-0.5" role="separator" />
          )}
        </div>
      ))}
    </div>
  );
}
