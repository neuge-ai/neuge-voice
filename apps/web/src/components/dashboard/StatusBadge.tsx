import React from "react";

type StatusBadgeProps = {
  statusText: string;
  statusColor: string;
  onClick?: () => void;
};

export function StatusBadge({ statusText, statusColor, onClick }: StatusBadgeProps) {
  const dotClass =
    statusColor === "bg-onSurfaceVariant"
      ? "bg-white/30"
      : statusColor === "bg-primary"
        ? "bg-primary shadow-[0_0_14px_rgba(168,85,247,0.78)]"
        : "bg-[#00fbfb] shadow-[0_0_14px_rgba(0,251,251,0.78)]";

  return (
    <div className="absolute top-24 md:top-12 w-full flex justify-center z-20 cursor-pointer" onClick={onClick}>
      <div className="border border-white/10 bg-white/5 backdrop-blur-md rounded-full px-[14px] py-[10px] flex items-center gap-2.5 hover:bg-white/10 hover:border-white/20 hover:-translate-y-0.5 transition-all duration-300 shadow-[0_4px_12px_rgba(0,0,0,0.5)] group">
        <div className={`w-[7px] h-[7px] rounded-full transition-colors duration-300 ${dotClass}`}></div>
        <span className="font-label-sm text-[11px] tracking-[0.12em] uppercase text-white/70 group-hover:text-white transition-colors duration-300">
          {statusText}
        </span>
      </div>
    </div>
  );
}
