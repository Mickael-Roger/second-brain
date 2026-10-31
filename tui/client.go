package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"time"
)

type apiError struct {
	Status int
	Detail string
}

func (e *apiError) Error() string {
	if e.Status == 401 {
		return "Session expired or invalid credentials. Please sign in again."
	}
	return fmt.Sprintf("HTTP %d: %s", e.Status, e.Detail)
}

type client struct {
	base     *url.URL
	http     *http.Client
	external *http.Client
	debug    *log.Logger
}

func newClient(address string) (*client, error) {
	u, err := url.Parse(address)
	if err != nil || u == nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" {
		return nil, fmt.Errorf("server URL must start with http:// or https://")
	}
	if u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("server URL must not contain credentials, a query or a fragment")
	}
	u.Path = strings.TrimRight(u.Path, "/") + "/"
	u.RawPath = ""
	jar, _ := cookiejar.New(nil)
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.ResponseHeaderTimeout = 30 * time.Second
	return &client{base: u, http: &http.Client{Jar: jar, Transport: transport},
		external: &http.Client{Transport: transport, Timeout: 30 * time.Second}}, nil
}

func (c *client) endpoint(route string) string {
	r, _ := url.Parse(strings.TrimLeft(route, "/"))
	return c.base.ResolveReference(r).String()
}

func query(route string, params url.Values) string { return route + "?" + params.Encode() }

func (c *client) request(ctx context.Context, method, route string, body, out any) error {
	ctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	response, err := c.open(ctx, method, route, body)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if c.debug != nil && method == "GET" && (route == "api/anki/deck-stats" || route == "api/anki/decks" || route == "api/anki/config") {
		data, err := io.ReadAll(io.LimitReader(response.Body, 65536))
		if err != nil {
			c.debug.Printf("Anki response read failed (%T)", err)
		}
		response.Body = io.NopCloser(io.MultiReader(bytes.NewReader(data), response.Body))
		c.debug.Printf("Anki %s: %s", route, ankiDebugSummary(data))
	}
	if err := checkResponse(response); err != nil {
		return err
	}
	if out == nil || response.StatusCode == http.StatusNoContent {
		return nil
	}
	return json.NewDecoder(response.Body).Decode(out)
}

func (c *client) open(ctx context.Context, method, route string, body any) (*http.Response, error) {
	var input io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		input = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.endpoint(route), input)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	req.Header.Set("User-Agent", "second-brain-tui/go")
	start := time.Now()
	response, err := c.http.Do(req)
	if c.debug != nil {
		// Never log query parameters, headers or request bodies.
		if err != nil {
			c.debug.Printf("HTTP %s %s failed (%T) after %s", method, req.URL.EscapedPath(), err, time.Since(start).Round(time.Millisecond))
		} else {
			c.debug.Printf("HTTP %s %s -> %d (%s, %s)", method, req.URL.EscapedPath(), response.StatusCode, response.Header.Get("Content-Type"), time.Since(start).Round(time.Millisecond))
		}
	}
	return response, err
}

func checkResponse(response *http.Response) error {
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return nil
	}
	body, _ := io.ReadAll(io.LimitReader(response.Body, 8192))
	var detail struct {
		Detail any `json:"detail"`
	}
	if json.Unmarshal(body, &detail) == nil && detail.Detail != nil {
		return &apiError{response.StatusCode, fmt.Sprint(detail.Detail)}
	}
	return &apiError{response.StatusCode, string(body)}
}

type event struct {
	Name string
	Data json.RawMessage
}

func (c *client) stream(ctx context.Context, route string, body any, receive func(event) error) error {
	response, err := c.open(ctx, "POST", route, body)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if err := checkResponse(response); err != nil {
		return err
	}
	return readSSE(response.Body, receive)
}

func readSSE(reader io.Reader, receive func(event) error) error {
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 4096), 16<<20)
	name := "message"
	var lines []string
	dispatch := func() error {
		if len(lines) == 0 {
			return nil
		}
		data := json.RawMessage(strings.Join(lines, "\n"))
		if !json.Valid(data) {
			return fmt.Errorf("invalid JSON in SSE %s event", name)
		}
		return receive(event{name, data})
	}
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if err := dispatch(); err != nil {
				return err
			}
			name, lines = "message", nil
		} else if strings.HasPrefix(line, "event:") {
			name = strings.TrimPrefix(strings.TrimPrefix(line, "event:"), " ")
		} else if strings.HasPrefix(line, "data:") {
			lines = append(lines, strings.TrimPrefix(strings.TrimPrefix(line, "data:"), " "))
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	return dispatch()
}

func (c *client) image(ctx context.Context, source string) ([]byte, error) {
	if strings.HasPrefix(source, "data:") {
		header, data, ok := strings.Cut(source, ",")
		if !ok {
			return nil, fmt.Errorf("invalid image data URI")
		}
		if strings.Contains(header, ";base64") {
			return base64.StdEncoding.DecodeString(data)
		}
		text, err := url.PathUnescape(data)
		return []byte(text), err
	}
	u, err := url.Parse(source)
	if err != nil {
		return nil, err
	}
	if !u.IsAbs() {
		u, err = url.Parse(c.endpoint(source))
		if err != nil {
			return nil, err
		}
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return nil, fmt.Errorf("unsupported image URL")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", u.String(), nil)
	if err != nil {
		return nil, err
	}
	connection := c.external
	if u.Scheme == c.base.Scheme && u.Host == c.base.Host {
		connection = c.http
	}
	response, err := connection.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if err := checkResponse(response); err != nil {
		return nil, err
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, (20<<20)+1))
	if len(data) > 20<<20 {
		return nil, fmt.Errorf("image exceeds 20 MiB")
	}
	return data, err
}
