import type { WSMessage } from "./types";

type Handler = (msg: WSMessage) => void;

export type ClientMode = "auto" | "player";

export class WSClient {
  private socket: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private token: string;
  private explicitUrl: string | null;
  private reconnectAttempts = 0;
  private shouldReconnect = true;
  private statusHandler: ((status: WSStatus) => void) | null;
  private mode: ClientMode;

  constructor(params: {
    token: string;
    url?: string;
    mode?: ClientMode;
    onStatus?: (status: WSStatus) => void;
  }) {
    this.token = params.token;
    this.explicitUrl = params.url ?? null;
    this.statusHandler = params.onStatus ?? null;
    this.mode = params.mode ?? "auto";
  }

  on(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** Re-handshake with a new mode without reconnecting the socket. */
  setMode(mode: ClientMode): void {
    this.mode = mode;
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.sendClientReady();
    }
  }

  get currentMode(): ClientMode {
    return this.mode;
  }

  connect(): void {
    this.shouldReconnect = true;
    this.open();
  }

  private sendClientReady(): void {
    this.send({
      type: "client_ready",
      mode: this.mode === "player" ? "player_v2" : "legacy",
      capabilities: ["dialogue_turn_gate"],
    });
  }

  private open() {
    this.emitStatus("connecting");
    const url = this.explicitUrl ?? this.buildUrl();
    const socket = new WebSocket(url);
    this.socket = socket;

    socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.emitStatus("open");
      this.sendClientReady();
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as WSMessage;
        for (const h of this.handlers) h(data);
      } catch (err) {
        console.warn("[ws] failed to parse message", err);
      }
    };

    socket.onerror = () => {
      this.emitStatus("error");
    };

    socket.onclose = (event) => {
      this.emitStatus("closed");
      this.socket = null;
      if (!this.shouldReconnect) return;
      // 4401 = auth failure from backend; don't loop-reconnect
      if (event.code === 4401) {
        this.emitStatus("unauthorized");
        return;
      }
      const delay = Math.min(8000, 500 * 2 ** Math.min(6, this.reconnectAttempts++));
      setTimeout(() => this.open(), delay);
    };
  }

  private buildUrl(): string {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws?token=${encodeURIComponent(this.token)}`;
  }

  send(payload: object): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  close(): void {
    this.shouldReconnect = false;
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }

  private emitStatus(status: WSStatus) {
    this.statusHandler?.(status);
  }
}

export type WSStatus =
  | "idle"
  | "connecting"
  | "open"
  | "closed"
  | "error"
  | "unauthorized";
