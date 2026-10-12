package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"os"
	"strings"

	"agentplatform/gateway/internal/auth"
	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	if err := run(context.Background()); err != nil {
		log.Fatal(err)
	}
}

func run(ctx context.Context) error {
	databaseURL := os.Getenv("PLATFORM_DATABASE_URL")
	email := strings.TrimSpace(os.Getenv("BOOTSTRAP_ADMIN_EMAIL"))
	password := os.Getenv("BOOTSTRAP_ADMIN_PASSWORD")
	displayName := strings.TrimSpace(os.Getenv("BOOTSTRAP_ADMIN_NAME"))
	workspaceName := strings.TrimSpace(os.Getenv("BOOTSTRAP_WORKSPACE_NAME"))
	workspaceSlug := strings.ToLower(strings.TrimSpace(
		os.Getenv("BOOTSTRAP_WORKSPACE_SLUG"),
	))
	if databaseURL == "" || email == "" || password == "" ||
		workspaceName == "" || workspaceSlug == "" {
		return errors.New(
			"PLATFORM_DATABASE_URL, BOOTSTRAP_ADMIN_EMAIL, " +
				"BOOTSTRAP_ADMIN_PASSWORD, BOOTSTRAP_WORKSPACE_NAME, and " +
				"BOOTSTRAP_WORKSPACE_SLUG are required",
		)
	}
	if displayName == "" {
		displayName = email
	}
	hash, err := auth.HashPassword(password)
	if err != nil {
		return err
	}
	db, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return err
	}
	defer db.Close()
	tx, err := db.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	userID := "user_" + randomHex(16)
	err = tx.QueryRow(ctx, `
		insert into users (
			id, email, display_name, password_hash, status
		) values ($1,$2,$3,$4,'active')
		on conflict (lower(email)) do update set
			display_name=excluded.display_name,
			password_hash=excluded.password_hash,
			status='active',
			updated_at=now()
		returning id
	`, userID, email, displayName, hash).Scan(&userID)
	if err != nil {
		return fmt.Errorf("upsert admin: %w", err)
	}

	workspaceID := "workspace_" + randomHex(16)
	err = tx.QueryRow(ctx, `
		insert into workspaces (id, name, slug, created_by)
		values ($1,$2,$3,$4)
		on conflict (lower(slug)) do update set
			name=excluded.name,
			updated_at=now()
		returning id
	`, workspaceID, workspaceName, workspaceSlug, userID).Scan(&workspaceID)
	if err != nil {
		return fmt.Errorf("upsert workspace: %w", err)
	}
	_, err = tx.Exec(ctx, `
		insert into workspace_memberships (
			workspace_id, user_id, role, status, joined_at
		) values ($1,$2,'admin','active',now())
		on conflict (workspace_id, user_id) do update set
			role='admin', status='active',
			joined_at=coalesce(workspace_memberships.joined_at, now())
	`, workspaceID, userID)
	if err != nil {
		return fmt.Errorf("upsert membership: %w", err)
	}
	_, err = tx.Exec(ctx, `
		insert into workspace_budget_policies (
			workspace_id, updated_by
		) values ($1, $2)
		on conflict (workspace_id) do nothing
	`, workspaceID, userID)
	if err != nil {
		return fmt.Errorf("seed workspace budget: %w", err)
	}
	_, err = tx.Exec(ctx,
		"insert into workspace_tool_bindings ("+
			"id, workspace_id, tool_definition_id, enabled, policy, created_by"+
			") select 'tool_binding_' || $1 || '_' || d.id, "+
			"$1, d.id, true, '{}'::jsonb, $2 "+
			"from tool_definitions d where d.tool_key in ("+
			"'current_time','calculator','date_calculator',"+
			"'unit_converter','query_planner','citation_checker',"+
			"'result_critic','artifact_writer','artifact_archiver'"+
			") on conflict (workspace_id, tool_definition_id) do nothing",
		workspaceID, userID,
	)
	if err != nil {
		return fmt.Errorf("seed workspace tools: %w", err)
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}
	fmt.Printf("admin=%s workspace_id=%s workspace_slug=%s\n",
		email, workspaceID, workspaceSlug)
	return nil
}

func randomHex(size int) string {
	raw := make([]byte, size)
	if _, err := rand.Read(raw); err != nil {
		panic(err)
	}
	return hex.EncodeToString(raw)
}
