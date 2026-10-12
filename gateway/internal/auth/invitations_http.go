package auth

import (
	"encoding/json"
	"net/http"
	"time"

	"agentplatform/gateway/internal/httpbase"
)

func (h *HTTP) CreateInvitation(w http.ResponseWriter, r *http.Request) {
	principal, ok := FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	var request struct {
		Email string `json:"email"`
		Role  string `json:"role"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	token, expires, err := h.store.CreateInvitation(
		r.Context(), principal, request.Email, request.Role, 72*time.Hour,
	)
	if err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invitation_failed")
		return
	}
	httpbase.JSON(w, http.StatusCreated, map[string]any{
		"token": token, "expires_at": expires,
	})
}

func (h *HTTP) AcceptInvitation(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Token       string `json:"token"`
		DisplayName string `json:"display_name"`
		Password    string `json:"password"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&request); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	if err := h.store.AcceptInvitation(
		r.Context(), request.Token, request.DisplayName, request.Password,
	); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invitation_invalid")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (h *HTTP) ListMembers(w http.ResponseWriter, r *http.Request) {
	principal, ok := FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	members, err := h.store.ListMembers(r.Context(), principal.WorkspaceID)
	if err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "members_unavailable")
		return
	}
	httpbase.JSON(w, http.StatusOK, map[string]any{"items": members})
}
