import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock all the data-fetching hooks the page uses; we only assert on the Config link.
vi.mock("next/navigation", () => ({
  useParams: () => ({ gatewayId: "gw-1" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
  }),
  usePathname: () => "/gateways/gw-1",
}));

vi.mock("@/auth/clerk", () => ({
  useAuth: () => ({ isSignedIn: true }),
}));

// The page renders inside DashboardPageLayout, which pulls in DashboardShell,
// DashboardSidebar, UserMenu, OrgSwitcher, etc. — far more than this nav test
// needs. Stub the layout down to just children + headerActions so we exercise
// only the page's own JSX.
vi.mock("@/components/templates/DashboardPageLayout", () => ({
  DashboardPageLayout: ({
    headerActions,
    children,
  }: {
    headerActions?: ReactNode;
    children: ReactNode;
  }) => (
    <div>
      <div data-testid="header-actions">{headerActions}</div>
      <div>{children}</div>
    </div>
  ),
}));

vi.mock("@/lib/use-organization-membership", () => ({
  useOrganizationMembership: () => ({ isAdmin: true }),
}));

const baseQuery = {
  data: undefined,
  isLoading: false,
  error: null,
};

vi.mock("@/api/generated/gateways/gateways", () => ({
  useGetGatewayApiV1GatewaysGatewayIdGet: vi.fn(() => baseQuery),
  useGatewaysStatusApiV1GatewaysStatusGet: vi.fn(() => baseQuery),
}));

vi.mock("@/api/generated/boards/boards", () => ({
  useListBoardsApiV1BoardsGet: vi.fn(() => baseQuery),
}));

vi.mock("@/api/generated/agents/agents", () => ({
  useListAgentsApiV1AgentsGet: vi.fn(() => baseQuery),
  useDeleteAgentApiV1AgentsAgentIdDelete: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  getListAgentsApiV1AgentsGetQueryKey: vi.fn(() => ["list-agents"]),
}));

import GatewayDetailPage from "./page";

function renderPage() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <GatewayDetailPage />
    </QueryClientProvider>,
  );
}

describe("GatewayDetailPage", () => {
  it("renders a Config button/link that navigates to /gateways/<id>/config", () => {
    renderPage();
    const configEl = screen.getByRole("button", { name: /^config$/i });
    expect(configEl).toBeInTheDocument();
  });

  it("renders a Pairings button that navigates to /gateways/<id>/pairings", () => {
    renderPage();
    const link = screen.getByRole("button", { name: /^pairings$/i });
    expect(link).toBeInTheDocument();
  });
});
