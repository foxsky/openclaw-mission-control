/// <reference types="cypress" />

import { setupCommonPageTestHooks } from "../support/testHooks";

describe("/gateways/[id] - agents card collapse", () => {
  const apiBase = "**/api/v1";
  const gatewayId = "gw-test-1";
  const orgId = "org1";

  setupCommonPageTestHooks(apiBase, { organizationId: orgId });

  function stubAgents(count: number) {
    const items = Array.from({ length: count }, (_, i) => ({
      id: `agent-${i + 1}`,
      organization_id: orgId,
      gateway_id: gatewayId,
      board_id: null,
      identity_profile: null,
      identity_template: null,
      soul_template: null,
      heartbeat_config: null,
      assignee_id: null,
      role: "operator",
      timezone: "UTC",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      created_by: "u1",
      organization: orgId,
      created_by_id: "u1",
      gateway_agent_id: `gw-agent-${i + 1}`,
      name: `Agent ${i + 1}`,
      display_name: `Agent ${i + 1}`,
      status: "online",
      last_seen_at: "2026-05-01T00:00:00Z",
      board_name: null,
      gateway_name: "Test Gateway",
      gateway_url: "ws://localhost:18789",
      gateway_token_set: true,
      gateway_disable_device_pairing: false,
      gateway_allow_insecure_tls: false,
      board_role: null,
      identity_locked: false,
      assignee_email: null,
      template_drift_kind: null,
      heartbeat_directPolicy: null,
      effective_workspace_path: null,
    }));
    cy.intercept("GET", `${apiBase}/agents*gateway_id=${gatewayId}*`, {
      statusCode: 200,
      body: { items, total: count },
    }).as("agentsByGateway");
  }

  function stubGateway() {
    cy.intercept("GET", `${apiBase}/gateways/${gatewayId}`, {
      statusCode: 200,
      body: {
        id: gatewayId,
        organization_id: orgId,
        name: "Test Gateway",
        url: "ws://localhost:18789",
        token_set: true,
        disable_device_pairing: false,
        allow_insecure_tls: false,
        workspace_root: "/tmp",
        created_at: "2026-05-01T00:00:00Z",
        updated_at: "2026-05-01T00:00:00Z",
      },
    }).as("gateway");
    cy.intercept("GET", `${apiBase}/gateways/status*`, {
      statusCode: 200,
      body: { gateways: [] },
    }).as("gatewaysStatus");
    cy.intercept("GET", `${apiBase}/boards*`, {
      statusCode: 200,
      body: { items: [], total: 0 },
    }).as("boardsList");
  }

  function visit() {
    stubGateway();
    stubAgents(3);
    cy.loginWithLocalAuth();
    cy.visit(`/gateways/${gatewayId}`);
    cy.waitForAppLoaded();
  }

  it("starts expanded with the agents table visible and a chevron-down toggle", () => {
    visit();
    cy.get('[data-cy="gateway-agents-toggle"]').should("be.visible");
    cy.get('[data-cy="gateway-agents-toggle"]').should(
      "have.attr",
      "aria-expanded",
      "true",
    );
    cy.get("#gateway-agents-table").should("exist");
  });

  it("clicking the toggle hides the table but keeps the count visible", () => {
    visit();
    cy.get('[data-cy="gateway-agents-toggle"]').click();
    cy.get('[data-cy="gateway-agents-toggle"]').should(
      "have.attr",
      "aria-expanded",
      "false",
    );
    cy.get("#gateway-agents-table").should("not.exist");
    cy.get('[data-cy="gateway-agents-toggle"]').should("contain", "3 total");
  });

  it("collapsed state persists across reload", () => {
    visit();
    cy.get('[data-cy="gateway-agents-toggle"]').click();
    cy.reload();
    cy.waitForAppLoaded();
    cy.get('[data-cy="gateway-agents-toggle"]').should(
      "have.attr",
      "aria-expanded",
      "false",
    );
    cy.get("#gateway-agents-table").should("not.exist");
  });

  it("clicking again expands the table", () => {
    visit();
    cy.get('[data-cy="gateway-agents-toggle"]').click();
    cy.get('[data-cy="gateway-agents-toggle"]').click();
    cy.get('[data-cy="gateway-agents-toggle"]').should(
      "have.attr",
      "aria-expanded",
      "true",
    );
    cy.get("#gateway-agents-table").should("exist");
  });
});
