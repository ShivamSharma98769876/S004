type StreamHandler = (payload: unknown) => void;

type StreamState = {
  socket: WebSocket | null;
  url: string;
  reconnectAttempts: number;
  maxReconnect: number;
  closedByUser: boolean;
};

export class FrontendStreamManager {
  private channels = new Map<string, StreamState>();
  private handlers = new Map<string, Set<StreamHandler>>();

  connect(channel: string, url: string): void {
    const existing = this.channels.get(channel);
    if (existing?.socket && existing.socket.readyState === WebSocket.OPEN) {
      return;
    }

    const state: StreamState = existing ?? {
      socket: null,
      url,
      reconnectAttempts: 0,
      maxReconnect: 10,
      closedByUser: false,
    };
    state.url = url;
    state.closedByUser = false;

    const ws = new WebSocket(url);
    state.socket = ws;
    this.channels.set(channel, state);

    ws.onopen = () => {
      state.reconnectAttempts = 0;
    };

    ws.onmessage = (event) => {
      let parsed: unknown = event.data;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        // Keep raw payload if not valid JSON
      }
      const callbacks = this.handlers.get(channel);
      callbacks?.forEach((h) => h(parsed));
    };

    ws.onclose = () => {
      if (state.closedByUser) return;
      this.reconnect(channel);
    };

    ws.onerror = () => {
      // Rely on onclose reconnect flow.
    };
  }

  disconnect(channel: string): void {
    const state = this.channels.get(channel);
    if (!state) return;
    state.closedByUser = true;
    state.socket?.close();
    state.socket = null;
  }

  subscribe(channel: string, handler: StreamHandler): () => void {
    if (!this.handlers.has(channel)) this.handlers.set(channel, new Set());
    this.handlers.get(channel)!.add(handler);
    return () => this.unsubscribe(channel, handler);
  }

  unsubscribe(channel: string, handler: StreamHandler): void {
    const set = this.handlers.get(channel);
    if (!set) return;
    set.delete(handler);
    if (set.size === 0) this.handlers.delete(channel);
  }

  private reconnect(channel: string): void {
    const state = this.channels.get(channel);
    if (!state) return;
    if (state.reconnectAttempts >= state.maxReconnect) return;
    state.reconnectAttempts += 1;
    const delayMs = Math.min(30000, 1000 * 2 ** Math.min(state.reconnectAttempts, 5));
    setTimeout(() => {
      if (!state.closedByUser) this.connect(channel, state.url);
    }, delayMs);
  }
}

