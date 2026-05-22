"use client";

import { Suspense } from "react";
import {
  useParams,
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation";

import { ConfigReloadKindBadge } from "@/components/ConfigReloadKindBadge";
import { useGatewayConfigLookup } from "@/api/generated/gateways/gateways";
import type { ConfigSchemaLookupResponse } from "@/api/generated/model";

export const dynamic = "force-dynamic";

function Inner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();
  const gatewayIdParam = params?.gatewayId;
  const gatewayId = Array.isArray(gatewayIdParam)
    ? gatewayIdParam[0]
    : (gatewayIdParam ?? "");

  const path = searchParams.get("path") ?? ".";

  const queryResult = useGatewayConfigLookup(gatewayId, { path });

  if (!gatewayId) {
    return <div className="text-sm text-muted">Missing gateway id.</div>;
  }

  const { data, isLoading, error } = queryResult;

  // The orval-generated hook returns the customFetch envelope
  // `{ data: ConfigSchemaLookupResponse; status: number; headers: Headers }`.
  const lookup = resolveLookup(data);

  const goTo = (nextPath: string) => {
    const next = new URLSearchParams(searchParams.toString());
    next.set("path", nextPath);
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  };

  if (isLoading) {
    return <div className="text-sm text-muted">Loading…</div>;
  }
  if (error) {
    return <ErrorPanel error={error} />;
  }
  if (!lookup) {
    return <div className="text-sm text-muted">No data.</div>;
  }

  const children = lookup.children ?? [];

  return (
    <div className="flex flex-col gap-4">
      <header className="flex items-center justify-between">
        <Breadcrumbs path={lookup.path} onJump={goTo} />
        <ConfigReloadKindBadge reloadKind={lookup.reloadKind ?? null} />
      </header>

      <section className="grid grid-cols-2 gap-4">
        <div className="rounded border border-[color:var(--border)] p-3 text-sm">
          <h3 className="font-medium">Schema</h3>
          <pre className="mt-2 overflow-auto text-xs">
            {JSON.stringify(lookup.schema, null, 2)}
          </pre>
        </div>
        <div className="rounded border border-[color:var(--border)] p-3 text-sm">
          <h3 className="font-medium">Hint</h3>
          <p className="mt-2 text-muted">{lookup.hint ?? "—"}</p>
        </div>
      </section>

      <section>
        <h3 className="font-medium">Children ({children.length})</h3>
        <ul className="mt-2 divide-y divide-[color:var(--border)]">
          {children.map((child) => (
            <li key={child.path}>
              <button
                type="button"
                onClick={() => goTo(child.path)}
                className="flex w-full cursor-pointer items-center justify-between py-2 text-left hover:bg-slate-50"
              >
                <span className="text-sm">{child.path}</span>
                <ConfigReloadKindBadge reloadKind={child.reloadKind ?? null} />
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

type LookupEnvelope = {
  status: number;
  data: unknown;
};

function resolveLookup(
  envelope: LookupEnvelope | undefined | null,
): ConfigSchemaLookupResponse | null {
  if (!envelope) return null;
  if (envelope.status === 200 && envelope.data) {
    return envelope.data as ConfigSchemaLookupResponse;
  }
  return null;
}

function Breadcrumbs({
  path,
  onJump,
}: {
  path: string;
  onJump: (p: string) => void;
}) {
  const segments = path === "." ? ["."] : ["."].concat(path.split("."));
  return (
    <nav className="flex items-center gap-1 text-sm">
      {segments.map((seg, i) => {
        const target = i === 0 ? "." : segments.slice(1, i + 1).join(".");
        return (
          <span key={target} className="flex items-center gap-1">
            <button className="hover:underline" onClick={() => onJump(target)}>
              {seg}
            </button>
            {i < segments.length - 1 && <span className="text-muted">›</span>}
          </span>
        );
      })}
    </nav>
  );
}

function ErrorPanel({ error }: { error: unknown }) {
  const status = (error as { response?: { status?: number }; status?: number })
    ?.response?.status ?? (error as { status?: number })?.status;
  if (status === 400) {
    return <div className="text-sm text-red-700">Invalid path.</div>;
  }
  if (status === 404) {
    return (
      <div className="text-sm text-muted">
        Path not found in current gateway schema.
      </div>
    );
  }
  if (status === 501) {
    return (
      <div className="text-sm text-amber-800">
        This gateway is too old. Upgrade to OpenClaw 2026.5.19 to use the
        schema lookup.
      </div>
    );
  }
  return <div className="text-sm text-red-700">Gateway unreachable.</div>;
}

export default function GatewayConfigPage() {
  return (
    <Suspense fallback={<div className="text-sm text-muted">Loading…</div>}>
      <Inner />
    </Suspense>
  );
}
