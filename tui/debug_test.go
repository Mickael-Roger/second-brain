package main

import (
	"context"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDebugAnkiAndCredentials(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Set-Cookie", "session=secret-cookie")
		if r.URL.Path == "/api/anki/deck-stats" {
			_, _ = w.Write([]byte(`[{"name":"Test","review_count":3,"total_in_deck":42,"front":"private-card"}]`))
		} else {
			_, _ = w.Write([]byte(`{"password":"secret-password"}`))
		}
	}))
	defer server.Close()
	c, err := newClient(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	trace := &debugBuffer{}
	c.debug = log.New(trace, "", 0)
	if err := c.request(context.Background(), "POST", "api/auth/login?token=secret-token", object{"password": "secret-password"}, nil); err != nil {
		t.Fatal(err)
	}
	var decks []ankiDeck
	if err := c.request(context.Background(), "GET", "api/anki/deck-stats", nil, &decks); err != nil {
		t.Fatal(err)
	}
	if len(decks) != 1 || decks[0].Total != 42 {
		t.Fatal("debug must preserve response decoding")
	}
	text := trace.String()
	for _, secret := range []string{"secret-password", "secret-cookie", "secret-token", "private-card"} {
		if strings.Contains(text, secret) {
			t.Errorf("debug leaked %s", secret)
		}
	}
	for _, expected := range []string{"POST /api/auth/login -> 200", "GET /api/anki/deck-stats -> 200", `"review_count":3`, `"total_in_deck":42`} {
		if !strings.Contains(text, expected) {
			t.Errorf("missing diagnostic %s", expected)
		}
	}
}

func TestDebugMissingFieldsAndBound(t *testing.T) {
	summary := ankiDebugSummary([]byte(`[{"name":"Test"}]`))
	if strings.Contains(summary, "total_in_deck") {
		t.Error("missing fields must not become zero counts")
	}
	b := &debugBuffer{}
	_, _ = b.Write([]byte("oldest\n"))
	for i := 0; i < 500; i++ {
		_, _ = b.Write([]byte("recent\n"))
	}
	if len(b.lines) != 500 || strings.Contains(b.String(), "oldest") {
		t.Error("trace must retain only the latest 500 entries")
	}
}
