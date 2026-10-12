package server

import (
	"encoding/base64"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"agentplatform/gateway/internal/config"
)

func testHandler(t *testing.T) (http.Handler, *int) {
	t.Helper()
	proxyCalls := 0
	runtime := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		proxyCalls++
		w.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(runtime.Close)
	cfg := config.Config{
		RuntimeURL:        runtime.URL,
		CookieName:        "test_session",
		SessionTTL:        time.Hour,
		InternalAPIToken:  "internal-token",
		ModelMasterKey:    base64.StdEncoding.EncodeToString(make([]byte, 32)),
		PlatformTaskAPI:   true,
		InputFileMaxBytes: 1024,
	}
	handler, err := New(cfg, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return handler, &proxyCalls
}

func TestPublicRoutesDoNotExposeLegacyRuntimeAPIs(t *testing.T) {
	handler, proxyCalls := testHandler(t)
	for _, path := range []string{
		"/tasks",
		"/tasks/task_1",
		"/sessions",
		"/results/task_1",
		"/replay/task_1",
		"/artifacts/task_1",
	} {
		t.Run(path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
			if recorder.Code != http.StatusNotFound {
				t.Fatalf("status = %d, want %d", recorder.Code, http.StatusNotFound)
			}
		})
	}
	if *proxyCalls != 0 {
		t.Fatalf("legacy requests reached runtime %d times", *proxyCalls)
	}
}

func TestDeclaredPublicRoutesRemainAvailable(t *testing.T) {
	handler, proxyCalls := testHandler(t)
	for _, path := range []string{"/", "/ui", "/static/app.js"} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusNoContent {
			t.Fatalf("%s status = %d, want %d", path, recorder.Code, http.StatusNoContent)
		}
	}
	if *proxyCalls != 3 {
		t.Fatalf("public proxy calls = %d, want 3", *proxyCalls)
	}
}

func TestPlatformAndAdminRoutesRequireAuthentication(t *testing.T) {
	handler, proxyCalls := testHandler(t)
	for _, path := range []string{
		"/api/v1/tasks",
		"/api/v1/files",
		"/api/v1/admin/governance/tasks",
	} {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusUnauthorized {
			t.Fatalf("%s status = %d, want %d", path, recorder.Code, http.StatusUnauthorized)
		}
	}
	if *proxyCalls != 0 {
		t.Fatalf("unauthenticated requests reached runtime %d times", *proxyCalls)
	}
}
