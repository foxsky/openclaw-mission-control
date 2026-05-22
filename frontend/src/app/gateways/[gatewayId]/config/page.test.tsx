import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/generated/gateways/gateways", () => ({
  useGatewayConfigLookup: vi.fn(() => ({
    data: {
      status: 200,
      data: {
        gateway_id: "gw-1",
        path: "agents.defaults.models",
        schema: { type: "object" },
        reloadKind: "restart",
        hint: "Restart required.",
        hintPath: "agents.defaults.models",
        children: [
          { path: "agents.defaults.models.foo", reloadKind: "hot" },
        ],
      },
      headers: new Headers(),
    },
    isLoading: false,
    error: null,
  })),
}));

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: replaceMock }),
  useParams: () => ({ gatewayId: "gw-1" }),
  useSearchParams: () => new URLSearchParams("path=agents.defaults.models"),
  usePathname: () => "/gateways/gw-1/config",
}));

vi.mock("@/auth/clerk", () => ({
  useAuth: () => ({ isSignedIn: true }),
}));

vi.mock("@/lib/use-organization-membership", () => ({
  useOrganizationMembership: () => ({ isAdmin: true }),
}));

// The page renders inside DashboardPageLayout, which pulls in DashboardShell,
// DashboardSidebar, UserMenu, OrgSwitcher, etc. — far more than this inspector
// test needs. Stub the layout down to children + headerActions so we exercise
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

import GatewayConfigPage from "./page";

function renderPage() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <GatewayConfigPage />
    </QueryClientProvider>,
  );
}

describe("GatewayConfigPage", () => {
  it("renders the badge for the current path", () => {
    renderPage();
    expect(screen.getByText("Restart required")).toBeInTheDocument();
  });

  it("renders child rows with their own badges", () => {
    renderPage();
    expect(
      screen.getByText("agents.defaults.models.foo"),
    ).toBeInTheDocument();
    expect(screen.getByText("Hot reload")).toBeInTheDocument();
  });

  it("clicking a child row navigates with new ?path query", async () => {
    renderPage();
    await userEvent.click(screen.getByText("agents.defaults.models.foo"));
    await waitFor(() => {
      expect(replaceMock).toHaveBeenCalledWith(
        expect.stringContaining("path=agents.defaults.models.foo"),
        expect.objectContaining({ scroll: false }),
      );
    });
  });
});
