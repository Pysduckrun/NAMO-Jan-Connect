export function apiUrl(path: string): string {
  const base = globalThis.__NJC_API_URL__ || "";
  return (path.startsWith("/api") || path.startsWith("/kv")) ? `${base}${path}` : path;
}

export function setCookie(name: string, value: string, days = 30) {
  if (typeof document === "undefined") return;
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Lax`;
}

export function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const matches = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/([\.$?*|{}\(\)\[\]\\\/\+^])/g, '\\$1') + '=([^;]*)'));
  return matches ? decodeURIComponent(matches[1]) : null;
}

export function eraseCookie(name: string) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; Max-Age=0; path=/; SameSite=Lax`;
}

export function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const target = typeof input === "string" ? apiUrl(input) : input;
  const opts: RequestInit = { credentials: "include", ...(init || {}) };
  return globalThis.fetch(target, opts);
}

export async function readJson<T = Record<string, unknown>>(response: Response): Promise<T> {
  const text = await response.text();
  if (!text.trim()) {
    throw new Error(`The API returned an empty response (HTTP ${response.status}).`);
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`The API returned a non-JSON response (HTTP ${response.status}).`);
  }
}
export async function fetchOfficers(category: string): Promise<any[]> {
  const response = await apiFetch(`/kv/officers/${category}`);
  if (!response.ok) throw new Error('Failed to fetch officers');
  return readJson<any[]>(response);
}

export async function saveOfficers(category: string, officers: any[]): Promise<void> {
  const response = await apiFetch(`/kv/officers/${category}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(officers),
  });
  if (!response.ok) throw new Error('Failed to save officers');
}

export async function requestPasswordReset(email: string): Promise<void> {
  const response = await apiFetch('/kv/reset-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) throw new Error('Failed to request password reset');
}
