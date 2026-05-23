"use client";

import { Suspense, useState } from "react";
import { useParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";

import { useAuth } from "@/auth/clerk";
import { useOrganizationMembership } from "@/lib/use-organization-membership";
import { DashboardPageLayout } from "@/components/templates/DashboardPageLayout";
import { Button } from "@/components/ui/button";
import { ConfirmActionDialog } from "@/components/ui/confirm-action-dialog";
import { formatTimestamp } from "@/lib/formatters";
import {
  getListGatewayDevicesQueryKey,
  useListGatewayDevices,
  useRemoveGatewayDevice,
} from "@/api/generated/gateways/gateways";

export const dynamic = "force-dynamic";

type Device = {
  deviceId: string;
  publicKey: string;
  clientId?: string | null;
  clientMode?: string | null;
  remoteIp?: string | null;
  scopes: string[];
  tokenCount: number;
  lastUsedAtMs: number | null;
  isSelf: boolean;
};

type ListBody = {
  gateway_id: string;
  isSelfResolved: boolean;
  devices: Device[];
};

function unwrap(data: unknown): ListBody | null {
  if (!data || typeof data !== "object") return null;
  const env = data as { status?: number; data?: unknown };
  if (env.status === 200 && env.data) {
    const body = env.data as Partial<ListBody>;
    return {
      gateway_id: body.gateway_id ?? "",
      isSelfResolved: Boolean(body.isSelfResolved),
      devices: (body.devices ?? []) as Device[],
    };
  }
  return null;
}

function Inner({ gatewayId }: { gatewayId: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useListGatewayDevices(gatewayId);
  const remove = useRemoveGatewayDevice();
  const [target, setTarget] = useState<Device | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);

  if (isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (error) return <div className="text-sm text-red-700">Failed to load.</div>;

  const lookup = unwrap(data);
  if (!lookup) return <div className="text-sm text-muted">No data.</div>;

  const writeBlocked = !lookup.isSelfResolved;
  const queryKey = getListGatewayDevicesQueryKey(gatewayId);

  const onConfirm = () => {
    if (!target) return;
    const deviceId = target.deviceId;
    setMutationError(null);
    remove.mutate(
      { gatewayId, deviceId },
      {
        onError: (err: unknown) => {
          const message =
            (err as { message?: string })?.message ?? "Remove failed.";
          setMutationError(message);
        },
        onSettled: () => {
          queryClient.invalidateQueries({ queryKey });
          setTarget(null);
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      {writeBlocked && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          MC could not verify its own device identity. Remove actions are
          disabled until this is resolved.
        </div>
      )}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-2">Client</th>
            <th>Remote IP</th>
            <th>Last used</th>
            <th>Scopes</th>
            <th>Device</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lookup.devices.map((d) => (
            <tr key={d.deviceId} className="border-t border-[color:var(--border)]">
              <td className="py-2">
                <span>{d.clientId ?? "—"}</span>
                <span className="text-muted"> / </span>
                <span>{d.clientMode ?? "—"}</span>
                {d.isSelf && (
                  <span className="ml-2 inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">
                    this is MC
                  </span>
                )}
              </td>
              <td>{d.remoteIp ?? "—"}</td>
              <td>
                {d.lastUsedAtMs
                  ? formatTimestamp(new Date(d.lastUsedAtMs).toISOString())
                  : "never"}
              </td>
              <td>
                <div className="flex flex-wrap gap-1">
                  {d.scopes.slice(0, 3).map((s) => (
                    <span
                      key={s}
                      className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700"
                    >
                      {s}
                    </span>
                  ))}
                  {d.scopes.length > 3 && (
                    <span
                      className="text-xs text-muted"
                      title={d.scopes.slice(3).join(", ")}
                    >
                      +{d.scopes.length - 3} more
                    </span>
                  )}
                </div>
              </td>
              <td className="font-mono text-xs">{d.deviceId.slice(0, 12)}…</td>
              <td>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={d.isSelf || writeBlocked || remove.isPending}
                  title={
                    d.isSelf
                      ? "This is MC's own backend device — removing would lock MC out of the gateway."
                      : undefined
                  }
                  onClick={() => setTarget(d)}
                >
                  Remove
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <ConfirmActionDialog
        open={Boolean(target)}
        onOpenChange={(open) => {
          if (!open) {
            setTarget(null);
            setMutationError(null);
          }
        }}
        title="Remove paired device?"
        description={
          target
            ? `Remove paired device ${target.deviceId.slice(0, 12)}…? The device will lose gateway access immediately. This cannot be undone.`
            : ""
        }
        onConfirm={onConfirm}
        isConfirming={remove.isPending}
        confirmLabel="Remove"
        confirmingLabel="Removing…"
        errorMessage={mutationError}
      />
    </div>
  );
}

export default function GatewayPairingsPage() {
  const { isSignedIn } = useAuth();
  const { isAdmin } = useOrganizationMembership(isSignedIn);
  const params = useParams();
  const gatewayIdParam = params?.gatewayId;
  const gatewayId = Array.isArray(gatewayIdParam)
    ? gatewayIdParam[0]
    : (gatewayIdParam ?? "");

  return (
    <DashboardPageLayout
      title="Gateway pairings"
      description="Inspect and revoke devices paired with this gateway."
      isAdmin={isAdmin}
      adminOnlyMessage="Only organization owners and admins can manage gateway pairings."
      signedOut={{
        message: "Sign in to manage gateway pairings.",
        forceRedirectUrl: `/gateways/${gatewayId}/pairings`,
      }}
    >
      <Suspense fallback={<div className="text-sm text-muted">Loading…</div>}>
        {gatewayId
          ? <Inner gatewayId={gatewayId} />
          : <div className="text-sm text-muted">Missing gateway id.</div>}
      </Suspense>
    </DashboardPageLayout>
  );
}
