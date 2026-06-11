import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomUUID, timingSafeEqual } from "node:crypto";

import { WebSocket, WebSocketServer } from "ws";

const CHANNEL_ID = "weauto";
const ROUTE_PATH = "/weauto/channel";
const PROTOCOL_VERSION = 1;
const DEFAULT_ACCOUNT_ID = "default";
const accountStates = new Map();
const conversationDirectory = new Map();
const turnCache = new Map();
const wss = new WebSocketServer({
  noServer: true,
  maxPayload: 64 * 1024 * 1024,
});

function channelConfig(cfg) {
  const value = cfg?.channels?.[CHANNEL_ID];
  return value && typeof value === "object" ? value : {};
}

function accountConfig(cfg, accountId) {
  const root = channelConfig(cfg);
  const nested = root.accounts?.[accountId];
  return nested && typeof nested === "object" ? { ...root, ...nested } : root;
}

function resolveAccount(cfg, accountId = DEFAULT_ACCOUNT_ID) {
  const id = String(accountId || DEFAULT_ACCOUNT_ID);
  const config = accountConfig(cfg, id);
  const token = typeof config.token === "string" ? config.token.trim() : "";
  return {
    accountId: id,
    name: String(config.name || id),
    enabled: config.enabled !== false,
    configured: Boolean(token),
    token,
    attachmentMaxMb: Math.max(1, Number(config.attachmentMaxMb || 20)),
  };
}

function listAccountIds(cfg) {
  const root = channelConfig(cfg);
  const ids = Object.keys(root.accounts || {});
  if (ids.length) return ids;
  return root.token || root.enabled ? [DEFAULT_ACCOUNT_ID] : [];
}

function constantTimeEqual(left, right) {
  const a = Buffer.from(String(left || ""));
  const b = Buffer.from(String(right || ""));
  return a.length === b.length && timingSafeEqual(a, b);
}

function bearerToken(req) {
  const header = String(req.headers.authorization || "");
  return header.toLowerCase().startsWith("bearer ") ? header.slice(7).trim() : "";
}

function rejectUpgrade(socket, status, message) {
  socket.write(
    `HTTP/1.1 ${status}\r\nConnection: close\r\nContent-Type: text/plain\r\n` +
      `Content-Length: ${Buffer.byteLength(message)}\r\n\r\n${message}`,
  );
  socket.destroy();
}

function sendJson(ws, frame) {
  if (ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(frame));
  return true;
}

function accountState(accountId) {
  return accountStates.get(accountId);
}

function liveSocket(state) {
  return [...state.connections]
    .reverse()
    .find((ws) => ws.readyState === WebSocket.OPEN);
}

function conversationKey(accountId, conversationId) {
  return `${accountId}:${conversationId}`;
}

function rememberConversation(accountId, conversation) {
  if (!conversation?.id) return;
  conversationDirectory.set(conversationKey(accountId, conversation.id), {
    id: String(conversation.id),
    title: String(conversation.title || conversation.id),
    kind: conversation.kind === "group" ? "group" : "direct",
  });
}

function emitTurnFrame(entry, frame) {
  entry.frames.push(frame);
  for (const ws of entry.waiters) sendJson(ws, frame);
}

function pruneTurnCache() {
  const cutoff = Date.now() - 10 * 60 * 1000;
  for (const [key, entry] of turnCache) {
    if (entry.completedAt && entry.completedAt < cutoff) turnCache.delete(key);
  }
  while (turnCache.size > 500) {
    turnCache.delete(turnCache.keys().next().value);
  }
}

function safeFileName(value) {
  return String(value || "attachment")
    .replace(/[^0-9A-Za-z._-]+/g, "_")
    .replace(/^[._]+|[._]+$/g, "") || "attachment";
}

async function decodeInboundAttachments(account, eventId, attachments) {
  const paths = [];
  const types = [];
  const maxBytes = account.attachmentMaxMb * 1024 * 1024;
  const outputDir = path.join(os.tmpdir(), "openclaw-weauto", eventId);
  for (const item of Array.isArray(attachments) ? attachments : []) {
    const encoded = typeof item?.data_base64 === "string" ? item.data_base64 : "";
    if (!encoded) continue;
    const data = Buffer.from(encoded, "base64");
    if (data.length > maxBytes) {
      throw new Error(`inbound attachment exceeds ${account.attachmentMaxMb}MB`);
    }
    fs.mkdirSync(outputDir, { recursive: true });
    const target = path.join(outputDir, safeFileName(item.name));
    fs.writeFileSync(target, data);
    paths.push(target);
    types.push(String(item.mime_type || "application/octet-stream"));
  }
  return { paths, types };
}

async function encodeOutboundAttachment(account, mediaUrl) {
  if (!mediaUrl) return null;
  const maxBytes = account.attachmentMaxMb * 1024 * 1024;
  let data;
  let name;
  let mimeType = "application/octet-stream";
  if (/^https?:\/\//i.test(mediaUrl)) {
    const response = await fetch(mediaUrl);
    if (!response.ok) throw new Error(`media download failed: HTTP ${response.status}`);
    data = Buffer.from(await response.arrayBuffer());
    name = safeFileName(new URL(mediaUrl).pathname.split("/").pop());
    mimeType = response.headers.get("content-type") || mimeType;
  } else {
    const filePath = mediaUrl.startsWith("file://")
      ? new URL(mediaUrl).pathname
      : path.resolve(mediaUrl);
    data = fs.readFileSync(filePath);
    name = path.basename(filePath);
  }
  if (data.length > maxBytes) {
    throw new Error(`outbound attachment exceeds ${account.attachmentMaxMb}MB`);
  }
  return {
    name: safeFileName(name),
    mime_type: mimeType,
    size: data.length,
    data_base64: data.toString("base64"),
  };
}

function outboundFrame({ accountId, to, text, attachments = [], requestId }) {
  const conversation =
    conversationDirectory.get(conversationKey(accountId, to)) || {
      id: to,
      title: to,
      kind: "direct",
    };
  return {
    type: "message.send",
    version: PROTOCOL_VERSION,
    message_id: randomUUID(),
    ...(requestId ? { request_id: requestId } : {}),
    conversation,
    text: String(text || ""),
    attachments,
  };
}

async function dispatchInbound(state, ws, frame) {
  const eventId = String(frame.event_id || "");
  if (!eventId) throw new Error("event_id is required");
  const cacheKey = `${state.account.accountId}:${eventId}`;
  const cached = turnCache.get(cacheKey);
  if (cached) {
    if (cached.completedAt) {
      for (const replay of cached.frames) sendJson(ws, replay);
    } else {
      cached.waiters.add(ws);
    }
    return;
  }

  const entry = { frames: [], waiters: new Set([ws]), completedAt: 0 };
  turnCache.set(cacheKey, entry);
  pruneTurnCache();
  sendJson(ws, { type: "ack", event_id: eventId });

  const message = frame.message && typeof frame.message === "object" ? frame.message : {};
  const conversation =
    message.conversation && typeof message.conversation === "object"
      ? message.conversation
      : {};
  const sender =
    message.sender && typeof message.sender === "object" ? message.sender : {};
  const conversationId = String(conversation.id || "");
  if (!conversationId) throw new Error("message.conversation.id is required");
  rememberConversation(state.account.accountId, conversation);

  const body = String(message.text || "").trim();
  const media = await decodeInboundAttachments(
    state.account,
    eventId,
    message.attachments,
  );
  const chatType = conversation.kind === "group" ? "group" : "direct";
  const ctx = {
    Body: body || (media.paths.length ? "[attachment]" : ""),
    BodyForAgent: body || (media.paths.length ? "[attachment]" : ""),
    CommandBody: body,
    BodyForCommands: body,
    From: String(sender.id || sender.name || conversationId),
    To: conversationId,
    AccountId: state.account.accountId,
    OriginatingChannel: CHANNEL_ID,
    OriginatingTo: conversationId,
    MessageSid: eventId,
    Timestamp: Number(message.timestamp_ms || Date.now()),
    Provider: CHANNEL_ID,
    ChatType: chatType,
    SenderName: String(sender.name || sender.id || ""),
    WasMentioned: Boolean(message.mentioned_bot),
    CommandAuthorized: Boolean(message.metadata?.is_admin),
    ...(media.paths.length
      ? {
          MediaPath: media.paths[0],
          MediaPaths: media.paths,
          MediaType: media.types[0],
          MediaTypes: media.types,
        }
      : {}),
  };

  const channelRuntime = state.channelRuntime;
  const route = channelRuntime.routing.resolveAgentRoute({
    cfg: state.cfg,
    channel: CHANNEL_ID,
    accountId: state.account.accountId,
    peer: { kind: chatType, id: conversationId },
  });
  ctx.SessionKey = route.sessionKey;
  const storePath = channelRuntime.session.resolveStorePath(state.cfg.session?.store, {
    agentId: route.agentId,
  });
  const finalized = channelRuntime.reply.finalizeInboundContext(ctx);
  await channelRuntime.session.recordInboundSession({
    storePath,
    sessionKey: route.sessionKey,
    ctx: finalized,
    updateLastRoute: {
      sessionKey: route.mainSessionKey,
      channel: CHANNEL_ID,
      to: conversationId,
      accountId: state.account.accountId,
    },
    onRecordError: (error) =>
      state.log?.error?.(`weauto recordInboundSession: ${String(error)}`),
  });

  const humanDelay = channelRuntime.reply.resolveHumanDelayConfig(
    state.cfg,
    route.agentId,
  );
  const { dispatcher, replyOptions, markDispatchIdle } =
    channelRuntime.reply.createReplyDispatcherWithTyping({
      humanDelay,
      deliver: async (payload) => {
        const mediaUrl = payload.mediaUrl || payload.mediaUrls?.[0];
        const attachment = mediaUrl
          ? await encodeOutboundAttachment(state.account, mediaUrl)
          : null;
        emitTurnFrame(
          entry,
          outboundFrame({
            accountId: state.account.accountId,
            to: conversationId,
            text: payload.text || "",
            attachments: attachment ? [attachment] : [],
            requestId: eventId,
          }),
        );
      },
      onError: (error) =>
        state.log?.error?.(`weauto reply delivery: ${String(error)}`),
    });

  try {
    await channelRuntime.reply.withReplyDispatcher({
      dispatcher,
      run: () =>
        channelRuntime.reply.dispatchReplyFromConfig({
          ctx: finalized,
          cfg: state.cfg,
          dispatcher,
          replyOptions: { ...replyOptions, disableBlockStreaming: true },
        }),
    });
    emitTurnFrame(entry, {
      type: "turn.complete",
      request_id: eventId,
      status: "ok",
      send: true,
    });
  } catch (error) {
    emitTurnFrame(entry, {
      type: "turn.complete",
      request_id: eventId,
      status: "error",
      error: String(error),
    });
  } finally {
    markDispatchIdle();
    entry.completedAt = Date.now();
  }
}

function installSocket(ws, req, state) {
  state.connections.add(ws);
  ws.on("close", () => state.connections.delete(ws));
  ws.on("error", (error) =>
    state.log?.error?.(`weauto websocket: ${String(error)}`),
  );
  ws.on("message", (raw) => {
    void (async () => {
      try {
        const frame = JSON.parse(raw.toString("utf8"));
        if (frame.type === "hello") {
          if (Number(frame.version) !== PROTOCOL_VERSION) {
            throw new Error(`unsupported protocol version: ${frame.version}`);
          }
          sendJson(ws, {
            type: "ready",
            version: PROTOCOL_VERSION,
            account_id: state.account.accountId,
            server: "openclaw-weauto-channel",
          });
          return;
        }
        if (frame.type === "ping") {
          sendJson(ws, { type: "pong", timestamp_ms: Date.now() });
          return;
        }
        if (frame.type === "message.received" || frame.type === "pong") return;
        if (frame.type === "message.create") {
          await dispatchInbound(state, ws, frame);
          return;
        }
        throw new Error(`unsupported frame type: ${frame.type}`);
      } catch (error) {
        let requestId = "";
        try {
          requestId = String(JSON.parse(raw.toString("utf8")).event_id || "");
        } catch {
          requestId = "";
        }
        const cacheKey = `${state.account.accountId}:${requestId}`;
        const entry = requestId ? turnCache.get(cacheKey) : null;
        if (entry && !entry.completedAt) {
          emitTurnFrame(entry, {
            type: "turn.complete",
            request_id: requestId,
            status: "error",
            error: String(error),
          });
          entry.completedAt = Date.now();
        } else {
          sendJson(ws, {
            type: "error",
            request_id: requestId,
            message: String(error),
          });
        }
      }
    })();
  });
  state.log?.info?.(
    `weauto websocket connected account=${state.account.accountId} remote=${req.socket.remoteAddress}`,
  );
}

async function sendProactive({ cfg, accountId, to, text, mediaUrl }) {
  const account = resolveAccount(cfg, accountId);
  const state = accountState(account.accountId);
  if (!state) throw new Error(`weauto account is not running: ${account.accountId}`);
  const ws = liveSocket(state);
  if (!ws) throw new Error(`weauto client is not connected: ${account.accountId}`);
  const attachment = mediaUrl
    ? await encodeOutboundAttachment(account, mediaUrl)
    : null;
  const frame = outboundFrame({
    accountId: account.accountId,
    to,
    text,
    attachments: attachment ? [attachment] : [],
  });
  sendJson(ws, frame);
  return { channel: CHANNEL_ID, messageId: frame.message_id };
}

const weautoChannel = {
  id: CHANNEL_ID,
  meta: {
    id: CHANNEL_ID,
    label: "weauto",
    selectionLabel: "weauto (persistent WebSocket)",
    docsPath: "/channels/weauto",
    docsLabel: "weauto",
    blurb: "Persistent remote channel backed by a weauto WeChat RPA client.",
    order: 80,
  },
  configSchema: {
    schema: {
      type: "object",
      additionalProperties: false,
      properties: {
        enabled: { type: "boolean" },
        name: { type: "string" },
        token: { type: "string" },
        attachmentMaxMb: { type: "number", minimum: 1 },
        accounts: {
          type: "object",
          additionalProperties: {
            type: "object",
            additionalProperties: false,
            properties: {
              enabled: { type: "boolean" },
              name: { type: "string" },
              token: { type: "string" },
              attachmentMaxMb: { type: "number", minimum: 1 },
            },
          },
        },
      },
    },
  },
  capabilities: {
    chatTypes: ["direct", "group"],
    media: true,
    blockStreaming: true,
  },
  reload: { configPrefixes: ["channels.weauto"] },
  config: {
    listAccountIds,
    resolveAccount,
    defaultAccountId: () => DEFAULT_ACCOUNT_ID,
    isConfigured: (account) => account.configured,
    describeAccount: (account) => ({
      accountId: account.accountId,
      name: account.name,
      enabled: account.enabled,
      configured: account.configured,
    }),
  },
  outbound: {
    deliveryMode: "direct",
    textChunkLimit: 8000,
    sendText: (ctx) =>
      sendProactive({
        cfg: ctx.cfg,
        accountId: ctx.accountId || DEFAULT_ACCOUNT_ID,
        to: ctx.to,
        text: ctx.text,
      }),
    sendMedia: (ctx) =>
      sendProactive({
        cfg: ctx.cfg,
        accountId: ctx.accountId || DEFAULT_ACCOUNT_ID,
        to: ctx.to,
        text: ctx.text || "",
        mediaUrl: ctx.mediaUrl,
      }),
  },
  gateway: {
    startAccount: async (ctx) => {
      if (!ctx.channelRuntime) {
        throw new Error("OpenClaw channel runtime is unavailable");
      }
      if (!ctx.account.configured) {
        throw new Error("weauto channel token is not configured");
      }
      const state = {
        cfg: ctx.cfg,
        account: ctx.account,
        channelRuntime: ctx.channelRuntime,
        connections: new Set(),
        log: ctx.log,
      };
      accountStates.set(ctx.account.accountId, state);
      ctx.setStatus?.({
        accountId: ctx.account.accountId,
        running: true,
        lastStartAt: Date.now(),
      });
      await new Promise((resolve) => {
        ctx.abortSignal.addEventListener("abort", resolve, { once: true });
      });
      for (const ws of state.connections) ws.close(1001, "channel stopping");
      accountStates.delete(ctx.account.accountId);
    },
  },
};

export default {
  id: CHANNEL_ID,
  name: "weauto",
  description: "Persistent WebSocket channel bridge for weauto.",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {},
  },
  register(api) {
    api.registerChannel({ plugin: weautoChannel });
    api.registerHttpRoute({
      path: ROUTE_PATH,
      auth: "plugin",
      match: "exact",
      handler: (_req, res) => {
        res.statusCode = 426;
        res.setHeader("Content-Type", "text/plain; charset=utf-8");
        res.end("WebSocket upgrade required");
        return true;
      },
      handleUpgrade: (req, socket, head) => {
        const url = new URL(req.url || ROUTE_PATH, "http://localhost");
        const accountId =
          url.searchParams.get("account_id") ||
          String(req.headers["x-weauto-account"] || DEFAULT_ACCOUNT_ID);
        const state = accountState(accountId);
        if (!state) {
          rejectUpgrade(socket, "503 Service Unavailable", "weauto account is not running");
          return true;
        }
        if (!constantTimeEqual(bearerToken(req), state.account.token)) {
          rejectUpgrade(socket, "401 Unauthorized", "invalid weauto token");
          return true;
        }
        wss.handleUpgrade(req, socket, head, (ws) => installSocket(ws, req, state));
        return true;
      },
    });
  },
};
