package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

func updateUI(t *testing.T, u *ui, fn func()) {
	t.Helper()
	done := make(chan struct{})
	go func() { u.app.QueueUpdateDraw(fn); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("terminal event loop stalled")
	}
}

func awaitUI(t *testing.T, u *ui, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		var ready bool
		var status string
		updateUI(t, u, func() { ready = condition(); status = u.status.GetText(false) })
		if ready {
			return
		}
		if time.Now().Add(20 * time.Millisecond).After(deadline) {
			t.Fatalf("UI condition not met; status: %s", status)
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("UI condition not met")
}

func startUI(t *testing.T, address string) *ui {
	t.Helper()
	c, err := newClient(address)
	if err != nil {
		t.Fatal(err)
	}
	u := newUI(c, "test", "off")
	screen := tcell.NewSimulationScreen("UTF-8")
	u.app.SetScreen(screen)
	screen.SetSize(170, 50)
	finished := make(chan error, 1)
	go func() { finished <- u.run() }()
	t.Cleanup(func() {
		// A quit key already stops the event loop. Do not call Stop again while
		// tview is tearing down its screen after that first stop.
		if u.ctx.Err() == nil {
			u.cancel()
			u.app.Stop()
		}
		select {
		case err := <-finished:
			if err != nil {
				t.Error(err)
			}
		case <-time.After(6 * time.Second):
			t.Error("terminal did not shut down")
		}
	})
	updateUI(t, u, func() {})
	return u
}

func frontForm(u *ui) *tview.Form {
	_, primitive := u.pages.GetFrontPage()
	var form *tview.Form
	var find func(tview.Primitive)
	find = func(p tview.Primitive) {
		if f, ok := p.(*tview.Form); ok {
			form = f
			return
		}
		if flex, ok := p.(*tview.Flex); ok {
			for i := 0; i < flex.GetItemCount(); i++ {
				if child := flex.GetItem(i); child != nil {
					find(child)
				}
			}
		}
	}
	find(primitive)
	return form
}

func signIn(t *testing.T, u *ui, password string) {
	t.Helper()
	updateUI(t, u, func() {
		// Submit from the password prompt, as with the keyboard.
		form := frontForm(u)
		if form == nil {
			t.Error("login form missing")
			return
		}
		form.GetFormItem(1).(*tview.InputField).SetText(password)
		if form.GetButtonCount() != 0 {
			t.Error("login should only contain username/password prompts")
		}
		form.SetFocus(1)
		u.app.SetFocus(form)
	})
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
}

func TestTerminalWorkflows(t *testing.T) {
	var mutex sync.Mutex
	writes := map[string][]object{}
	content := "# Test\n\nOriginal"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mutex.Lock()
		defer mutex.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if r.Method != "GET" {
			var body object
			if r.Body != nil {
				_ = json.NewDecoder(r.Body).Decode(&body)
			}
			writes[r.URL.Path] = append(writes[r.URL.Path], body)
			switch r.URL.Path {
			case "/api/auth/login":
				if body["password"] != "password" {
					w.WriteHeader(401)
					fmt.Fprint(w, `{"detail":"invalid credentials"}`)
					return
				}
				http.SetCookie(w, &http.Cookie{Name: "session", Value: "test", Path: "/"})
			case "/api/vault/edit/lock":
				if r.Method == "POST" {
					fmt.Fprint(w, `{"token":"lock-token"}`)
				} else {
					w.WriteHeader(204)
				}
				return
			case "/api/vault/note":
				if body["token"] != "lock-token" {
					t.Error("missing lock token")
				}
				if body["content"] == "# Conflict" {
					w.WriteHeader(409)
					fmt.Fprint(w, `{"detail":"git conflict"}`)
					return
				}
				content = body["content"].(string)
				_ = json.NewEncoder(w).Encode(note{Path: "Wiki/Test.md", Content: content})
				return
			case "/api/chat":
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "event: chat\ndata: {\"id\":\"c1\"}\n\nevent: tool_use\ndata: {\"name\":\"vault.read\"}\n\nevent: text_delta\ndata: {\"text\":\"Reply\"}\n\nevent: message_done\ndata: {\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"Reply\"}]}\n\nevent: done\ndata: {}\n\n")
				return
			case "/api/training/expand":
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "event: done\ndata: {\"path\":\"Wiki/Test.md\"}\n\n")
				return
			case "/api/anki/cards":
				fmt.Fprint(w, `{"cards_created":1}`)
				return
			case "/api/news/feeds/f1":
				if body["detach"] != true || body["category"] != nil {
					t.Error("empty category must be null when detaching a feed")
				}
			}
			fmt.Fprint(w, `{}`)
			return
		}
		if _, err := r.Cookie("session"); err != nil {
			t.Error("request without session")
		}
		routes := map[string]any{
			"/api/llm/providers":   []provider{{Name: "test", Models: []string{"model"}}},
			"/api/chats":           []chat{{ID: "c1", Title: "Conversation"}},
			"/api/vault/tree":      []treeEntry{{Path: "Wiki", Type: "folder"}, {Path: "Wiki/Test.md", Type: "file", Depth: 1}},
			"/api/vault/note":      note{Path: "Wiki/Test.md", Content: content},
			"/api/news/feeds":      []feed{{ID: "f1", Title: "Feed", Unread: 1}},
			"/api/news/categories": []string{"Tech"}, "/api/news/labels": []string{"Read later"},
			"/api/news/articles":       []article{{ID: "a1", Title: "News title", FeedTitle: "Feed"}},
			"/api/news/articles/a1":    article{ID: "a1", Title: "News title", FeedTitle: "Feed", Summary: "<p>Article <b>body</b></p>"},
			"/api/training/themes":     object{"themes": []theme{{Theme: "Test", IndexPath: "Wiki/Test.md", Count: 1}}},
			"/api/wiki-reviews/status": object{"reviewed_today_count": 1},
			"/api/wiki-reviews/next":   note{Path: "Wiki/Test.md", Content: content},
			"/api/anki/config":         object{"enabled": true}, "/api/anki/deck-stats": []object{{"name": "Default"}},
		}
		response, ok := routes[r.URL.Path]
		if !ok {
			t.Errorf("unexpected route %s", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "wrong")
	awaitUI(t, u, func() bool { return strings.Contains(u.status.GetText(false), "invalid credentials") })
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return u.authenticated && len(u.browsers[0].items) == 1 })
	updateUI(t, u, func() { u.chat.composer.SetText("Question", false); u.sendChat() })
	awaitUI(t, u, func() bool { return !u.chat.busy && u.chat.id == "c1" && strings.Contains(u.chat.text, "Reply") })
	updateUI(t, u, func() { u.switchPage(3) })
	awaitUI(t, u, func() bool { return len(u.news.articles) == 1 })
	updateUI(t, u, func() { u.openArticle("a1", true) })
	awaitUI(t, u, func() bool { return u.news.article.ID == "a1" && u.news.article.Read })
	updateUI(t, u, func() { u.newsAction("add-label", "Read later") })
	awaitUI(t, u, func() bool { return !u.news.busy })
	updateUI(t, u, u.manageFeeds)
	awaitUI(t, u, func() bool { return u.dialogOpen })
	updateUI(t, u, func() {
		form := frontForm(u)
		if form == nil {
			t.Error("feed dialog missing")
			return
		}
		form.GetFormItem(0).(*tview.DropDown).SetCurrentOption(1)
		form.GetFormItem(1).(*tview.DropDown).SetCurrentOption(1)
		form.GetFormItem(4).(*tview.InputField).SetText("")
		form.GetButton(0).InputHandler()(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone), func(tview.Primitive) {})
	})
	awaitUI(t, u, func() bool { mutex.Lock(); defer mutex.Unlock(); return len(writes["/api/news/feeds/f1"]) == 1 })
	updateUI(t, u, func() { u.switchPage(2) })
	awaitUI(t, u, func() bool { return len(u.browsers[2].items) == 1 })
	updateUI(t, u, func() { u.training.concept.SetText("Test"); u.generateFiche() })
	awaitUI(t, u, func() bool { return !u.training.busy && u.training.parent.GetText() == "Wiki/Test.md" })
	updateUI(t, u, func() { u.switchPage(1) })
	awaitUI(t, u, func() bool { return u.anki.enabled })
	updateUI(t, u, func() { u.anki.front.SetText("Question", false); u.anki.back.SetText("Answer", false); u.createCard() })
	awaitUI(t, u, func() bool { return !u.anki.busy && u.anki.front.GetText() == "" })
	mutex.Lock()
	for _, route := range []string{"/api/chat", "/api/news/articles/a1/read", "/api/news/articles/a1/labels", "/api/news/feeds/f1", "/api/training/expand", "/api/anki/cards"} {
		if len(writes[route]) != 1 {
			t.Errorf("expected one write to %s, got %d", route, len(writes[route]))
		}
	}
	mutex.Unlock()
}
