package config

import (
	"errors"
	"os"
	"strconv"
	"time"
)

type Config struct {
	Address           string
	DatabaseURL       string
	RuntimeURL        string
	CookieName        string
	CookieSecure      bool
	SessionTTL        time.Duration
	InternalAPIToken  string
	ModelMasterKey    string
	PlatformTaskAPI   bool
	InputFileMaxBytes int64
}

func Load() (Config, error) {
	ttlHours, err := strconv.Atoi(env("SESSION_TTL_HOURS", "168"))
	if err != nil || ttlHours < 1 {
		return Config{}, errors.New("SESSION_TTL_HOURS must be a positive integer")
	}
	inputFileMaxBytes, err := strconv.ParseInt(env("INPUT_FILE_MAX_BYTES", "20971520"), 10, 64)
	if err != nil || inputFileMaxBytes < 1 {
		return Config{}, errors.New("INPUT_FILE_MAX_BYTES must be a positive integer")
	}
	cfg := Config{
		Address:           env("GATEWAY_ADDRESS", ":8080"),
		DatabaseURL:       os.Getenv("PLATFORM_DATABASE_URL"),
		RuntimeURL:        env("PYTHON_RUNTIME_URL", "http://127.0.0.1:8000"),
		CookieName:        env("SESSION_COOKIE_NAME", "agent_platform_session"),
		CookieSecure:      env("SESSION_COOKIE_SECURE", "false") == "true",
		SessionTTL:        time.Duration(ttlHours) * time.Hour,
		InternalAPIToken:  os.Getenv("INTERNAL_API_TOKEN"),
		ModelMasterKey:    os.Getenv("MODEL_MASTER_KEY"),
		PlatformTaskAPI:   env("PLATFORM_TASK_API_ENABLED", "false") == "true",
		InputFileMaxBytes: inputFileMaxBytes,
	}
	if cfg.DatabaseURL == "" {
		return Config{}, errors.New("PLATFORM_DATABASE_URL is required")
	}
	if cfg.InternalAPIToken == "" {
		return Config{}, errors.New("INTERNAL_API_TOKEN is required")
	}
	if cfg.ModelMasterKey == "" {
		return Config{}, errors.New("MODEL_MASTER_KEY is required")
	}
	return cfg, nil
}

func env(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
