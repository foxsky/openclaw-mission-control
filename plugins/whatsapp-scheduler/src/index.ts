import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";
import { handleMessage, type SchedulerContext } from "./handler.js";
import { deleteContext, getContext, initStore, setContext } from "./context-store.js";

export type PluginConfig = {
  calendarId?: string;
  defaultLocation?: string;
  timezone?: string;
  contextStorePath?: string;
  gogAccount?: string;
  gogKeyringPassword?: string;
};

type MessageReceivedEvent = {
  from: string;
  content: string;
  timestamp?: number;
  metadata?: {
    provider?: string;
    messageId?: string;
    senderId?: string;
    senderName?: string;
    senderE164?: string;
  };
};

type MessageReceivedCtx = {
  channelId?: string;
  accountId?: string;
  conversationId?: string;
};

const plugin = {
  id: "whatsapp-scheduler",
  name: "WhatsApp Scheduler",
  description: "Detects scheduling intent in WhatsApp messages and creates Google Calendar events",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    api.logger?.info?.("[whatsapp-scheduler] loading...");

    const config = api.pluginConfig as PluginConfig | undefined;
    const calendarId = config?.calendarId ?? process.env.CALENDAR_ID ?? "";
    const timezone = config?.timezone ?? process.env.TIMEZONE ?? "-03:00";
    const defaultLocation =
      config?.defaultLocation ?? process.env.DEFAULT_LOCATION ?? "Prodater";
    const contextStorePath =
      config?.contextStorePath ?? process.env.CONTEXT_STORE_PATH ?? "";

    if (!contextStorePath) {
      api.logger?.error?.("[whatsapp-scheduler] CONTEXT_STORE_PATH not set");
      return;
    }
    initStore(contextStorePath);

    const gogAccount = config?.gogAccount ?? process.env.GOG_ACCOUNT ?? "";
    const gogKeyringPassword = config?.gogKeyringPassword ?? process.env.GOG_KEYRING_PASSWORD ?? "";
    if (!gogAccount || !gogKeyringPassword || !calendarId) {
      api.logger?.warn?.("[whatsapp-scheduler] missing GOG_ACCOUNT/GOG_KEYRING_PASSWORD/CALENDAR_ID");
    }

    const runtimeKeys = api.runtime ? Object.keys(api.runtime) : [];
    api.logger?.info?.(`[whatsapp-scheduler] runtime keys: ${runtimeKeys.join(",")}`);

    api.on("message_received", async (event: MessageReceivedEvent, ctx: MessageReceivedCtx) => {
      if (ctx.channelId !== "whatsapp") return;
      if (!event.content) return;

      const senderKey = event.metadata?.senderE164 ?? event.metadata?.senderId ?? event.from;
      const pendingContext: SchedulerContext | null = getContext(senderKey) ?? null;

      const result = await handleMessage(event.content, event.metadata?.senderName ?? senderKey, pendingContext, {
        calendarId,
        timezone,
        gogAccount,
        gogKeyringPassword,
        defaultLocation,
      });

      if (result.context) {
        setContext(senderKey, result.context);
      } else if (result.action === "scheduled" || result.action === "cancelled" || result.action === "no_intent") {
        deleteContext(senderKey);
      }

      if (!result.message) return;

      const to = ctx.conversationId ?? event.from;
      const sendWhatsApp =
        api.runtime?.channel?.whatsapp?.sendMessageWhatsApp ??
        api.runtime?.whatsapp?.sendMessageWhatsApp ??
        api.runtime?.web?.sendMessageWhatsApp ??
        api.runtime?.channels?.whatsapp?.sendMessageWhatsApp;

      if (!sendWhatsApp) {
        api.logger?.error?.("[whatsapp-scheduler] sendMessageWhatsApp not available on runtime");
        return;
      }

      await sendWhatsApp(to, result.message, {
        accountId: ctx.accountId,
      });
    });

    api.logger?.info?.("[whatsapp-scheduler] loaded");
  },
};

export default plugin;
