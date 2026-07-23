/**
 * auth.js — Google OAuth helper for the Emerald Bride admin panel.
 *
 * The session token lives in an httpOnly cookie set by emeraldbride-oauth-callback,
 * so this module never holds the token itself — it only calls the auth API and
 * reports outcomes. All calls use `credentials: 'include'` so the browser attaches
 * the cookie automatically.
 *
 * GOOGLE_CLIENT_ID below must be filled in after creating the OAuth 2.0 Client ID
 * in Google Cloud Console (see README/aws-setup.md for the exact steps + which
 * redirect URIs to register).
 */
(function (global) {
  const AUTH_API_BASE = 'https://sg0k9b4ggd.execute-api.us-east-1.amazonaws.com/prod';
  const GOOGLE_CLIENT_ID = 'REPLACE_WITH_YOUR_GOOGLE_OAUTH_CLIENT_ID.apps.googleusercontent.com';
  const OAUTH_STATE_KEY = 'eb_oauth_state';

  function getRedirectUri() {
    // Must exactly match a URI registered for the OAuth client in Google Cloud
    // Console. Deriving it from the current origin (rather than hardcoding)
    // means the same code works in production and in local dev, as long as
    // both URIs are registered.
    return `${window.location.origin}${window.location.pathname}`;
  }

  function redirectToGoogleLogin() {
    const state = crypto.getRandomValues(new Uint8Array(16))
      .reduce((s, b) => s + b.toString(16).padStart(2, '0'), '');
    sessionStorage.setItem(OAUTH_STATE_KEY, state);

    const params = new URLSearchParams({
      client_id: GOOGLE_CLIENT_ID,
      redirect_uri: getRedirectUri(),
      response_type: 'code',
      scope: 'openid email profile',
      state,
      prompt: 'select_account',
    });
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
  }

  function isOAuthRedirect() {
    return new URLSearchParams(window.location.search).has('code')
      || new URLSearchParams(window.location.search).has('error');
  }

  async function handleOAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const error = params.get('error');
    const state = params.get('state');

    // Scrub OAuth params from the URL regardless of outcome — refreshing the
    // page must never replay an already-used (or expired) authorization code.
    window.history.replaceState({}, '', `${window.location.origin}${window.location.pathname}`);

    if (error) {
      return { ok: false, message: `Google sign-in was cancelled or denied (${error}).` };
    }
    if (!code) {
      return { ok: false, message: '' };
    }

    const expectedState = sessionStorage.getItem(OAUTH_STATE_KEY);
    sessionStorage.removeItem(OAUTH_STATE_KEY);
    if (!state || !expectedState || state !== expectedState) {
      return { ok: false, message: 'Sign-in could not be verified (state mismatch). Please try again.' };
    }

    try {
      const res = await fetch(`${AUTH_API_BASE}/oauth-callback`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return { ok: false, message: data.error || 'Sign-in failed.' };
      }
      return { ok: true, email: data.email };
    } catch (e) {
      return { ok: false, message: 'Could not reach the sign-in service. Check your connection.' };
    }
  }

  async function isAuthenticated() {
    try {
      const res = await fetch(`${AUTH_API_BASE}/oauth-verify`, { credentials: 'include' });
      const data = await res.json().catch(() => ({}));
      return data.authenticated ? { ok: true, email: data.email } : { ok: false };
    } catch (e) {
      return { ok: false };
    }
  }

  async function logout() {
    try {
      await fetch(`${AUTH_API_BASE}/oauth-logout`, { method: 'POST', credentials: 'include' });
    } catch (e) {
      // Best-effort — the cookie will simply expire on its own (8h TTL) if this fails.
    }
    window.location.href = 'index.html';
  }

  function getAuthToken() {
    // Always null by design: the session lives in an httpOnly cookie
    // specifically so client-side JS (including this file) cannot read it.
    // Kept as a named export for API discoverability/compatibility — use
    // isAuthenticated() to check auth state instead.
    return null;
  }

  global.EBAuth = {
    getAuthToken,
    isAuthenticated,
    redirectToGoogleLogin,
    handleOAuthCallback,
    isOAuthRedirect,
    logout,
  };
})(window);
