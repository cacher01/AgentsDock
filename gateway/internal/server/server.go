package server

import (
	"context"
	"log/slog"
	"net/http"
	"time"

	"agentplatform/gateway/internal/auditlog"
	"agentplatform/gateway/internal/auth"
	"agentplatform/gateway/internal/budget"
	"agentplatform/gateway/internal/config"
	"agentplatform/gateway/internal/httpbase"
	"agentplatform/gateway/internal/modelgateway"
	runtimeproxy "agentplatform/gateway/internal/proxy"
	"agentplatform/gateway/internal/secure"
	"agentplatform/gateway/internal/toolcatalog"
	"github.com/jackc/pgx/v5/pgxpool"
)

func New(cfg config.Config, db *pgxpool.Pool, logger *slog.Logger) (http.Handler, error) {
	cipher, err := secure.New(cfg.ModelMasterKey)
	if err != nil {
		return nil, err
	}
	authHTTP := auth.NewHTTP(auth.NewStore(db), cfg.CookieName, cfg.CookieSecure, cfg.SessionTTL)
	auditStore := auditlog.NewStore(db)
	auditHTTP := auditlog.NewHTTP(auditStore)
	modelHTTP := modelgateway.NewHTTP(
		modelgateway.NewStore(db, cipher), cfg.InternalAPIToken, auditStore,
	)
	budgetHTTP := budget.NewHTTP(budget.NewStore(db), auditStore)
	toolHTTP := toolcatalog.NewHTTP(toolcatalog.NewStore(db), auditStore)
	proxy, err := runtimeproxy.Runtime(cfg.RuntimeURL, cfg.InternalAPIToken, cfg.PlatformTaskAPI)
	if err != nil {
		return nil, err
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", func(w http.ResponseWriter, _ *http.Request) {
		httpbase.JSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /health/ready", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		if err := db.Ping(ctx); err != nil {
			httpbase.Error(w, http.StatusServiceUnavailable, "database_unavailable")
			return
		}
		httpbase.JSON(w, http.StatusOK, map[string]string{"status": "ready"})
	})
	mux.HandleFunc("POST /internal/v1/model/invoke", modelHTTP.Invoke)
	mux.HandleFunc("POST /api/v1/auth/login", authHTTP.Login)
	mux.HandleFunc("POST /api/v1/auth/invitations/accept", authHTTP.AcceptInvitation)
	mux.Handle("POST /api/v1/auth/logout", authHTTP.RequireSession(http.HandlerFunc(authHTTP.Logout)))
	mux.Handle("GET /api/v1/me", authHTTP.RequireSession(http.HandlerFunc(authHTTP.Me)))
	mux.Handle("GET /api/v1/workspace/members", authHTTP.RequireSession(http.HandlerFunc(authHTTP.ListMembers)))
	mux.Handle("POST /api/v1/workspace/invitations", authHTTP.RequireAdmin(http.HandlerFunc(authHTTP.CreateInvitation)))
	mux.Handle("GET /api/v1/models", authHTTP.RequireSession(http.HandlerFunc(modelHTTP.List)))
	mux.Handle("GET /api/v1/admin/models", authHTTP.RequireAdmin(http.HandlerFunc(modelHTTP.List)))
	mux.Handle("POST /api/v1/admin/models", authHTTP.RequireAdmin(http.HandlerFunc(modelHTTP.Create)))
	mux.Handle("GET /api/v1/admin/budget", authHTTP.RequireAdmin(http.HandlerFunc(budgetHTTP.Get)))
	mux.Handle("PATCH /api/v1/admin/budget", authHTTP.RequireAdmin(http.HandlerFunc(budgetHTTP.Update)))
	mux.Handle("GET /api/v1/admin/tools", authHTTP.RequireAdmin(http.HandlerFunc(toolHTTP.List)))
	mux.Handle("PATCH /api/v1/admin/tools/{definition_id}", authHTTP.RequireAdmin(http.HandlerFunc(toolHTTP.Update)))
	mux.Handle("GET /api/v1/admin/governance/tasks", authHTTP.RequireAdmin(proxy))
	mux.Handle("GET /api/v1/admin/audit", authHTTP.RequireAdmin(http.HandlerFunc(auditHTTP.List)))

	mux.Handle("GET /{$}", proxy)
	mux.Handle("GET /ui", proxy)
	mux.Handle("/static/", proxy)
	mux.Handle(
		"POST /api/v1/files",
		authHTTP.RequireSession(runtimeproxy.LimitBody(cfg.InputFileMaxBytes, proxy)),
	)

	for _, pattern := range []string{
		"/api/v1/tasks", "/api/v1/tasks/",
		"/api/v1/files", "/api/v1/files/",
		"/api/v1/notifications", "/api/v1/notifications/",
		"/api/v1/sessions", "/api/v1/sessions/",
		"/api/v1/quota/", "/api/v1/quota",
	} {
		mux.Handle(pattern, authHTTP.RequireSession(proxy))
	}
	handler := httpbase.WithRequestID(mux)
	handler = httpbase.Log(logger, handler)
	handler = httpbase.Recover(logger, handler)
	return handler, nil
}
