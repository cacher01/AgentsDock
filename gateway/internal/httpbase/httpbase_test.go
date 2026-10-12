package httpbase

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRequestIDIsGeneratedAndExposed(t *testing.T) {
	var observed string
	handler := WithRequestID(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		observed = RequestID(r.Context())
		w.WriteHeader(http.StatusNoContent)
	}))
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/", nil))
	if observed == "" || recorder.Header().Get("X-Request-ID") != observed {
		t.Fatal("request id was not propagated")
	}
}
