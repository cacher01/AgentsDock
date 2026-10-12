package toolcatalog

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5/pgxpool"
)

type Tool struct {
	DefinitionID  string         `json:"definition_id"`
	BindingID     *string        `json:"binding_id,omitempty"`
	Key           string         `json:"key"`
	Version       string         `json:"version"`
	AdapterType   string         `json:"adapter_type"`
	RiskLevel     string         `json:"risk_level"`
	InputSchema   map[string]any `json:"input_schema"`
	OutputSchema  map[string]any `json:"output_schema"`
	Enabled       bool           `json:"enabled"`
	Policy        map[string]any `json:"policy"`
	CredentialRef *string        `json:"credential_ref,omitempty"`
}

type UpdateRequest struct {
	Enabled       bool           `json:"enabled"`
	Policy        map[string]any `json:"policy"`
	CredentialRef *string        `json:"credential_ref"`
}

type Store struct {
	db *pgxpool.Pool
}

func NewStore(db *pgxpool.Pool) *Store {
	return &Store{db: db}
}

func (s *Store) List(ctx context.Context, workspaceID string) ([]Tool, error) {
	rows, err := s.db.Query(ctx, `
		select d.id, b.id, d.tool_key, d.version, d.adapter_type,
		       d.risk_level, d.input_schema, d.output_schema,
		       coalesce(b.enabled, false), coalesce(b.policy, '{}'::jsonb),
		       b.credential_ref
		from tool_definitions d
		left join workspace_tool_bindings b
		  on b.tool_definition_id=d.id and b.workspace_id=$1
		order by d.tool_key, d.version
	`, workspaceID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var tools []Tool
	for rows.Next() {
		var item Tool
		var input, output, policy []byte
		if err := rows.Scan(
			&item.DefinitionID, &item.BindingID, &item.Key, &item.Version,
			&item.AdapterType, &item.RiskLevel, &input, &output,
			&item.Enabled, &policy, &item.CredentialRef,
		); err != nil {
			return nil, err
		}
		_ = json.Unmarshal(input, &item.InputSchema)
		_ = json.Unmarshal(output, &item.OutputSchema)
		_ = json.Unmarshal(policy, &item.Policy)
		tools = append(tools, item)
	}
	return tools, rows.Err()
}

func (s *Store) Update(
	ctx context.Context,
	workspaceID, actorID, definitionID string,
	request UpdateRequest,
) (Tool, error) {
	if request.Policy == nil {
		request.Policy = map[string]any{}
	}
	policy, err := json.Marshal(request.Policy)
	if err != nil {
		return Tool{}, errors.New("invalid tool policy")
	}
	bindingID := "tool_binding_" + definitionID + "_" + workspaceID
	_, err = s.db.Exec(ctx, `
		insert into workspace_tool_bindings (
			id, workspace_id, tool_definition_id, credential_ref,
			enabled, policy, created_by
		) values ($1,$2,$3,$4,$5,$6,$7)
		on conflict (workspace_id, tool_definition_id) do update set
			credential_ref=excluded.credential_ref,
			enabled=excluded.enabled,
			policy=excluded.policy,
			updated_at=now()
	`, bindingID, workspaceID, definitionID, request.CredentialRef,
		request.Enabled, policy, actorID)
	if err != nil {
		return Tool{}, err
	}
	tools, err := s.List(ctx, workspaceID)
	if err != nil {
		return Tool{}, err
	}
	for _, tool := range tools {
		if tool.DefinitionID == definitionID {
			return tool, nil
		}
	}
	return Tool{}, errors.New("tool definition not found")
}
