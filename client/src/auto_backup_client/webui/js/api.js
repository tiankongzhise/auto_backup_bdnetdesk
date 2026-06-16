const timeoutMs = 20000;

let bridgeReadyPromise;

function bridgeReady() {
  if (bridgeReadyPromise) {
    return bridgeReadyPromise;
  }
  bridgeReadyPromise = new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) {
      resolve();
      return;
    }
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
  return bridgeReadyPromise;
}

export async function call(method, ...args) {
  await bridgeReady();
  const api = window.pywebview && window.pywebview.api;
  if (!api || typeof api[method] !== "function") {
    throw new Error(`bridge method not available: ${method}`);
  }
  const timer = new Promise((_, reject) => {
    window.setTimeout(() => reject(new Error(`bridge call timeout: ${method}`)), timeoutMs);
  });
  const response = await Promise.race([api[method](...args), timer]);
  if (!response || response.ok !== true) {
    const message = response && response.error && response.error.message ? response.error.message : "bridge call failed";
    throw new Error(message);
  }
  return response.data;
}

export { bridgeReady };
