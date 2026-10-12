package proxy

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLimitBodyRejectsDeclaredOversizeBeforeProxying(t *testing.T) {
	called := false
	handler := LimitBody(4, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		called = true
		w.WriteHeader(http.StatusNoContent)
	}))
	request := httptest.NewRequest(http.MethodPost, "/api/v1/files", strings.NewReader("12345"))
	recorder := httptest.NewRecorder()

	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusRequestEntityTooLarge)
	}
	if called {
		t.Fatal("oversize request reached the next handler")
	}
}

func TestLimitBodyRejectsChunkedOversizeThroughReverseProxy(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()

	runtime, err := Runtime(upstream.URL, "internal-token", true)
	if err != nil {
		t.Fatal(err)
	}
	handler := LimitBody(4, runtime)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/files", strings.NewReader("12345"))
	request.ContentLength = -1
	recorder := httptest.NewRecorder()

	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusRequestEntityTooLarge)
	}
}

func TestLimitBodyAllowsPayloadAtLimit(t *testing.T) {
	var received string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
		}
		received = string(body)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer upstream.Close()

	runtime, err := Runtime(upstream.URL, "internal-token", true)
	if err != nil {
		t.Fatal(err)
	}
	handler := LimitBody(4, runtime)
	request := httptest.NewRequest(http.MethodPost, "/api/v1/files", strings.NewReader("1234"))
	recorder := httptest.NewRecorder()

	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusNoContent)
	}
	if received != "1234" {
		t.Fatalf("received %q, want exact payload", received)
	}
}
