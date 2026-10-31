package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

func TestAnkiBrowseAllCardsAndDiscardStaleDueResponse(t *testing.T) {
	releaseDue := make(chan struct{})
	var release sync.Once
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
		case "/api/anki/deck-stats":
			result = []object{
				{"name": "Test", "review_count": 1, "total_in_deck": 1001},
				{"name": "New", "new_count": 2, "total_in_deck": 2},
				{"name": "Done", "total_in_deck": 10},
			}
		case "/api/anki/due":
			<-releaseDue
			result = []ankiCard{{ID: 42, Front: "Due card"}}
		case "/api/anki/cards":
			if r.Method != "GET" || r.URL.Query().Get("deck") != "Test" || r.URL.Query().Has("include_new") {
				t.Error("browsing must request all cards for the selected deck")
			}
			switch r.URL.Query().Get("offset") {
			case "0":
				cards := make([]ankiCard, 1000)
				for i := range cards {
					cards[i] = ankiCard{ID: int64(i + 1), Front: fmt.Sprintf("Card %d", i+1)}
				}
				result = cards
			case "1000":
				result = []ankiCard{{ID: 1001, Front: "Future card", Back: "Future answer", State: "review", Interval: 30}}
			default:
				t.Error("unexpected card page")
			}
		default:
			t.Errorf("browsing must not mutate cards: %s", r.URL.Path)
			w.WriteHeader(404)
		}
		_ = json.NewEncoder(w).Encode(result)
	}))
	defer server.Close()
	defer release.Do(func() { close(releaseDue) })
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return u.authenticated })
	updateUI(t, u, func() { u.switchPage(1) })
	awaitUI(t, u, func() bool { return len(u.anki.decks) == 3 })
	updateUI(t, u, func() {
		b := u.browsers[1]
		for i, style := range []string{"yellow", "aqua", "gray"} {
			if !strings.Contains(b.list.GetCell(i+1, 0).Text, "["+style+"]") {
				t.Errorf("deck %d must reflect its review status", i)
			}
		}
		if b.items[0].columns[4] != "Due" || b.items[2].columns[4] != "Up to date" {
			t.Error("deck status must also be readable without colors")
		}
		b.activate(0)
	})
	awaitUI(t, u, func() bool { return u.anki.loading["Test"] })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, 'u', tcell.ModNone))
	awaitUI(t, u, func() bool { return u.anki.showAll })
	release.Do(func() { close(releaseDue) })
	awaitUI(t, u, func() bool { return !u.anki.loading["Test"] && len(u.anki.cards["Test"]) == 1001 })
	updateUI(t, u, func() {
		b := u.browsers[1]
		if b.mode != "all cards" || !b.expanded["anki-deck:Test"] || !strings.Contains(u.headerActions.GetText(true), "Show due only") {
			t.Error("all-card mode must be visible and preserve expanded decks")
		}
		b.activate(1001)
	})
	awaitUI(t, u, func() bool { return u.anki.reviewOpen })
	updateUI(t, u, func() {
		u.showAnkiAnswer()
		if !strings.Contains(u.anki.doc.text.GetText(true), "Future answer") || u.anki.buttons.GetItemCount() != 1 {
			t.Error("future cards must show both sides without rating controls")
		}
		u.answerAnki(3)
		if u.anki.busy {
			t.Error("browsing must not schedule a review")
		}
		u.closeAnkiReview()
		u.browsers[1].filter = "card"
		u.executeCommand("cards")
	})
	awaitUI(t, u, func() bool { return !u.anki.showAll && !u.anki.loading["Test"] && len(u.anki.cards["Test"]) == 1 })
	updateUI(t, u, func() {
		if u.anki.cards["Test"][0].ID != 42 || u.browsers[1].mode != "due" || !u.browsers[1].expanded["anki-deck:Test"] || u.browsers[1].filter != "card" {
			t.Error("switching back must reload due cards without losing expansion or search")
		}
	})
}

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
				result = []ankiCard{{ID: 42, Front: "<b>Question</b>", Back: "Answer", Question: "Reverse question", Answer: "Reverse answer", State: "review", NextReviews: []string{"5h", "18d", "3.1mo", "9.2mo"}}}
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
	awaitUI(t, u, func() bool { return len(u.anki.cards["Test"]) == 1 && len(u.browsers[1].rows) == 2 })
	updateUI(t, u, func() {
		if u.browsers[1].rows[0].item.title != "Test" || u.browsers[1].rows[1].depth != 1 || u.browsers[1].detail {
			t.Error("cards must appear below their deck in the same list")
		}
		if u.browsers[1].list.GetCell(1, 4).Text != "[gray]10[-:-:-]" {
			t.Errorf("deck total must retain API statistics: %s", u.browsers[1].list.GetCell(1, 4).Text)
		}
	})
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return len(u.browsers[1].rows) == 1 })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return len(u.browsers[1].rows) == 2 && !u.anki.loading["Test"] })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyDown, 0, tcell.ModNone))
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return u.dialogOpen && u.anki.reviewOpen && !u.browsers[1].detail })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEscape, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return !u.dialogOpen && !u.anki.reviewOpen && len(u.browsers[1].rows) == 2 })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return u.anki.reviewOpen })
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
		for index, expected := range []string{"5h", "18d", "3.1mo", "9.2mo"} {
			if got := u.anki.intervals.GetItem(index).(*tview.TextView).GetText(false); got != expected {
				t.Errorf("rating %d next review: got %q, want %q", index+1, got, expected)
			}
			buttonX, buttonY, buttonWidth, _ := u.anki.buttons.GetItem(index).GetRect()
			intervalX, intervalY, intervalWidth, _ := u.anki.intervals.GetItem(index).GetRect()
			if intervalX != buttonX || intervalY != buttonY+1 || intervalWidth != buttonWidth {
				t.Error("next review intervals must line up directly below the rating buttons")
			}
		}
	})
	updateUI(t, u, func() { u.answerAnki(3) })
	awaitUI(t, u, func() bool { return !u.anki.busy && strings.Contains(u.status.GetText(false), "review failed") })
	updateUI(t, u, func() {
		if !u.dialogOpen || !u.anki.reviewOpen || u.browsers[1].detail || !u.anki.answerShown {
			t.Error("failed review lost the current card")
		}
	})
	mu.Lock()
	fail = false
	mu.Unlock()
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyTab, 0, tcell.ModNone))
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyTab, 0, tcell.ModNone))
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool {
		return !u.anki.busy && !u.dialogOpen && !u.browsers[1].detail && len(u.anki.cards["Test"]) == 0
	})
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

func TestAnkiMissingReviewIntervals(t *testing.T) {
	c, err := newClient("http://example.test")
	if err != nil {
		t.Fatal(err)
	}
	u := newUI(c, "test", "off")
	defer u.cancel()
	u.closeDialog()
	u.active = 1
	for _, intervals := range [][]string{nil, {"1m", "1d", "4d"}} {
		u.openAnkiCard(ankiCard{ID: 1, Front: "Question", Back: "Answer", NextReviews: intervals})
		u.showAnkiAnswer()
		for index := 0; index < 4; index++ {
			if got := u.anki.intervals.GetItem(index).(*tview.TextView).GetText(false); got != "—" {
				t.Errorf("missing/ambiguous intervals must not be assigned to a rating: %q", got)
			}
		}
		u.closeAnkiReview()
	}
}

func TestAnkiIndependentDeckExpansionAndReviewPopup(t *testing.T) {
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
		case "/api/anki/deck-stats":
			result = []object{{"name": "First", "total_in_deck": 10}, {"name": "Second", "total_in_deck": 20}}
		case "/api/anki/due":
			deck := r.URL.Query().Get("deck")
			id := int64(1)
			if deck == "Second" {
				id = 2
			}
			result = []ankiCard{{ID: id, Front: deck + " question", Back: deck + " answer", State: "review"}}
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
	awaitUI(t, u, func() bool { return len(u.browsers[1].rows) == 2 })
	updateUI(t, u, func() { u.browsers[1].activate(0) })
	awaitUI(t, u, func() bool { return len(u.browsers[1].rows) == 3 && len(u.anki.cards["First"]) == 1 })
	updateUI(t, u, func() { u.browsers[1].activate(2) })
	awaitUI(t, u, func() bool { return len(u.browsers[1].rows) == 4 && len(u.anki.cards["Second"]) == 1 })
	updateUI(t, u, func() {
		b := u.browsers[1]
		if b.rows[1].item.title != "First question" || b.rows[3].item.title != "Second question" || b.rows[1].depth != 1 || b.rows[3].depth != 1 {
			t.Error("cards must stay beneath the correct deck when multiple decks are expanded")
		}
		b.list.SetCurrentItem(3)
		b.activate(3)
		if !u.dialogOpen || !u.anki.reviewOpen || b.detail || len(b.rows) != 4 || u.anki.currentDeck != "Second" {
			t.Error("review must overlay the unchanged deck hierarchy")
		}
	})
	updateUI(t, u, func() {
		b := u.browsers[1]
		_, y, _, _ := b.list.GetInnerRect()
		u.pages.MouseHandler()(tview.MouseLeftClick, tcell.NewEventMouse(1, y+1, tcell.Button1, tcell.ModNone), func(p tview.Primitive) { u.app.SetFocus(p) })
		if len(b.rows) != 4 || !u.anki.reviewOpen || b.list.HasFocus() {
			t.Error("review popup must block clicks on the deck list behind it")
		}
	})
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEscape, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return !u.dialogOpen })
	updateUI(t, u, func() {
		b := u.browsers[1]
		if b.list.GetCurrentItem() != 3 || !b.list.HasFocus() || !b.expanded["anki-deck:First"] || !b.expanded["anki-deck:Second"] {
			t.Error("closing review must restore list focus, selection and expansion")
		}
		u.newAnkiNote()
		_, deck := u.anki.deck.GetCurrentOption()
		if deck != "Second" {
			t.Errorf("creation should use the selected card's deck, got %s", deck)
		}
		u.closeDialog()
		b.activate(0)
		if len(b.rows) != 3 || b.rows[0].item.title != "First" || b.rows[2].item.title != "Second question" {
			t.Error("collapsing one deck must preserve other expanded decks")
		}
	})
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
		case "/api/anki/cards":
			w.WriteHeader(http.StatusMethodNotAllowed)
			result = object{"detail": "Method Not Allowed"}
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
	updateUI(t, u, u.toggleAnkiCards)
	awaitUI(t, u, func() bool {
		return u.anki.showAll && !u.anki.loading["Default"] && strings.Contains(u.anki.loadErrors["Default"], "updated backend")
	})
}

func TestAnkiSyncRefreshesExpandedDeckAndCanRetry(t *testing.T) {
	testAnkiSyncRetry(t, 502, "sync: auth not configured")
}

func TestAnkiFullSyncMessageCanRetryAfterDesktopResolution(t *testing.T) {
	testAnkiSyncRetry(t, 409, "Anki requires a full sync. Open Anki desktop and choose Upload or Download.")
}

func testAnkiSyncRetry(t *testing.T, failureStatus int, failureMessage string) {
	t.Helper()
	var mu sync.Mutex
	synced, fail := false, true
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
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
		case "/api/anki/deck-stats":
			review, total := 309, 554
			if synced {
				review, total = 2, 600
			}
			result = []object{{"name": "English", "review_count": review, "learn_count": 1, "total_in_deck": total}}
		case "/api/anki/due":
			result = []ankiCard{{ID: 1, Front: "Before sync"}}
			if synced {
				result = []ankiCard{{ID: 2, Front: "After sync"}, {ID: 3, Front: "Another card"}}
			}
		case "/api/anki/sync":
			if r.Method != "POST" {
				t.Error("sync must be an explicit POST")
			}
			calls++
			if fail {
				w.WriteHeader(failureStatus)
				result = object{"detail": failureMessage}
			} else {
				synced = true
				result = object{"synced": true}
			}
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
	awaitUI(t, u, func() bool { return len(u.anki.decks) == 1 })
	updateUI(t, u, func() { u.browsers[1].activate(0) })
	awaitUI(t, u, func() bool { return len(u.anki.cards["English"]) == 1 })
	mu.Lock()
	if calls != 0 {
		t.Error("loading decks must not sync automatically")
	}
	mu.Unlock()
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, 's', tcell.ModNone))
	awaitUI(t, u, func() bool {
		return !u.anki.busy && !u.anki.syncing && strings.Contains(u.status.GetText(false), failureMessage)
	})
	updateUI(t, u, func() {
		if u.anki.decks[0].Total != 554 || !u.browsers[1].expanded["anki-deck:English"] {
			t.Error("sync failure must preserve the current deck list and expansion")
		}
	})
	mu.Lock()
	fail = false
	mu.Unlock()
	updateUI(t, u, func() { u.executeCommand("sync"); u.syncAnki() })
	awaitUI(t, u, func() bool {
		return !u.anki.busy && !u.anki.syncing && u.anki.decks[0].Total == 600 && len(u.anki.cards["English"]) == 2
	})
	updateUI(t, u, func() {
		if u.anki.decks[0].Review != 2 || u.anki.decks[0].Learning != 1 || !u.browsers[1].expanded["anki-deck:English"] {
			t.Error("sync must reload counts and cards without collapsing the deck")
		}
	})
	mu.Lock()
	defer mu.Unlock()
	if calls != 2 {
		t.Errorf("expected one failed sync and one retry, got %d", calls)
	}
}
