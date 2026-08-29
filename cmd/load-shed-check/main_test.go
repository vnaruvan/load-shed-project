package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testServer(t *testing.T, clientStatus int, retryAfter string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		switch request.URL.Path {
		case "/healthz":
			writer.Header().Set("Content-Type", "application/json")
			_, _ = writer.Write([]byte(`{"ok":true}`))
		case "/client":
			if request.URL.Query().Get("priority") != "normal" {
				t.Errorf("priority query = %q", request.URL.Query().Get("priority"))
			}
			if retryAfter != "" {
				writer.Header().Set("Retry-After", retryAfter)
			}
			writer.WriteHeader(clientStatus)
		default:
			http.NotFound(writer, request)
		}
	}))
}

func TestCheckAdmitted(t *testing.T) {
	server := testServer(t, http.StatusOK, "")
	defer server.Close()
	result, err := check(context.Background(), server.Client(), config{
		baseURL: server.URL, priority: "normal", expected: "admitted", delayMS: 20, upstreamTimeout: 2000,
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Outcome != "admitted" || result.Status != http.StatusOK {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestCheckShedRequiresRetryAfter(t *testing.T) {
	server := testServer(t, http.StatusTooManyRequests, "1")
	defer server.Close()
	_, err := check(context.Background(), server.Client(), config{
		baseURL: server.URL, priority: "normal", expected: "shed", delayMS: 20, upstreamTimeout: 2000,
	})
	if err != nil {
		t.Fatal(err)
	}

	missingHeader := testServer(t, http.StatusTooManyRequests, "")
	defer missingHeader.Close()
	if _, err := check(context.Background(), missingHeader.Client(), config{
		baseURL: missingHeader.URL, priority: "normal", expected: "shed", delayMS: 20, upstreamTimeout: 2000,
	}); err == nil {
		t.Fatal("expected missing Retry-After to fail")
	}
}

func TestCheckRejectsUnknownPriorityWithoutCallingServer(t *testing.T) {
	called := false
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { called = true }))
	defer server.Close()
	if _, err := check(context.Background(), server.Client(), config{
		baseURL: server.URL, priority: "urgent", expected: "admitted",
	}); err == nil {
		t.Fatal("expected invalid priority to fail")
	}
	if called {
		t.Fatal("invalid priority reached the server")
	}
}

func TestClassifyBoundedOutcomes(t *testing.T) {
	tests := map[int]string{
		http.StatusOK:                 "admitted",
		http.StatusServiceUnavailable: "breaker-open",
		http.StatusBadGateway:         "upstream-error",
		http.StatusGatewayTimeout:     "upstream-timeout",
	}
	for status, expected := range tests {
		outcome, err := classify(&http.Response{StatusCode: status, Header: make(http.Header)})
		if err != nil || outcome != expected {
			t.Errorf("status %d: outcome=%q err=%v", status, outcome, err)
		}
	}
}
