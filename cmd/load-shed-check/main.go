// Command load-shed-check verifies the API's health and one priority-aware
// admission outcome without requiring curl or a load-test framework.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type config struct {
	baseURL         string
	priority        string
	expected        string
	delayMS         int
	upstreamTimeout int
}

type report struct {
	Health   string `json:"health"`
	Priority string `json:"priority"`
	Status   int    `json:"status"`
	Outcome  string `json:"outcome"`
}

var priorities = map[string]struct{}{"high": {}, "normal": {}, "low": {}}

func classify(response *http.Response) (string, error) {
	switch response.StatusCode {
	case http.StatusOK:
		return "admitted", nil
	case http.StatusTooManyRequests:
		if response.Header.Get("Retry-After") == "" {
			return "", errors.New("shed response is missing Retry-After")
		}
		return "shed", nil
	case http.StatusServiceUnavailable:
		return "breaker-open", nil
	case http.StatusBadGateway:
		return "upstream-error", nil
	case http.StatusGatewayTimeout:
		return "upstream-timeout", nil
	default:
		return "", fmt.Errorf("unexpected /client status %d", response.StatusCode)
	}
}

func check(ctx context.Context, client *http.Client, cfg config) (report, error) {
	if _, ok := priorities[cfg.priority]; !ok {
		return report{}, fmt.Errorf("priority must be high, normal, or low; got %q", cfg.priority)
	}

	base := strings.TrimRight(cfg.baseURL, "/")
	healthRequest, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/healthz", nil)
	if err != nil {
		return report{}, fmt.Errorf("build health request: %w", err)
	}
	healthResponse, err := client.Do(healthRequest)
	if err != nil {
		return report{}, fmt.Errorf("health request: %w", err)
	}
	defer healthResponse.Body.Close()
	var health struct {
		OK bool `json:"ok"`
	}
	if healthResponse.StatusCode != http.StatusOK || json.NewDecoder(io.LimitReader(healthResponse.Body, 4096)).Decode(&health) != nil || !health.OK {
		return report{}, fmt.Errorf("health check failed with status %d", healthResponse.StatusCode)
	}

	clientURL, err := url.Parse(base + "/client")
	if err != nil {
		return report{}, fmt.Errorf("parse client URL: %w", err)
	}
	query := clientURL.Query()
	query.Set("priority", cfg.priority)
	query.Set("ms", fmt.Sprint(cfg.delayMS))
	query.Set("fail_rate", "0")
	query.Set("timeout_ms", fmt.Sprint(cfg.upstreamTimeout))
	clientURL.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, clientURL.String(), nil)
	if err != nil {
		return report{}, fmt.Errorf("build client request: %w", err)
	}
	response, err := client.Do(request)
	if err != nil {
		return report{}, fmt.Errorf("client request: %w", err)
	}
	defer response.Body.Close()
	outcome, err := classify(response)
	if err != nil {
		return report{}, err
	}
	result := report{Health: "ok", Priority: cfg.priority, Status: response.StatusCode, Outcome: outcome}
	if outcome != cfg.expected {
		return result, fmt.Errorf("expected %q, observed %q", cfg.expected, outcome)
	}
	return result, nil
}

func main() {
	cfg := config{}
	requestTimeout := 3 * time.Second
	flag.StringVar(&cfg.baseURL, "base-url", "http://127.0.0.1:8080", "load-shed API base URL")
	flag.StringVar(&cfg.priority, "priority", "normal", "lab priority: high, normal, or low")
	flag.StringVar(&cfg.expected, "expect", "admitted", "expected outcome: admitted, shed, breaker-open, upstream-error, or upstream-timeout")
	flag.IntVar(&cfg.delayMS, "ms", 20, "lab-only upstream delay in milliseconds")
	flag.IntVar(&cfg.upstreamTimeout, "upstream-timeout-ms", 2000, "lab-only upstream timeout in milliseconds")
	flag.DurationVar(&requestTimeout, "request-timeout", requestTimeout, "overall checker timeout")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
	defer cancel()
	result, err := check(ctx, &http.Client{Timeout: requestTimeout}, cfg)
	if result.Health != "" {
		_ = json.NewEncoder(os.Stdout).Encode(result)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "load-shed-check:", err)
		os.Exit(1)
	}
}
