package budget

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"agentplatform/gateway/internal/auditlog"
	"agentplatform/gateway/internal/auth"
	"agentplatform/gateway/internal/httpbase"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	DefaultTaskTokenLimit = int64(100000)
	DefaultTaskCostLimit  = 2.0
	DefaultDailyCostLimit = 20.0
)

type Policy struct {
	TaskTokenSoftLimit int64   `json:"task_token_soft_limit"`
	TaskCostSoftLimit  float64 `json:"task_cost_soft_limit"`
	DailyCostSoftLimit float64 `json:"daily_cost_soft_limit"`
}

type Store struct{ db *pgxpool.Pool }

func NewStore(db *pgxpool.Pool) *Store { return &Store{db: db} }

func (s *Store) Get(ctx context.Context, workspaceID string) (Policy, error) {
	policy := Policy{
		TaskTokenSoftLimit: DefaultTaskTokenLimit,
		TaskCostSoftLimit:  DefaultTaskCostLimit,
		DailyCostSoftLimit: DefaultDailyCostLimit,
	}
	err := s.db.QueryRow(ctx, `
		select task_token_soft_limit, task_cost_soft_limit,
		       daily_cost_soft_limit
		from workspace_budget_policies where workspace_id=$1
	`, workspaceID).Scan(
		&policy.TaskTokenSoftLimit, &policy.TaskCostSoftLimit,
		&policy.DailyCostSoftLimit,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return policy, nil
	}
	return policy, err
}

func (s *Store) Update(
	ctx context.Context, workspaceID, actorID string, policy Policy,
) (Policy, error) {
	if policy.TaskTokenSoftLimit <= 0 || policy.TaskCostSoftLimit <= 0 ||
		policy.DailyCostSoftLimit <= 0 {
		return Policy{}, errors.New("soft budget limits must be positive")
	}
	_, err := s.db.Exec(ctx, `
		insert into workspace_budget_policies (
			workspace_id, task_token_soft_limit, task_cost_soft_limit,
			daily_cost_soft_limit, updated_by
		) values ($1,$2,$3,$4,$5)
		on conflict (workspace_id) do update set
			task_token_soft_limit=excluded.task_token_soft_limit,
			task_cost_soft_limit=excluded.task_cost_soft_limit,
			daily_cost_soft_limit=excluded.daily_cost_soft_limit,
			updated_by=excluded.updated_by, updated_at=now()
	`, workspaceID, policy.TaskTokenSoftLimit, policy.TaskCostSoftLimit,
		policy.DailyCostSoftLimit, actorID)
	return policy, err
}

type HTTP struct {
	store *Store
	audit *auditlog.Store
}

func NewHTTP(store *Store, audit *auditlog.Store) *HTTP {
	return &HTTP{store: store, audit: audit}
}

func adminPrincipal(w http.ResponseWriter, r *http.Request) (auth.Principal, bool) {
	principal, ok := auth.FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return auth.Principal{}, false
	}
	if principal.Role != "admin" {
		httpbase.Error(w, http.StatusForbidden, "administrator_required")
		return auth.Principal{}, false
	}
	return principal, true
}

func (h *HTTP) Get(w http.ResponseWriter, r *http.Request) {
	principal, ok := adminPrincipal(w, r)
	if !ok {
		return
	}
	policy, err := h.store.Get(r.Context(), principal.WorkspaceID)
	if err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "budget_unavailable")
		return
	}
	httpbase.JSON(w, http.StatusOK, policy)
}

func (h *HTTP) Update(w http.ResponseWriter, r *http.Request) {
	principal, ok := adminPrincipal(w, r)
	if !ok {
		return
	}
	var policy Policy
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&policy); err != nil {
		httpbase.Error(w, http.StatusBadRequest, "invalid_request")
		return
	}
	updated, err := h.store.Update(
		r.Context(), principal.WorkspaceID, principal.UserID, policy,
	)
	if err != nil {
		httpbase.Error(w, http.StatusBadRequest, "budget_update_failed")
		return
	}
	if err := h.audit.Record(r.Context(), principal.WorkspaceID, principal.UserID,
		"budget.policy.updated", "workspace_budget_policy", principal.WorkspaceID,
		map[string]any{
			"task_token_soft_limit": updated.TaskTokenSoftLimit,
			"task_cost_soft_limit":  updated.TaskCostSoftLimit,
			"daily_cost_soft_limit": updated.DailyCostSoftLimit,
		},
	); err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "audit_write_failed")
		return
	}
	httpbase.JSON(w, http.StatusOK, updated)
}
