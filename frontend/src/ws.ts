/** Client WebSocket temps réel (§9) avec reconnexion automatique. */
import { getToken } from "./api";

type Handler = (payload: any) => void;
const handlers = new Map<string, Set<Handler>>();
type StatusCb = (s: "connecté" | "déconnecté") => void;
const statusCbs = new Set<StatusCb>();

let ws: WebSocket | null = null;
let tentatives = 0;
let timer: number | undefined;

export function on(type: string, h: Handler): () => void {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type)!.add(h);
  return () => handlers.get(type)?.delete(h);
}

export function onStatus(cb: StatusCb): () => void {
  statusCbs.add(cb);
  return () => statusCbs.delete(cb);
}

function emitStatus(s: "connecté" | "déconnecté") {
  statusCbs.forEach((cb) => cb(s));
}

export function connectWS() {
  const token = getToken();
  if (!token) return;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);

  ws.onopen = () => {
    tentatives = 0;
    emitStatus("connecté");
  };
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      handlers.get(m.type)?.forEach((h) => h(m.payload));
      handlers.get("*")?.forEach((h) => h(m));
    } catch {
      /* message non JSON */
    }
  };
  ws.onclose = () => {
    emitStatus("déconnecté");
    tentatives += 1;
    window.clearTimeout(timer);
    timer = window.setTimeout(connectWS, Math.min(15000, 1200 * tentatives));
  };
}

export function disconnectWS() {
  window.clearTimeout(timer);
  ws?.close();
  ws = null;
}

// keep-alive applicatif
setInterval(() => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
}, 25000);
