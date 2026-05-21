import { clsx } from "clsx";

type KnownKind = "restart" | "hot" | "none";

const KNOWN: Record<KnownKind, { label: string; className: string }> = {
  restart: {
    label: "Restart required",
    className: "bg-red-100 text-red-900 border border-red-200",
  },
  hot: {
    label: "Hot reload",
    className: "bg-emerald-100 text-emerald-900 border border-emerald-200",
  },
  none: {
    label: "No-op",
    className: "bg-zinc-100 text-zinc-700 border border-zinc-200",
  },
};

export interface ConfigReloadKindBadgeProps {
  reloadKind: string | null | undefined;
  className?: string;
}

export function ConfigReloadKindBadge({
  reloadKind,
  className,
}: ConfigReloadKindBadgeProps) {
  if (reloadKind === null || reloadKind === undefined) {
    return (
      <span
        className={clsx(
          "inline-flex items-center rounded px-2 py-0.5 text-xs text-muted",
          className,
        )}
        title="Gateway didn't report restart impact for this path."
      >
        —
      </span>
    );
  }

  const known = KNOWN[reloadKind as KnownKind];
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        known?.className ?? "bg-zinc-100 text-zinc-700 border border-zinc-200",
        className,
      )}
    >
      {known?.label ?? reloadKind}
    </span>
  );
}
