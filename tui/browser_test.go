package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/gdamore/tcell/v2"
)

func TestFuzzySearchCollapsedGroups(t *testing.T) {
	b := newResourceBrowser("test", nil)
	opened := ""
	b.set([]*resource{{id: "folder", title: "Wiki", children: []*resource{
		{id: "loose", title: "Go long and distant", open: func() { opened = "loose" }},
		{id: "exact", title: "Goland", open: func() { opened = "exact" }},
		{id: "other", title: "Python"},
	}}})
	if len(b.rows) != 1 {
		t.Fatal("groups must start collapsed")
	}
	b.filter = "gola"
	b.render()
	if len(b.rows) != 2 || b.rows[0].item.id != "exact" {
		t.Fatalf("unexpected ranked fuzzy matches: %+v", b.rows)
	}
	b.activate(0)
	if opened != "exact" {
		t.Fatal("activation did not open fuzzy result")
	}
	b.filter = "wiki python"
	b.render()
	if len(b.rows) != 1 || b.rows[0].item.id != "other" {
		t.Fatal("terms must match across parent path and title")
	}
	b.filter = "no matches"
	b.render()
	b.activate(0) // The empty-state row is never actionable.
	if len(b.rows) != 0 {
		t.Fatal("unexpected match")
	}
}

func TestCommandAndHierarchicalNavigation(t *testing.T) {
	var mu sync.Mutex
	reads := map[string]int{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		mu.Lock()
		reads[r.URL.Path]++
		mu.Unlock()
		var response any
		switch r.URL.Path {
		case "/api/auth/login":
			response = object{}
		case "/api/llm/providers":
			response = []provider{}
		case "/api/chats":
			response = []chat{{ID: "c1", Title: "First conversation"}}
		case "/api/chats/c1":
			response = chat{ID: "c1", Title: "First conversation", Messages: []message{{Role: "user", Content: []block{{Type: "text", Text: "Hello"}}}}}
		case "/api/vault/tree":
			response = []treeEntry{{Path: "Wiki/Test.md", Type: "file"}, {Path: "Training/Go/Index.md", Type: "file"}, {Path: "Training/Go/Interfaces.md", Type: "file"}}
		case "/api/vault/note":
			response = note{Path: r.URL.Query().Get("path"), Content: "# Selected note"}
		case "/api/training/themes":
			response = object{"themes": []theme{{Theme: "Go", IndexPath: "Training/Go/Index.md", Count: 1}}}
		case "/api/news/feeds":
			response = []feed{{ID: "f1", Title: "Feed", Group: "Tech"}}
		case "/api/news/categories", "/api/news/labels":
			response = []string{}
		case "/api/news/articles":
			response = []article{{ID: "a1", Title: "A fuzzy news item", FeedID: "f1", FeedTitle: "Feed", FeedGroup: "Tech"}}
		case "/api/news/articles/a1":
			response = article{ID: "a1", Title: "A fuzzy news item", Summary: "Article content", Read: true}
		case "/api/wiki-reviews/status":
			response = object{"reviewed_today_count": 0}
		case "/api/anki/config":
			response = object{"enabled": false}
		default:
			t.Errorf("unexpected route: %s", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	key := func(k tcell.Key) { u.app.QueueEvent(tcell.NewEventKey(k, 0, tcell.ModNone)) }
	typeText := func(text string) {
		for _, r := range text {
			u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, r, tcell.ModNone))
		}
	}
	command := func(text string, index int) {
		typeText(":" + text)
		key(tcell.KeyEnter)
		awaitUI(t, u, func() bool { return u.active == index && len(u.browsers[index].items) > 0 && !u.commandOpen })
	}
	awaitUI(t, u, func() bool { return u.authenticated && len(u.browsers[0].rows) == 1 })
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return len(u.browsers[0].rows) == 2 })
	key(tcell.KeyDown)
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return len(u.browsers[0].rows) == 3 })
	key(tcell.KeyDown)
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return u.browsers[0].detail && strings.Contains(u.chat.text, "Hello") })
	key(tcell.KeyEscape)
	awaitUI(t, u, func() bool { return !u.browsers[0].detail })
	typeText("1")
	awaitUI(t, u, func() bool { return u.active == 1 })
	typeText("0")
	awaitUI(t, u, func() bool { return u.active == 0 && !u.browsers[0].detail })
	typeText("c")
	typeText("012345")
	awaitUI(t, u, func() bool { return u.active == 0 && u.chat.composer.GetText() == "012345" })
	key(tcell.KeyEscape)
	command("news", 3)
	key(tcell.KeyEnter) // Category.
	awaitUI(t, u, func() bool { return len(u.browsers[3].rows) == 2 })
	key(tcell.KeyDown)
	key(tcell.KeyEnter) // Feed.
	awaitUI(t, u, func() bool { return len(u.browsers[3].rows) == 3 })
	mu.Lock()
	count := reads["/api/news/articles/a1"]
	mu.Unlock()
	if count != 0 {
		t.Fatal("expansion fetched an article before explicit activation")
	}
	key(tcell.KeyDown)
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return u.browsers[3].detail && u.news.article.ID == "a1" })
	key(tcell.KeyEscape)
	command("training", 2)
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return len(u.browsers[2].rows) == 3 })
	key(tcell.KeyDown)
	key(tcell.KeyDown)
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return u.browsers[2].detail && u.training.parent.GetText() == "Training/Go/Interfaces.md" })
	key(tcell.KeyEscape)
	for _, target := range []struct {
		key   string
		index int
	}{{"3", 3}, {"2", 2}, {"1", 1}} {
		typeText(target.key)
		awaitUI(t, u, func() bool { return u.active == target.index })
	}
	typeText(":chat /012345")
	awaitUI(t, u, func() bool { return u.command.GetText() == "chat /012345" })
	updateUI(t, u, func() {
		if u.active != 1 {
			t.Error("digits inside the command prompt switched pages")
		}
	})
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return u.active == 0 && u.browsers[0].filter == "012345" })
}
