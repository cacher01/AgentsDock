package auth

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

type Member struct {
	UserID      string    `json:"user_id"`
	Email       string    `json:"email"`
	DisplayName string    `json:"display_name"`
	Role        string    `json:"role"`
	Status      string    `json:"status"`
	JoinedAt    time.Time `json:"joined_at"`
}

func (s *Store) CreateInvitation(
	ctx context.Context,
	actor Principal,
	email, role string,
	ttl time.Duration,
) (string, time.Time, error) {
	email = strings.TrimSpace(strings.ToLower(email))
	if email == "" || (role != "admin" && role != "member") {
		return "", time.Time{}, errors.New("invalid invitation")
	}
	token, tokenHash, err := newToken()
	if err != nil {
		return "", time.Time{}, err
	}
	expires := time.Now().UTC().Add(ttl)
	_, err = s.db.Exec(ctx, `
		insert into invitations (
			id, workspace_id, email, role, token_hash,
			expires_at, created_by
		) values ($1, $2, $3, $4, $5, $6, $7)
	`, "invitation_"+randomHex(16), actor.WorkspaceID, email, role,
		tokenHash, expires, actor.UserID)
	if err != nil {
		return "", time.Time{}, err
	}
	return token, expires, nil
}

func (s *Store) AcceptInvitation(
	ctx context.Context,
	token, displayName, password string,
) error {
	passwordHash, err := HashPassword(password)
	if err != nil {
		return err
	}
	tx, err := s.db.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	var invitationID, workspaceID, email, role string
	err = tx.QueryRow(ctx, `
		select id, workspace_id, email, role
		from invitations
		where token_hash=$1 and accepted_at is null and expires_at > now()
		for update
	`, hashToken(token)).Scan(&invitationID, &workspaceID, &email, &role)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return errors.New("invalid or expired invitation")
		}
		return err
	}

	var userID string
	err = tx.QueryRow(ctx, "select id from users where lower(email)=lower($1)", email).Scan(&userID)
	if errors.Is(err, pgx.ErrNoRows) {
		userID = "user_" + randomHex(16)
		_, err = tx.Exec(ctx, `
			insert into users (id, email, display_name, password_hash)
			values ($1, $2, $3, $4)
		`, userID, email, strings.TrimSpace(displayName), passwordHash)
	}
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		insert into workspace_memberships (
			workspace_id, user_id, role, status, joined_at
		) values ($1, $2, $3, 'active', now())
		on conflict (workspace_id, user_id) do update set
			role=excluded.role, status='active', joined_at=now()
	`, workspaceID, userID, role)
	if err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "update invitations set accepted_at=now() where id=$1", invitationID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) ListMembers(
	ctx context.Context, workspaceID string,
) ([]Member, error) {
	rows, err := s.db.Query(ctx, `
		select u.id, u.email, u.display_name, m.role, m.status,
		       coalesce(m.joined_at, m.created_at)
		from workspace_memberships m
		join users u on u.id=m.user_id
		where m.workspace_id=$1
		order by lower(u.email)
	`, workspaceID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var members []Member
	for rows.Next() {
		var member Member
		if err := rows.Scan(
			&member.UserID, &member.Email, &member.DisplayName,
			&member.Role, &member.Status, &member.JoinedAt,
		); err != nil {
			return nil, err
		}
		members = append(members, member)
	}
	return members, rows.Err()
}
