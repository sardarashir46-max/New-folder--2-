const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "";

export async function apiFetch(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});

  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    const csrfResponse = await fetch(`${API_BASE_URL}/api/auth/csrf/`, {
      credentials: "include",
    });
    if (!csrfResponse.ok) throw new Error("Could not start a secure request. Please retry.");
    const { csrfToken } = await csrfResponse.json();
    if (!csrfToken) throw new Error("The security token is missing. Please refresh and retry.");
    headers.set("X-CSRFToken", csrfToken);
  }

  return fetch(`${API_BASE_URL}${path}`, {
    ...options,
    method,
    headers,
    credentials: "include",
  });
}

export async function responseError(response) {
  let data = {};
  try {
    data = await response.json();
  } catch {
    return "The request could not be completed. Please try again.";
  }
  if (data.detail) return data.detail;
  if (data.errors) {
    const firstError = Object.values(data.errors).flat()[0];
    if (firstError) return firstError;
  }
  return "The request could not be completed. Please try again.";
}
