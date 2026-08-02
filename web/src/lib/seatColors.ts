const SEAT_COLORS = [
  { dot: "bg-indigo-500", text: "text-indigo-400", border: "border-indigo-500/30", bg: "bg-indigo-500/5" },
  { dot: "bg-purple-500", text: "text-purple-400", border: "border-purple-500/30", bg: "bg-purple-500/5" },
  { dot: "bg-cyan-500", text: "text-cyan-400", border: "border-cyan-500/30", bg: "bg-cyan-500/5" },
  { dot: "bg-emerald-500", text: "text-emerald-400", border: "border-emerald-500/30", bg: "bg-emerald-500/5" },
  { dot: "bg-amber-400", text: "text-amber-400", border: "border-amber-400/30", bg: "bg-amber-400/5" },
  { dot: "bg-rose-400", text: "text-rose-400", border: "border-rose-400/30", bg: "bg-rose-400/5" },
];

export function getSeatColor(seatIndex: number) {
  return SEAT_COLORS[seatIndex % SEAT_COLORS.length];
}
