package modelgateway

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"

	"agentplatform/gateway/internal/secure"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Variant struct {
	ID            string         `json:"id"`
	ModelName     string         `json:"model_name"`
	Capabilities  []string       `json:"capabilities"`
	ContextWindow *int           `json:"context_window,omitempty"`
	Pricing       map[string]any `json:"pricing"`
	Enabled       bool           `json:"enabled"`
}

type Connection struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	ProviderType string    `json:"provider_type"`
	BaseURL      string    `json:"base_url"`
	Status       string    `json:"status"`
	Variants     []Variant `json:"variants"`
}

type CreateRequest struct {
	Name          string         `json:"name"`
	ProviderType  string         `json:"provider_type"`
	BaseURL       string         `json:"base_url"`
	APIKey        string         `json:"api_key"`
	ModelName     string         `json:"model_name"`
	Capabilities  []string       `json:"capabilities"`
	ContextWindow *int           `json:"context_window"`
	Pricing       map[string]any `json:"pricing"`
}

type Store struct {
	db     *pgxpool.Pool
	cipher *secure.Cipher
}

func NewStore(db *pgxpool.Pool, cipher *secure.Cipher) *Store {
	return &Store{db: db, cipher: cipher}
}

func (s *Store) Create(
	ctx context.Context, workspaceID, actorID string, request CreateRequest,
) (Connection, error) {
	if strings.TrimSpace(request.Name) == "" ||
		strings.TrimSpace(request.BaseURL) == "" ||
		strings.TrimSpace(request.APIKey) == "" ||
		strings.TrimSpace(request.ModelName) == "" {
		return Connection{}, errors.New("missing model connection fields")
	}
	if request.ProviderType == "" {
		request.ProviderType = "openai_compatible"
	}
	if request.ProviderType != "openai_compatible" {
		return Connection{}, errors.New("unsupported provider type")
	}
	encrypted, nonce, err := s.cipher.Encrypt([]byte(request.APIKey))
	if err != nil {
		return Connection{}, err
	}
	connectionID := "model_connection_" + randomID()
	variantID := "model_variant_" + randomID()
	capabilities, _ := json.Marshal(request.Capabilities)
	if request.Pricing == nil {
		request.Pricing = map[string]any{}
	}
	pricing, _ := json.Marshal(request.Pricing)
	tx, err := s.db.Begin(ctx)
	if err != nil {
		return Connection{}, err
	}
	defer tx.Rollback(ctx)
	_, err = tx.Exec(ctx, `
		insert into model_connections (
			id, workspace_id, name, provider_type, base_url,
			secret_ciphertext, secret_nonce, secret_key_version,
			status, created_by
		) values ($1,$2,$3,$4,$5,$6,$7,1,'active',$8)
	`, connectionID, workspaceID, request.Name, request.ProviderType,
		strings.TrimRight(request.BaseURL, "/"), encrypted, nonce, actorID)
	if err != nil {
		return Connection{}, err
	}
	_, err = tx.Exec(ctx, `
		insert into model_variants (
			id, connection_id, model_name, capabilities,
			context_window, pricing, enabled
		) values ($1,$2,$3,$4,$5,$6,true)
	`, variantID, connectionID, request.ModelName, capabilities,
		request.ContextWindow, pricing)
	if err != nil {
		return Connection{}, err
	}
	if err := tx.Commit(ctx); err != nil {
		return Connection{}, err
	}
	return Connection{
		ID: connectionID, Name: request.Name,
		ProviderType: request.ProviderType,
		BaseURL:      strings.TrimRight(request.BaseURL, "/"),
		Status:       "active",
		Variants: []Variant{{
			ID: variantID, ModelName: request.ModelName,
			Capabilities:  request.Capabilities,
			ContextWindow: request.ContextWindow,
			Pricing:       request.Pricing, Enabled: true,
		}},
	}, nil
}

func (s *Store) List(
	ctx context.Context, workspaceID string,
) ([]Connection, error) {
	rows, err := s.db.Query(ctx, `
		select c.id, c.name, c.provider_type, c.base_url, c.status,
		       v.id, v.model_name, v.capabilities, v.context_window,
		       v.pricing, v.enabled
		from model_connections c
		left join model_variants v on v.connection_id=c.id
		where c.workspace_id=$1
		order by lower(c.name), v.model_name
	`, workspaceID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []Connection
	index := map[string]int{}
	for rows.Next() {
		var connection Connection
		var variantID, modelName *string
		var capabilities, pricing []byte
		var contextWindow *int
		var enabled *bool
		if err := rows.Scan(
			&connection.ID, &connection.Name, &connection.ProviderType,
			&connection.BaseURL, &connection.Status, &variantID, &modelName,
			&capabilities, &contextWindow, &pricing, &enabled,
		); err != nil {
			return nil, err
		}
		position, ok := index[connection.ID]
		if !ok {
			position = len(result)
			index[connection.ID] = position
			connection.Variants = []Variant{}
			result = append(result, connection)
		}
		if variantID != nil {
			var caps []string
			var prices map[string]any
			_ = json.Unmarshal(capabilities, &caps)
			_ = json.Unmarshal(pricing, &prices)
			result[position].Variants = append(result[position].Variants, Variant{
				ID: *variantID, ModelName: *modelName,
				Capabilities: caps, ContextWindow: contextWindow,
				Pricing: prices, Enabled: *enabled,
			})
		}
	}
	return result, rows.Err()
}

type selectedModel struct {
	VariantID    string
	ModelName    string
	BaseURL      string
	Ciphertext   []byte
	Nonce        []byte
	Pricing      map[string]any
	ConnectionID string
	ProviderType string
}

func (s *Store) candidates(
	ctx context.Context, workspaceID, requestedVariant string,
) ([]selectedModel, error) {
	query := `
		select v.id, v.model_name, c.base_url, c.secret_ciphertext,
		       c.secret_nonce, v.pricing, c.id, c.provider_type
		from model_variants v
		join model_connections c on c.id=v.connection_id
		where c.workspace_id=$1 and c.status='active' and v.enabled=true
	`
	args := []any{workspaceID}
	if requestedVariant != "" {
		query += " order by case when v.id=$2 then 0 else 1 end, c.created_at, v.created_at"
		args = append(args, requestedVariant)
	} else {
		query += " order by c.created_at, v.created_at"
	}
	rows, err := s.db.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var models []selectedModel
	for rows.Next() {
		var item selectedModel
		var pricing []byte
		if err := rows.Scan(
			&item.VariantID, &item.ModelName, &item.BaseURL,
			&item.Ciphertext, &item.Nonce, &pricing,
			&item.ConnectionID, &item.ProviderType,
		); err != nil {
			return nil, err
		}
		_ = json.Unmarshal(pricing, &item.Pricing)
		models = append(models, item)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if requestedVariant != "" && (len(models) == 0 || models[0].VariantID != requestedVariant) {
		return nil, errors.New("requested model variant is not available")
	}
	return models, nil
}

func randomID() string {
	buffer := make([]byte, 16)
	if _, err := rand.Read(buffer); err != nil {
		panic("crypto/rand unavailable")
	}
	return hex.EncodeToString(buffer)
}
