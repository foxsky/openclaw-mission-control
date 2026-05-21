import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConfigReloadKindBadge } from "./ConfigReloadKindBadge";

describe("ConfigReloadKindBadge", () => {
  it("renders 'Restart required' for restart kind", () => {
    render(<ConfigReloadKindBadge reloadKind="restart" />);
    expect(screen.getByText("Restart required")).toBeInTheDocument();
  });

  it("renders 'Hot reload' for hot kind", () => {
    render(<ConfigReloadKindBadge reloadKind="hot" />);
    expect(screen.getByText("Hot reload")).toBeInTheDocument();
  });

  it("renders 'No-op' for none kind", () => {
    render(<ConfigReloadKindBadge reloadKind="none" />);
    expect(screen.getByText("No-op")).toBeInTheDocument();
  });

  it("renders em dash with explanation tooltip for missing kind", () => {
    render(<ConfigReloadKindBadge reloadKind={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(
      screen.getByTitle("Gateway didn't report restart impact for this path."),
    ).toBeInTheDocument();
  });

  it("renders the raw kind label verbatim for an unknown value", () => {
    render(<ConfigReloadKindBadge reloadKind="warm-restart-future" />);
    expect(screen.getByText("warm-restart-future")).toBeInTheDocument();
  });
});
