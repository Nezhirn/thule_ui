import type { Session, Message } from './types';

const BASE = '';

async function readError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return data.detail || data.error || JSON.stringify(data);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    throw new Error(await readError(res));
  }
  return res.json() as Promise<T>;
}

async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  const res = await fetch(url, init);
  if (!res.ok) {
    throw new Error(await readError(res));
  }
}

export async function fetchSessions(): Promise<Session[]> {
  return requestJson<Session[]>(`${BASE}/api/sessions`);
}

export async function createSession(
  title = 'Новый чат',
  provider: 'qwen' | 'claude' = 'qwen',
  model?: string
): Promise<Session> {
  return requestJson<Session>(`${BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, provider, model }),
  });
}

export async function deleteSession(id: string): Promise<void> {
  await requestVoid(`${BASE}/api/sessions/${id}`, { method: 'DELETE' });
}

export async function renameSession(id: string, title: string): Promise<void> {
  await requestVoid(`${BASE}/api/sessions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
}

export async function fetchMessages(sessionId: string): Promise<Message[]> {
  const data = await requestJson<{ messages?: Message[] } | Message[]>(
    `${BASE}/api/sessions/${sessionId}/messages?limit=200`
  );
  return data.messages || data;
}

export async function fetchDefaultPrompt(): Promise<string> {
  const data = await requestJson<{ default_prompt?: string }>(`${BASE}/api/default-prompt`);
  return data.default_prompt || '';
}

export async function fetchSessionPrompt(sessionId: string): Promise<string> {
  const data = await requestJson<{ system_prompt?: string }>(`${BASE}/api/sessions/${sessionId}/system-prompt`);
  return data.system_prompt || '';
}

export async function saveSessionPrompt(sessionId: string, prompt: string | null): Promise<void> {
  await requestVoid(`${BASE}/api/sessions/${sessionId}/system-prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system_prompt: prompt }),
  });
}

export async function saveSessionSettings(
  sessionId: string,
  settings: { provider: string; model: string | null }
): Promise<void> {
  await requestVoid(`${BASE}/api/sessions/${sessionId}/settings`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
}

export function createWebSocket(sessionId: string): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return new WebSocket(`${protocol}//${window.location.host}/ws/${sessionId}`);
}

export function exportSession(sessionId: string): void {
  const url = `${BASE}/api/sessions/${sessionId}/export`;
  const link = document.createElement('a');
  link.href = url;
  link.download = '';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
