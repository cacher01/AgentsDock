package toolcatalog

import (
	"encoding/json"
	"net/http"

	"agentplatform/gateway/internal/auditlog"
	"agentplatform/gateway/internal/auth"
	"agentplatform/gateway/internal/httpbase"
)

type HTTP struct {
	store *Store
	audit *auditlog.Store
}

func NewHTTP(store *Store, audit *auditlog.Store) *HTTP {
	return &HTTP{store: store, audit: audit}
}

func (h *HTTP) List(w http.ResponseWriter, r *http.Request) {
	principal, ok := auth.FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	tools, err := h.store.List(r.Context(), principal.WorkspaceID)
	if err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "tools_unavailable")
		return
	}
	httpbase.JSON(w, http.StatusOK, map[string]any{"items": tools})
}

func (h *HTTP) Update(w http.ResponseWriter, r *http.Request) {
	principal, ok := auth.FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	var request UpdateRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	tool, err := h.store.Update(
		r.Context(), principal.WorkspaceID, principal.UserID,
		r.PathValue("definition_id"), request,
	)
	if err != nil {
		httpbase.Error(w, http.StatusBadRequest, "tool_update_failed")
		return
	}
	if err := h.audit.Record(r.Context(), principal.WorkspaceID, principal.UserID,
		"tool.binding.updated", "tool_definition", tool.DefinitionID,
		map[string]any{"enabled": tool.Enabled, "risk_level": tool.RiskLevel},
	); err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "audit_write_failed")
		return
	}
	httpbase.JSON(w, http.StatusOK, tool)
}
