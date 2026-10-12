package auth

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"agentplatform/gateway/internal/httpbase"
)

type principalKey struct{}

func FromContext(ctx context.Context) (Principal, bool) {
	value, ok := ctx.Value(principalKey{}).(Principal)
	return value, ok
}

type HTTP struct {
	store        *Store
	cookieName   string
	cookieSecure bool
	ttl          time.Duration
}

func NewHTTP(store *Store, cookieName string, secure bool, ttl time.Duration) *HTTP {
	return &HTTP{store: store, cookieName: cookieName, cookieSecure: secure, ttl: ttl}
}

func (h *HTTP) Login(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Email       string `json:"email"`
		Password    string `json:"password"`
		WorkspaceID string `json:"workspace_id"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	principal, token, err := h.store.Login(r.Context(), request.Email, request.Password, request.WorkspaceID, h.ttl)
	if err != nil {
		httpbase.Error(w, http.StatusUnauthorized, "invalid_credentials")
		return
	}
	h.setCookie(w, token, time.Now().Add(h.ttl))
	httpbase.JSON(w, http.StatusOK, principal)
}

func (h *HTTP) Logout(w http.ResponseWriter, r *http.Request) {
	if cookie, err := r.Cookie(h.cookieName); err == nil {
		_ = h.store.Logout(r.Context(), cookie.Value)
	}
	h.setCookie(w, "", time.Unix(0, 0))
	w.WriteHeader(http.StatusNoContent)
}

func (h *HTTP) Me(w http.ResponseWriter, r *http.Request) {
	principal, ok := FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	httpbase.JSON(w, http.StatusOK, principal)
}

func (h *HTTP) RequireSession(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cookie, err := r.Cookie(h.cookieName)
		if err != nil || strings.TrimSpace(cookie.Value) == "" {
			httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
			return
		}
		principal, err := h.store.Resolve(r.Context(), cookie.Value, r.Header.Get("X-Workspace-ID"))
		if err != nil {
			httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
			return
		}
		ctx := context.WithValue(r.Context(), principalKey{}, principal)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func (h *HTTP) RequireAdmin(next http.Handler) http.Handler {
	return h.RequireSession(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		principal, _ := FromContext(r.Context())
		if principal.Role != "admin" {
			httpbase.Error(w, http.StatusForbidden, "admin_required")
			return
		}
		next.ServeHTTP(w, r)
	}))
}

func (h *HTTP) setCookie(w http.ResponseWriter, token string, expires time.Time) {
	http.SetCookie(w, &http.Cookie{
		Name: h.cookieName, Value: token, Path: "/", Expires: expires,
		MaxAge: int(time.Until(expires).Seconds()), HttpOnly: true,
		Secure: h.cookieSecure, SameSite: http.SameSiteStrictMode,
	})
}
