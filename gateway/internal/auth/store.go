package auth

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Principal struct {
	UserID      string `json:"user_id"`
	Email       string `json:"email"`
	DisplayName string `json:"display_name"`
	WorkspaceID string `json:"workspace_id"`
	Role        string `json:"role"`
}

type Store struct {
	db *pgxpool.Pool
}

func NewStore(db *pgxpool.Pool) *Store {
	return &Store{db: db}
}

func (s *Store) Login(ctx context.Context, email, password, workspaceID string, ttl time.Duration) (Principal, string, error) {
	var userID, canonicalEmail, displayName, passwordHash string
	err := s.db.QueryRow(ctx, `
		select id, email, display_name, password_hash
		from users where lower(email)=lower($1) and status='active'
	`, strings.TrimSpace(email)).Scan(&userID, &canonicalEmail, &displayName, &passwordHash)
	if err != nil || !VerifyPassword(passwordHash, password) {
		return Principal{}, "", errors.New("invalid credentials")
	}
	principal, err := s.resolveMembership(ctx, userID, canonicalEmail, displayName, workspaceID)
	if err != nil {
		return Principal{}, "", errors.New("active workspace membership required")
	}
	token, tokenHash, err := newToken()
	if err != nil {
		return Principal{}, "", err
	}
	_, err = s.db.Exec(ctx, `
		insert into auth_sessions (id, user_id, token_hash, expires_at, last_seen_at)
		values ($1, $2, $3, $4, now())
	`, "session_"+randomHex(16), userID, tokenHash, time.Now().UTC().Add(ttl))
	if err != nil {
		return Principal{}, "", err
	}
	return principal, token, nil
}

func (s *Store) Resolve(ctx context.Context, token, workspaceID string) (Principal, error) {
	hash := hashToken(token)
	var userID, email, displayName string
	err := s.db.QueryRow(ctx, `
		select u.id, u.email, u.display_name
		from auth_sessions s join users u on u.id=s.user_id
		where s.token_hash=$1 and s.revoked_at is null
		  and s.expires_at > now() and u.status='active'
	`, hash).Scan(&userID, &email, &displayName)
	if err != nil {
		return Principal{}, errors.New("invalid session")
	}
	principal, err := s.resolveMembership(ctx, userID, email, displayName, workspaceID)
	if err != nil {
		return Principal{}, err
	}
	_, _ = s.db.Exec(ctx, "update auth_sessions set last_seen_at=now() where token_hash=$1", hash)
	return principal, nil
}

func (s *Store) Logout(ctx context.Context, token string) error {
	_, err := s.db.Exec(ctx, `
		update auth_sessions set revoked_at=now()
		where token_hash=$1 and revoked_at is null
	`, hashToken(token))
	return err
}

func (s *Store) resolveMembership(ctx context.Context, userID, email, displayName, workspaceID string) (Principal, error) {
	query := `
		select workspace_id, role
		from workspace_memberships
		where user_id=$1 and status='active'
	`
	args := []any{userID}
	if workspaceID != "" {
		query += " and workspace_id=$2"
		args = append(args, workspaceID)
	}
	query += " order by created_at limit 1"
	var resolvedWorkspace, role string
	if err := s.db.QueryRow(ctx, query, args...).Scan(&resolvedWorkspace, &role); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return Principal{}, errors.New("membership not found")
		}
		return Principal{}, err
	}
	return Principal{
		UserID: userID, Email: email, DisplayName: displayName,
		WorkspaceID: resolvedWorkspace, Role: role,
	}, nil
}

func newToken() (string, string, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", "", err
	}
	token := base64.RawURLEncoding.EncodeToString(raw)
	return token, hashToken(token), nil
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func randomHex(size int) string {
	raw := make([]byte, size)
	if _, err := rand.Read(raw); err != nil {
		panic(err)
	}
	return hex.EncodeToString(raw)
}
