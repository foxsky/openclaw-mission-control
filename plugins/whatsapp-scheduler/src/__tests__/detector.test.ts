import {
  analyzeMessage,
  detectIntent,
  extractDateTime,
  extractTime,
  generateConfirmationMessage,
  getRecurrenceRule,
} from "../detector";

describe("detector", () => {
  test("detects intent keywords", () => {
    const intent = detectIntent("Vamos marcar uma reunião amanhã");
    expect(intent.hasIntent).toBe(true);
    expect(intent.keywords.length).toBeGreaterThan(0);
  });

  test("extracts time from portuguese pattern", () => {
    const time = extractTime("Reunião às 10h30");
    expect(time).not.toBeNull();
    expect(time?.hour).toBe(10);
    expect(time?.minute).toBe(30);
  });

  test("extracts datetime with time only assumes today", () => {
    const dt = extractDateTime("às 15");
    expect(dt.time).not.toBeNull();
    expect(dt.dateStr).toBe("hoje");
  });

  test("generates confirmation message", () => {
    const result = analyzeMessage("Vamos marcar amanhã às 10", "Miguel");
    const message = generateConfirmationMessage(result, "Miguel");
    expect(message).toContain("Confirma o agendamento");
    expect(message).toContain("Reunião com Miguel");
  });

  test("recurrence rule detection", () => {
    const rule = getRecurrenceRule("Reunião toda segunda");
    expect(rule).toBe("RRULE:FREQ=WEEKLY;BYDAY=MO");
  });
});
