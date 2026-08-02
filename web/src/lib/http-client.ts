const BASE_URL = "/api";

export async function request<T>(
  url: string,
  options?: RequestInit,
): Promise<T> {
  const headers = new Headers(options?.headers);
  if (options?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE_URL}${url}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.text();
    console.error(`API Error ${response.status}: ${error}`);
    throw new Error(
      `API Error ${response.status}: ${error || response.statusText}`,
    );
  }

  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    console.error(`API JSON parse error for ${url}:`, text);
    throw new Error(`Invalid JSON response from ${url}`);
  }
}
