(() => {
  // Redirige vers la connexion dès qu'une requête API répond 401.
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const url = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, location.origin);
      if (response.status === 401 && url.origin === location.origin) {
        location.href = '/login';
      }
    } catch {}
    return response;
  };

  window.stockLogout = async () => {
    try {
      await window.fetch('/api/auth/logout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    } finally {
      location.href = '/login';
    }
  };

  window.stockLoadProfile = async () => {
    const response = await window.fetch('/api/auth/me');
    if (!response.ok) return null;
    return response.json();
  };
})();
