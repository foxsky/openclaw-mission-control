import React from "react";
import { describe, expect, it, vi, type Mock } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const baseEnvelope = (overrides: object = {}) => ({
  data: {
    status: 200,
    data: {
      gateway_id: "gw-1",
      isSelfResolved: true,
      devices: [
        {
          deviceId: "self-id",
          publicKey: "K1",
          clientId: "gateway-client",
          clientMode: "backend",
          remoteIp: "192.168.2.64",
          scopes: ["operator.admin", "operator.pairing"],
          tokenCount: 1,
          lastUsedAtMs: 1700000000000,
          isSelf: true,
        },
        {
          deviceId: "stale-cli-id",
          publicKey: "K2",
          clientId: "cli",
          clientMode: "cli",
          remoteIp: null,
          scopes: ["operator.admin"],
          tokenCount: 1,
          lastUsedAtMs: 1500000000000,
          isSelf: false,
        },
      ],
      ...overrides,
    },
    headers: new Headers(),
  },
  isLoading: false,
  error: null,
});

const useListMock = vi.fn(() => baseEnvelope());
const removeMutateMock = vi.fn();
const removeMutationState = { isPending: false };

vi.mock("@/api/generated/gateways/gateways", () => ({
  useListGatewayDevices: () => useListMock(),
  useRemoveGatewayDevice: () => ({
    mutate: removeMutateMock,
    mutateAsync: removeMutateMock,
    isPending: removeMutationState.isPending,
  }),
  getListGatewayDevicesQueryKey: (gatewayId: string) =>
    [`/api/v1/gateways/${gatewayId}/devices`] as const,
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ gatewayId: "gw-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/gateways/gw-1/pairings",
}));
vi.mock("@/auth/clerk", () => ({ useAuth: () => ({ isSignedIn: true }) }));
vi.mock("@/lib/use-organization-membership", () => ({
  useOrganizationMembership: () => ({ isAdmin: true }),
}));
vi.mock("@/components/templates/DashboardPageLayout", () => ({
  // Stub: render headerActions + children verbatim so the page renders bare.
  DashboardPageLayout: ({
    headerActions,
    children,
  }: { headerActions?: React.ReactNode; children: React.ReactNode }) => (
    <div>
      {headerActions}
      {children}
    </div>
  ),
}));

import GatewayPairingsPage from "./page";

function renderPage() {
  const client = new QueryClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <GatewayPairingsPage />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

describe("GatewayPairingsPage", () => {
  it("renders one row per device with client and remoteIp", () => {
    renderPage();
    // Client column shows clientId / clientMode — clientId and clientMode are
    // rendered as separate spans so each becomes its own text node. The cli
    // device has clientId=cli AND clientMode=cli, so /^cli$/ matches two spans.
    expect(screen.getByText(/gateway-client/)).toBeInTheDocument();
    expect(screen.getAllByText(/^cli$/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("192.168.2.64")).toBeInTheDocument();
  });

  it("disables Remove and labels the isSelf row", () => {
    renderPage();
    const buttons = screen.getAllByRole("button", { name: /remove/i });
    expect(buttons.length).toBe(2);
    const selfButton = buttons.find(
      (b) => b.getAttribute("title")?.includes("MC's own backend device"),
    );
    expect(selfButton).toBeDefined();
    expect(selfButton).toBeDisabled();
    expect(screen.getByText(/this is MC/i)).toBeInTheDocument();
  });

  it("clicking Remove on a non-self row opens the confirm dialog with truncated id", async () => {
    renderPage();
    const enabledButton = screen.getAllByRole("button", { name: /remove/i }).find(
      (b) => !b.hasAttribute("disabled"),
    )!;
    await userEvent.click(enabledButton);
    // Truncated form: "stale-cli-id".slice(0, 12) = "stale-cli-id" (already 12 chars).
    // Scope to the dialog so we don't collide with the Device column text.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/stale-cli-id/)).toBeInTheDocument();
  });

  it("confirming fires the delete mutation with the right vars", async () => {
    renderPage();
    const enabledButton = screen.getAllByRole("button", { name: /remove/i }).find(
      (b) => !b.hasAttribute("disabled"),
    )!;
    await userEvent.click(enabledButton);
    // The dialog renders a new "Remove" button after the row buttons in DOM order.
    // Use getAllByRole and pick the last one (dialog confirm).
    const removeButtons = await screen.findAllByRole("button", { name: "Remove" });
    const dialogConfirm = removeButtons[removeButtons.length - 1];
    await userEvent.click(dialogConfirm);
    await waitFor(() => {
      expect(removeMutateMock).toHaveBeenCalledWith(
        { gatewayId: "gw-1", deviceId: "stale-cli-id" },
        expect.any(Object),
      );
    });
  });

  it("isSelfResolved=false disables every Remove button and shows the banner", () => {
    (useListMock as Mock).mockReturnValueOnce(
      baseEnvelope({
        isSelfResolved: false,
        devices: [
          {
            deviceId: "x", publicKey: "K", clientId: "cli", clientMode: "cli",
            scopes: [], tokenCount: 0, lastUsedAtMs: null, isSelf: false,
            remoteIp: null,
          },
        ],
      }),
    );
    renderPage();
    expect(
      screen.getByText(/could not verify its own device identity/i),
    ).toBeInTheDocument();
    for (const b of screen.getAllByRole("button", { name: /^remove$/i })) {
      expect(b).toBeDisabled();
    }
  });

  it("after a 404 delete the list refetches and a toast is requested", async () => {
    // Configure the mutation mock to invoke its onSettled with a 404-like error.
    removeMutateMock.mockImplementationOnce(
      (_arg: unknown, opts: { onSettled?: () => void } = {}) => {
        opts.onSettled?.();
      },
    );
    const { client } = renderPage();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const enabledButton = screen.getAllByRole("button", { name: /remove/i }).find(
      (b) => !b.hasAttribute("disabled"),
    )!;
    await userEvent.click(enabledButton);
    const removeButtons = await screen.findAllByRole("button", { name: "Remove" });
    const dialogConfirm = removeButtons[removeButtons.length - 1];
    await userEvent.click(dialogConfirm);
    // onSettled fires regardless of success/error — the page calls
    // queryClient.invalidateQueries with the list query key.
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: [`/api/v1/gateways/gw-1/devices`],
      });
    });
  });
});
