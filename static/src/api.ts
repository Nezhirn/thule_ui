import type { Session, Message } from './types';

const BASE = '';

export async function fetchSessions(): Promise<Session[]> {
  const res = await fetch(`${BASE}/api/sessions`);
  return res.json();
}

export async function createSession(
  title = 'Новый чат',
  provider: 'qwen' | 'claude' = 'qwen',
  model?: string
): Promise<Session> {
  const res = await fetch(`${BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, provider, model }),
  });
  return res.json();
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${BASE}/api/sessions/${id}`, { method: 'DELETE' });
}

export async function renameSession(id: string, title: string): Promise<void> {
  await fetch(`${BASE}/api/sessions/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
}

export async function fetchMessages(sessionId: string): Promise<Message[]> {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}/messages?limit=200`);
  const data = await res.json();
  return data.messages || data;
}

export async function fetchDefaultPrompt(): Promise<string> {
  const res = await fetch(`${BASE}/api/default-prompt`);
  const data = await res.json();
  return data.default_prompt || '';
}

export async function fetchSessionPrompt(sessionId: string): Promise<string> {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}/system-prompt`);
  const data = await res.json();
  return data.system_prompt || '';
}

export async function saveSessionPrompt(sessionId: string, prompt: string | null): Promise<void> {
  await fetch(`${BASE}/api/sessions/${sessionId}/system-prompt`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ system_prompt: prompt }),
  });
}

export async function saveSessionSettings(
  sessionId: string,
  settings: { provider: string; model: string | null }
): Promise<void> {
  await fetch(`${BASE}/api/sessions/${sessionId}/settings`, {
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
