package config

import "testing"

func TestLoadReadsInputFileLimit(t *testing.T) {
	t.Setenv("PLATFORM_DATABASE_URL", "postgresql://example")
	t.Setenv("INTERNAL_API_TOKEN", "internal-token")
	t.Setenv("MODEL_MASTER_KEY", "model-key")
	t.Setenv("INPUT_FILE_MAX_BYTES", "4096")

	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InputFileMaxBytes != 4096 {
		t.Fatalf("InputFileMaxBytes = %d, want 4096", cfg.InputFileMaxBytes)
	}
}

func TestLoadRejectsInvalidInputFileLimit(t *testing.T) {
	t.Setenv("INPUT_FILE_MAX_BYTES", "0")

	if _, err := Load(); err == nil {
		t.Fatal("expected invalid INPUT_FILE_MAX_BYTES to fail")
	}
}
