package proxy

import (
	"errors"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"

	"agentplatform/gateway/internal/auth"
	"agentplatform/gateway/internal/httpbase"
)

func Runtime(targetURL, internalToken string, platformTasks bool) (http.Handler, error) {
	target, err := url.Parse(targetURL)
	if err != nil {
		return nil, err
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.ErrorHandler = func(w http.ResponseWriter, _ *http.Request, err error) {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			httpbase.Error(w, http.StatusRequestEntityTooLarge, "request_too_large")
			return
		}
		httpbase.Error(w, http.StatusBadGateway, "runtime_unavailable")
	}
	original := proxy.Director
	proxy.Director = func(request *http.Request) {
		original(request)
		originalPath := request.URL.Path
		request.URL.Path = strings.TrimPrefix(originalPath, "/api/v1")
		if platformTasks && strings.HasPrefix(originalPath, "/api/v1/tasks") {
			request.URL.Path = "/internal/v1/platform" + request.URL.Path
		}
		if platformTasks && strings.HasPrefix(originalPath, "/api/v1/files") {
			request.URL.Path = "/internal/v1/platform" + request.URL.Path
		}
		if platformTasks && strings.HasPrefix(originalPath, "/api/v1/notifications") {
			request.URL.Path = "/internal/v1/platform" + request.URL.Path
		}
		if platformTasks && strings.HasPrefix(
			originalPath, "/api/v1/admin/governance",
		) {
			request.URL.Path = "/internal/v1/platform" +
				strings.TrimPrefix(originalPath, "/api/v1/admin")
		}
		request.Header.Set("X-Internal-Token", internalToken)
		request.Header.Set("X-Request-ID", httpbase.RequestID(request.Context()))
		if principal, ok := auth.FromContext(request.Context()); ok {
			request.Header.Set("X-User-ID", principal.UserID)
			request.Header.Set("X-Workspace-ID", principal.WorkspaceID)
			request.Header.Set("X-Workspace-Role", principal.Role)
		}
	}
	return proxy, nil
}

func LimitBody(maxBytes int64, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ContentLength > maxBytes {
			httpbase.Error(w, http.StatusRequestEntityTooLarge, "request_too_large")
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, maxBytes)
		next.ServeHTTP(w, r)
	})
}
