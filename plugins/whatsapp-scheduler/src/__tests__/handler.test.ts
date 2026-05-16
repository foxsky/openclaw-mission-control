import { handleMessage, type SchedulerContext } from "../handler";

jest.mock("../calendar", () => ({
  createCalendarEvent: jest.fn().mockResolvedValue({ status: "success" }),
}));

const baseConfig = {
  calendarId: "calendar-id",
  timezone: "-03:00",
  gogAccount: "acct",
  gogKeyringPassword: "pwd",
  defaultLocation: "Prodater",
};

describe("handler", () => {
  test("returns no_intent for unrelated message", async () => {
    const result = await handleMessage("Olá!", "Ana", null, baseConfig);
    expect(result.action).toBe("no_intent");
  });

  test("asks for info when missing date/time", async () => {
    const result = await handleMessage("Vamos marcar uma reunião", "Ana", null, baseConfig);
    expect(result.action).toBe("need_info");
  });

  test("requests location after 14h without location", async () => {
    const result = await handleMessage("Reunião amanhã às 15", "Ana", null, baseConfig);
    expect(result.action).toBe("need_location");
  });

  test("schedules when confirmed", async () => {
    const pending: SchedulerContext = {
      title: "Reunião com Ana",
      start: new Date("2026-02-06T10:00:00Z"),
      location: "Prodater",
      sender: "Ana",
    };

    const result = await handleMessage("sim", "Ana", pending, baseConfig);
    expect(result.action).toBe("scheduled");
    expect(result.message).toContain("Agendamento confirmado");
  });
});
