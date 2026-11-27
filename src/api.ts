import { invoke, isTauri } from "@tauri-apps/api/core";
let origin = "",
  token = "",
  hostPid = 0;
export async function connect() {
  if (isTauri()) {
    const data = await invoke<{ port: number; token: string; host_pid: number }>("bootstrap");
    origin = `http://127.0.0.1:${data.port}`;
    token = data.token;
    hostPid = data.host_pid;
  }
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  if (path.endsWith("/agent") && hostPid && body && typeof body === "object" && !(body instanceof Blob))
    body = { ...body, host_pid: hostPid };
  const file = body instanceof Blob ? body : undefined;
  const response = await fetch(`${origin}/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": file ? "application/octet-stream" : "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: file ?? (body === undefined ? undefined : JSON.stringify(body)),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      result.error || "The project service could not complete this action.",
    );
  return result;
}
export function projectRoute(id: string) {
  return `/projects/${encodeURIComponent(id)}`;
}
export function media(id: string, path: string, version = 0) {
  return `${origin}/api/media?project=${encodeURIComponent(id)}&path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}&v=${version}`;
}
/** A background music track, bundled or added by the user, for the in-app preview. */
export function musicUrl(name: string) {
  return `${origin}/api/music?name=${encodeURIComponent(name)}&token=${encodeURIComponent(token)}`;
}
