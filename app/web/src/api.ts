let csrf = '';
export class ApiError extends Error {
  constructor(public code: string, message: string) {super(message);}
}
export async function request<T>(path: string, method = 'GET', body?: unknown, headers: Record<string,string> = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api/v1' + path, {
      method, credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-Taverna-CSRF': csrf,...headers},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
  } catch {throw new ApiError('network_error', 'Сервер недоступен. Запустите Таверну повторно.');}
  const value = await response.json();
  if (!response.ok) throw new ApiError(value.code, value.message_ru || 'Не удалось выполнить запрос.');
  return value as T;
}
let authentication: Promise<void> | undefined;
export function authenticate(): Promise<void> {
  if (authentication) return authentication;
  authentication = (async () => {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get('launch');
    history.replaceState(null, '', location.pathname);
    const session = await request<{csrf: string}>(token ? '/session/bootstrap' : '/session', token ? 'POST' : 'GET', token ? {token} : undefined);
    csrf = session.csrf;
  })().catch(e => {authentication = undefined; throw e;});
  return authentication;
}
