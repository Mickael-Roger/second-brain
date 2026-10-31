package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestServerURL(t *testing.T) {
	for _, address := range []string{"file:///tmp/brain", "brain.example", "https://user:pass@brain.test", "https://brain.test/?x=1", "https://brain.test/#view"} {
		if _, err := newClient(address); err == nil {
			t.Errorf("accepted invalid URL %q", address)
		}
	}
	c, err := newClient("https://brain.test/prefix")
	if err != nil {
		t.Fatal(err)
	}
	if got := c.endpoint("/api/auth/login"); got != "https://brain.test/prefix/api/auth/login" {
		t.Fatal(got)
	}
}

func TestSessionPrefixAndSSE(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/prefix/api/auth/login":
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "secret", Path: "/"})
			fmt.Fprint(w, `{}`)
		case "/prefix/api/chat":
			cookie, err := r.Cookie("session")
			if err != nil || cookie.Value != "secret" {
				t.Error("session cookie missing")
			}
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, ": heartbeat\r\n\r\nevent: chat\r\ndata: {\"id\":\"c1\"}\r\n\r\nevent: text_delta\ndata: {\ndata: \"text\":\"bonjour\"}\n\nevent: done\ndata: {}")
		default:
			t.Errorf("unexpected route %s", r.URL.Path)
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	c, _ := newClient(server.URL + "/prefix")
	ctx := context.Background()
	if err := c.request(ctx, "POST", "api/auth/login", object{}, nil); err != nil {
		t.Fatal(err)
	}
	var events []event
	if err := c.stream(ctx, "/api/chat", object{}, func(ev event) error { events = append(events, ev); return nil }); err != nil {
		t.Fatal(err)
	}
	if len(events) != 3 || events[0].Name != "chat" || !strings.Contains(string(events[1].Data), "bonjour") || events[2].Name != "done" {
		t.Fatalf("events: %+v", events)
	}
}

func TestSSEFailuresAndCancellation(t *testing.T) {
	if err := readSSE(strings.NewReader("event: text_delta\ndata: broken\n\n"), func(event) error { return nil }); err == nil {
		t.Fatal("accepted malformed JSON")
	}
	want := errors.New("stop receiving")
	if err := readSSE(strings.NewReader("event: done\ndata: {}\n\n"), func(event) error { return want }); !errors.Is(err, want) {
		t.Fatal(err)
	}
	ready := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprint(w, ": heartbeat\n\n")
		w.(http.Flusher).Flush()
		close(ready)
		<-r.Context().Done()
	}))
	defer server.Close()
	c, _ := newClient(server.URL)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	finished := make(chan error, 1)
	go func() { finished <- c.stream(ctx, "api/chat", object{}, func(event) error { return nil }) }()
	<-ready
	cancel()
	if err := <-finished; !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation: %v", err)
	}
}

func TestImageAuthenticationAndExternalCookieIsolation(t *testing.T) {
	external := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Cookie") != "" {
			t.Error("session leaked to external image server")
		}
		fmt.Fprint(w, "external image")
	}))
	defer external.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/prefix/login" {
			http.SetCookie(w, &http.Cookie{Name: "session", Value: "secret", Path: "/"})
			fmt.Fprint(w, `{}`)
			return
		}
		if _, err := r.Cookie("session"); err != nil {
			t.Error("vault image missing authentication")
		}
		fmt.Fprint(w, "vault image")
	}))
	defer server.Close()
	c, _ := newClient(server.URL + "/prefix")
	ctx := context.Background()
	if err := c.request(ctx, "POST", "login", nil, nil); err != nil {
		t.Fatal(err)
	}
	if data, err := c.image(ctx, "api/vault/file?path=chart.png"); err != nil || string(data) != "vault image" {
		t.Fatalf("image: %q %v", data, err)
	}
	if data, err := c.image(ctx, external.URL+"/image.png"); err != nil || string(data) != "external image" {
		t.Fatalf("external image: %q %v", data, err)
	}
	if data, err := c.image(ctx, "data:image/png;base64,aW1hZ2U="); err != nil || string(data) != "image" {
		t.Fatalf("inline image: %q %v", data, err)
	}
}

func TestHTTPErrorDetail(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(409)
		fmt.Fprint(w, `{"detail":"note already locked"}`)
	}))
	defer server.Close()
	c, _ := newClient(server.URL)
	var api *apiError
	err := c.request(context.Background(), "PUT", "api/vault/note", nil, nil)
	if !errors.As(err, &api) || api.Status != 409 || api.Detail != "note already locked" {
		t.Fatal(err)
	}
}
