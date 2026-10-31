package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestChatHistoryByYearMonthAndLastActivity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var result any = object{}
		switch r.URL.Path {
		case "/api/llm/providers":
			result = []provider{}
		case "/api/chats":
			result = []chat{
				{ID: "old", UpdatedAt: "2025-12-31T23:00:00Z", Model: "same"},
				{ID: "oct-early", UpdatedAt: "2026-10-01T10:00:00", Model: "other"},
				{ID: "missing", UpdatedAt: ""},
				{ID: "latest", UpdatedAt: "2026-10-31T10:00:00Z", Model: "same"},
				{ID: "sept", UpdatedAt: "2026-09-30T10:00:00Z"},
			}
		}
		_ = json.NewEncoder(w).Encode(result)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return len(u.browsers[0].items) == 3 })
	updateUI(t, u, func() {
		groups := u.browsers[0].items
		if groups[0].title != "2026" || groups[1].title != "2025" || groups[2].title != "Unknown date" {
			t.Error("years are not newest first with missing dates last")
			return
		}
		months := groups[0].children
		if months[0].id != "month:2026-10" || months[1].id != "month:2026-09" {
			t.Error("months are not newest first")
		}
		if months[0].children[0].id != "latest" || months[0].children[1].id != "oct-early" {
			t.Error("conversations must be newest first, regardless of model")
		}
	})
}
