package auditlog

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"agentplatform/gateway/internal/auth"
	"agentplatform/gateway/internal/httpbase"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Entry struct {
	ID           string         `json:"id"`
	ActorUserID  *string        `json:"actor_user_id,omitempty"`
	Action       string         `json:"action"`
	ResourceType string         `json:"resource_type"`
	ResourceID   *string        `json:"resource_id,omitempty"`
	Metadata     map[string]any `json:"metadata"`
	CreatedAt    time.Time      `json:"created_at"`
}

type Store struct {
	db *pgxpool.Pool
}

func NewStore(db *pgxpool.Pool) *Store {
	return &Store{db: db}
}

func (s *Store) Record(
	ctx context.Context,
	workspaceID, actorID, action, resourceType, resourceID string,
	metadata map[string]any,
) error {
	if metadata == nil {
		metadata = map[string]any{}
	}
	encoded, err := json.Marshal(metadata)
	if err != nil {
		return err
	}
	_, err = s.db.Exec(ctx, `
		insert into audit_logs (
			id, workspace_id, actor_user_id, source_service,
			action, resource_type, resource_id, metadata
		) values ($1,$2,$3,'gateway',$4,$5,$6,$7)
	`, "audit_"+randomID(), workspaceID, actorID, action, resourceType,
		nullable(resourceID), encoded)
	return err
}

func (s *Store) List(
	ctx context.Context, workspaceID string, limit int,
) ([]Entry, error) {
	if limit < 1 || limit > 200 {
		limit = 100
	}
	rows, err := s.db.Query(ctx, `
		select id, actor_user_id, action, resource_type, resource_id,
		       metadata, created_at
		from audit_logs
		where workspace_id=$1
		order by created_at desc
		limit $2
	`, workspaceID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var entries []Entry
	for rows.Next() {
		var item Entry
		var raw []byte
		if err := rows.Scan(
			&item.ID, &item.ActorUserID, &item.Action, &item.ResourceType,
			&item.ResourceID, &raw, &item.CreatedAt,
		); err != nil {
			return nil, err
		}
		_ = json.Unmarshal(raw, &item.Metadata)
		entries = append(entries, item)
	}
	return entries, rows.Err()
}

type HTTP struct {
	store *Store
}

func NewHTTP(store *Store) *HTTP {
	return &HTTP{store: store}
}

func (h *HTTP) List(w http.ResponseWriter, r *http.Request) {
	principal, ok := auth.FromContext(r.Context())
	if !ok {
		httpbase.Error(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	if principal.Role != "admin" {
		httpbase.Error(w, http.StatusForbidden, "administrator_required")
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	items, err := h.store.List(r.Context(), principal.WorkspaceID, limit)
	if err != nil {
		httpbase.Error(w, http.StatusInternalServerError, "audit_unavailable")
		return
	}
	httpbase.JSON(w, http.StatusOK, map[string]any{"items": items})
}

func nullable(value string) any {
	if value == "" {
		return nil
	}
	return value
}

func randomID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		panic("crypto/rand unavailable")
	}
	return hex.EncodeToString(buffer)
}
