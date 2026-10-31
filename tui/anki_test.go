package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	"github.com/gdamore/tcell/v2"
)

func TestAnkiDeckReviewAndCreation(t *testing.T) {
	var mu sync.Mutex
	answered, fail, created := false, true, false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		var result any
		switch r.URL.Path {
		case "/api/auth/login":
			result = object{}
		case "/api/llm/providers":
			result = []provider{}
		case "/api/chats":
			result = []chat{}
		case "/api/anki/config":
			result = object{"enabled": true}
		case "/api/anki/deck-stats":
			result = []object{{"name": "Test", "review_count": 1, "new_count": 2, "learn_count": 3, "total_in_deck": 10}}
		case "/api/anki/due":
			if r.URL.Query().Get("deck") != "Test" {
				t.Error("wrong deck query")
			}
			result = []ankiCard{}
			if !answered {
				result = []ankiCard{{ID: 42, Front: "<b>Question</b>", Back: "Answer", Question: "Reverse question", Answer: "Reverse answer", State: "review"}}
			}
		case "/api/anki/cards/42/answer":
			if fail {
				w.WriteHeader(502)
				result = object{"detail": "review failed"}
			} else {
				var payload object
				_ = json.NewDecoder(r.Body).Decode(&payload)
				if payload["ease"] != float64(3) {
					t.Error("wrong review ease")
				}
				answered = true
				result = object{"answered": true}
			}
		case "/api/anki/cards":
			created = true
			result = object{"cards_created": 1}
		default:
			t.Errorf("unexpected route: %s", r.URL.Path)
			w.WriteHeader(404)
		}
		_ = json.NewEncoder(w).Encode(result)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return u.authenticated })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, '1', tcell.ModNone))
	awaitUI(t, u, func() bool { return u.anki.enabled && len(u.browsers[1].rows) == 1 })
	updateUI(t, u, func() {
		if u.browsers[1].rows[0].item.columns[0] != "1" {
			t.Error("missing review count")
		}
	})
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return len(u.anki.cards) == 1 })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return u.browsers[1].detail })
	updateUI(t, u, func() {
		text := u.anki.doc.text.GetText(true)
		if !strings.Contains(text, "Reverse question") || strings.Contains(text, "Reverse answer") {
			t.Error("review must show the card's rendered question without the answer")
		}
	})
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return u.anki.answerShown })
	updateUI(t, u, func() {
		if text := u.anki.doc.text.GetText(true); !strings.Contains(text, "Reverse answer") || strings.Contains(text, "Reverse question") {
			t.Error("rendered answer should be shown as Anki rendered it, without duplicating the question")
		}
	})
	updateUI(t, u, func() { u.answerAnki(3) })
	awaitUI(t, u, func() bool { return !u.anki.busy && strings.Contains(u.status.GetText(false), "review failed") })
	updateUI(t, u, func() {
		if !u.browsers[1].detail || !u.anki.answerShown {
			t.Error("failed review lost the current card")
		}
	})
	mu.Lock()
	fail = false
	mu.Unlock()
	updateUI(t, u, func() { u.answerAnki(3) })
	awaitUI(t, u, func() bool { return !u.anki.busy && !u.browsers[1].detail && len(u.anki.cards) == 0 })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEscape, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return u.anki.currentDeck == "" && len(u.browsers[1].rows) == 1 })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, 'c', tcell.ModNone))
	awaitUI(t, u, func() bool { return u.dialogOpen })
	updateUI(t, u, func() {
		u.anki.front.SetText("New question", false)
		u.anki.back.SetText("New answer", false)
		u.createCard()
	})
	awaitUI(t, u, func() bool { return !u.dialogOpen && !u.anki.busy && u.anki.front.GetText() == "" })
	mu.Lock()
	defer mu.Unlock()
	if !created || !answered {
		t.Error("missing Anki mutations")
	}
}

func TestAnkiMediaUsesServerProxy(t *testing.T) {
	content := ankiMarkdown(`<p>Question</p><img src="image%20one.png"><img src="https://example.test/cover.png">`)
	if !strings.Contains(content, "/api/anki/media?filename=") || !strings.Contains(content, "https://example.test/cover.png") {
		t.Errorf("Anki images must use authenticated server media routes: %s", content)
	}
	_, _, images := renderMarkdown(content, "", "")
	if len(images) != 2 {
		t.Errorf("media must not be resolved as vault attachments: %v", images)
		return
	}
	source, err := url.Parse(images[0])
	if err != nil || source.Path != "api/anki/media" || source.Query().Get("filename") != "image one.png" {
		t.Errorf("incorrect image proxy URL: %v", images[0])
	}
}

func TestAnkiDecksOnOlderBackend(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		var result any = object{}
		switch r.URL.Path {
		case "/api/auth/login":
		case "/api/llm/providers":
			result = []provider{}
		case "/api/chats":
			result = []chat{}
		case "/api/anki/config":
			result = object{"enabled": true}
		case "/api/anki/decks":
			result = []object{{"id": 1, "name": "Default"}}
		case "/api/anki/deck-stats", "/api/anki/due":
			w.WriteHeader(http.StatusNotFound)
			result = object{"detail": "Not Found"}
		default:
			t.Errorf("unexpected route: %s", r.URL.Path)
		}
		_ = json.NewEncoder(w).Encode(result)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return u.authenticated })
	updateUI(t, u, func() { u.switchPage(1) })
	awaitUI(t, u, func() bool { return len(u.browsers[1].rows) == 1 })
	updateUI(t, u, func() {
		row := u.browsers[1].rows[0].item
		if row.title != "Default" || row.columns[0] != "—" || u.anki.statsAvailable {
			t.Error("legacy decks should be shown with unavailable counts")
		}
		if !strings.Contains(u.status.GetText(false), "Update and restart") {
			t.Error("missing backend upgrade explanation")
		}
		u.newAnkiNote()
		if !u.dialogOpen {
			t.Error("note creation must remain available on older servers")
		}
		u.closeDialog()
		u.browsers[1].activate(0)
	})
	awaitUI(t, u, func() bool { return strings.Contains(u.status.GetText(false), "reviews require the updated backend") })
}
