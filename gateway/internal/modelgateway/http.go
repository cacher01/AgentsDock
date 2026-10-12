package modelgateway

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"

	"agentplatform/gateway/internal/auditlog"
	"agentplatform/gateway/internal/auth"
	"agentplatform/gateway/internal/httpbase"
)

type HTTP struct {
	store         *Store
	internalToken string
	audit         *auditlog.Store
}

func NewHTTP(store *Store, internalToken string, audit *auditlog.Store) *HTTP {
	return &HTTP{store: store, internalToken: internalToken, audit: audit}
}

func (h *HTTP) List(w http.ResponseWriter, r *http.Request) {
	principal, ok := auth.FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	items, err := h.store.List(r.Context(), principal.WorkspaceID)
	if err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "models_unavailable")
		return
	}
	httpbase.JSON(w, http.StatusOK, map[string]any{"items": items})
}

func (h *HTTP) Create(w http.ResponseWriter, r *http.Request) {
	principal, ok := auth.FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	var request CreateRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	connection, err := h.store.Create(
		r.Context(), principal.WorkspaceID, principal.UserID, request,
	)
	if err != nil {
		httpbase.Error(w, http.StatusBadRequest, "model_create_failed")
		return
	}
	if err := h.audit.Record(r.Context(), principal.WorkspaceID, principal.UserID,
		"model.connection.created", "model_connection", connection.ID,
		map[string]any{"provider_type": connection.ProviderType},
	); err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "audit_write_failed")
		return
	}
	httpbase.JSON(w, http.StatusCreated, connection)
}

func (h *HTTP) Invoke(w http.ResponseWriter, r *http.Request) {
	provided := r.Header.Get("X-Internal-Token")
	if subtle.ConstantTimeCompare([]byte(provided), []byte(h.internalToken)) != 1 {
		httpbase.Error(w, http.StatusUnauthorized, "invalid_internal_token")
		return
	}
	workspaceID := r.Header.Get("X-Workspace-ID")
	if workspaceID == "" {
		httpbase.Error(w, http.StatusBadRequest, "workspace_required")
		return
	}
	var request InvokeRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4<<20)).Decode(&request); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	response, err := h.store.Invoke(r.Context(), workspaceID, request)
	if err != nil {
		httpbase.JSON(w, http.StatusBadGateway, map[string]any{
			"error": "model_unavailable", "detail": err.Error(),
		})
		return
	}
	httpbase.JSON(w, http.StatusOK, response)
}
