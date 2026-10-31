package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gdamore/tcell/v2"
)

func TestNewsReadKeysAndSharedPrompt(t *testing.T) {
	logo := image.NewRGBA(image.Rect(0, 0, 16, 16))
	for y := 0; y < 16; y++ {
		for x := 0; x < 16; x++ {
			logo.Set(x, y, color.RGBA{255, 40, 0, 255})
		}
	}
	var logoData bytes.Buffer
	if err := png.Encode(&logoData, logo); err != nil {
		t.Fatal(err)
	}
	logoURI := "data:image/png;base64," + base64.StdEncoding.EncodeToString(logoData.Bytes())
	var mu sync.Mutex
	articles := []article{
		{ID: "a1", Title: "Needle beta", FeedID: "f1"},
		{ID: "a2", Title: "Needle alpha", FeedID: "f1", Read: true,
			PublishedAt: "2026-10-26T08:30:00Z", Summary: "Flattened summary",
			RawHTML: `<h2>Release details</h2><p>First paragraph with <strong>important changes</strong>.</p><p>Second paragraph.</p><ul><li>First feature</li><li>Second feature</li></ul><p><a href="https://example.test/release">Read more</a></p>`},
	}
	failMutation := false
	filters := []string{}
	writes := []string{}
	opens := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		var response any
		switch r.URL.Path {
		case "/api/auth/login":
			response = object{}
		case "/api/llm/providers":
			response = []provider{}
		case "/api/chats":
			response = []chat{}
		case "/api/news/feeds":
			response = []feed{{ID: "f1", Title: "Feed", Group: "Tech", Favicon: logoURI}}
		case "/api/news/articles":
			filter := r.URL.Query().Get("unread_only")
			filters = append(filters, filter)
			items := []article{}
			for _, a := range articles {
				if filter != "true" || !a.Read {
					items = append(items, a)
				}
			}
			response = items
		case "/api/news/articles/a2":
			opens++
			response = articles[1]
		case "/api/news/articles/a2/star", "/api/news/articles/a2/unstar":
			articles[1].Starred = strings.HasSuffix(r.URL.Path, "/star")
			response = object{}
		case "/api/news/articles/a1/read", "/api/news/articles/a1/unread", "/api/news/articles/a2/read", "/api/news/articles/a2/unread":
			if r.Method != "POST" {
				t.Errorf("unexpected method: %s", r.Method)
			}
			writes = append(writes, r.URL.Path)
			if failMutation {
				w.WriteHeader(500)
				_, _ = w.Write([]byte(`{"detail":"read update failed"}`))
				return
			}
			for i := range articles {
				if strings.Contains(r.URL.Path, "/"+articles[i].ID+"/") {
					articles[i].Read = strings.HasSuffix(r.URL.Path, "/read")
				}
			}
			response = object{}
		default:
			t.Errorf("unexpected route (marking must not open an article): %s", r.URL.Path)
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return u.authenticated })
	typeText := func(text string) {
		for _, r := range text {
			u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, r, tcell.ModNone))
		}
	}
	key := func(k tcell.Key) { u.app.QueueEvent(tcell.NewEventKey(k, 0, tcell.ModNone)) }
	typeText(":ne")
	key(tcell.KeyTab)
	awaitUI(t, u, func() bool { return u.command.GetText() == "news" })
	typeText(" /Needle")
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool {
		return u.active == 3 && u.news.unreadOnly && len(u.news.articles) == 1 && len(u.browsers[3].rows) == 1 && !u.commandOpen
	})
	typeText("u")
	awaitUI(t, u, func() bool { return u.active == 3 && len(u.browsers[3].rows) == 2 && !u.commandOpen })
	updateUI(t, u, func() {
		if u.news.logos[logoURI] == nil || u.browsers[3].items[0].children[0].icon == nil || u.browsers[3].rows[0].item.columnIcons[0] == nil {
			t.Error("API favicon was not decoded and attached to feed names")
		}
		if u.browsers[3].filter != "Needle" || u.browsers[3].detail {
			t.Error("combined resource/filter command was not applied")
		}
		if strings.Contains(u.header.GetText(false), ":news") {
			t.Error("sections must not be a static navigation row")
		}
		_, y, _, _ := u.command.GetRect()
		if y != 7 {
			t.Errorf("shared prompt is not below the header: y=%d", y)
		}
		for _, row := range u.browsers[3].rows {
			if strings.Contains(row.item.meta, "unread") {
				t.Error("read state must use color, not title metadata")
			}
		}
	})
	// Cancel restores the previous accepted filter.
	typeText("/missing")
	awaitUI(t, u, func() bool { return u.command.GetText() == "Needlemissing" && len(u.browsers[3].rows) == 0 })
	key(tcell.KeyEscape)
	awaitUI(t, u, func() bool { return !u.commandOpen && u.browsers[3].filter == "Needle" && len(u.browsers[3].rows) == 2 })
	typeText("u")
	awaitUI(t, u, func() bool {
		return u.news.unreadOnly && len(u.news.articles) == 1 && len(u.browsers[3].rows) == 1
	})
	typeText("u")
	awaitUI(t, u, func() bool {
		return !u.news.unreadOnly && len(u.news.articles) == 2 && len(u.browsers[3].rows) == 2
	})
	selectArticle := func(id string) {
		updateUI(t, u, func() {
			for i, row := range u.browsers[3].rows {
				if row.item.id == id {
					u.browsers[3].list.SetCurrentItem(i)
					return
				}
			}
			t.Errorf("missing article %s", id)
		})
	}
	selectArticle("a2")
	typeText("m")
	awaitUI(t, u, func() bool { return !u.news.busy && !u.news.articles[1].Read })
	mu.Lock()
	failMutation = true
	mu.Unlock()
	typeText("m")
	awaitUI(t, u, func() bool { return !u.news.busy && strings.Contains(u.status.GetText(false), "read update failed") })
	updateUI(t, u, func() {
		if u.news.articles[1].Read {
			t.Error("failed mutation changed local read state")
		}
	})
	mu.Lock()
	failMutation = false
	mu.Unlock()
	typeText("m")
	awaitUI(t, u, func() bool { return !u.news.busy && u.news.articles[1].Read })
	typeText("u")
	awaitUI(t, u, func() bool { return len(u.news.articles) == 1 && len(u.browsers[3].rows) == 1 })
	typeText("m")
	awaitUI(t, u, func() bool { return !u.news.busy && len(u.browsers[3].rows) == 0 })
	updateUI(t, u, func() {
		if u.browsers[3].detail || u.news.article.ID != "" {
			t.Error("read key opened a document")
		}
	})
	mu.Lock()
	if strings.Join(filters, ",") != "true,false,true,false,true" {
		t.Errorf("unexpected all/unread requests: %v", filters)
	}
	if len(writes) != 4 || writes[0] != "/api/news/articles/a2/unread" || writes[3] != "/api/news/articles/a1/read" {
		t.Errorf("wrong mutation targets: %v", writes)
	}
	if opens != 0 {
		t.Error("marking in the list downloaded the article")
	}
	mu.Unlock()

	// Article state stays visible after the filter and action toolbars are removed.
	typeText(":unread")
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return !u.news.unreadOnly && len(u.news.articles) == 2 })
	selectArticle("a2")
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool {
		return u.browsers[3].detail && strings.Contains(u.news.doc.text.GetTitle(), "☆[-] [gray]READ[-] Needle alpha")
	})
	updateUI(t, u, func() {
		if u.pageDefs[3].root != u.news.doc || len(u.pageDefs[3].focus) != 2 {
			t.Error("article view still has filter or action controls")
		}
		if !strings.Contains(u.headerActions.GetText(true), "<u>  Hide read") || !strings.Contains(u.headerActions.GetText(true), "<s>  Toggle star") {
			t.Error("article shortcuts are missing from the dashboard")
		}
		content := u.news.doc.text.GetText(false)
		for _, fragment := range []string{"[::b]Release details", "[::b]important changes", "Second paragraph.\n\n", "• First feature", "• Second feature", "[deepskyblue]Read more", "2026-10-26"} {
			if !strings.Contains(content, fragment) {
				t.Errorf("HTML formatting missing %q in %q", fragment, content)
			}
		}
		if strings.Contains(content, "Flattened summary") || strings.Contains(content, "08:30") || strings.Contains(content, "<h2>") {
			t.Errorf("article used flattened content, raw tags or a time: %q", content)
		}
		if u.news.doc.links.GetItemCount() != 1 {
			t.Error("HTML link was not retained")
		}
	})
	typeText("m")
	awaitUI(t, u, func() bool {
		return !u.news.busy && !u.news.article.Read && strings.Contains(u.news.doc.text.GetTitle(), "☆[-] [aqua::b]UNREAD[-:-:-] Needle alpha")
	})
	typeText("s")
	awaitUI(t, u, func() bool {
		return u.news.article.Starred && strings.Contains(u.news.doc.text.GetTitle(), "[yellow]★[-] [aqua::b]UNREAD[-:-:-] Needle alpha")
	})
	typeText(":star")
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return !u.news.article.Starred && !u.news.busy })
	typeText(":read")
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool {
		return u.news.article.Read && strings.Contains(u.news.doc.text.GetText(true), "☆ READ Needle alpha")
	})
	typeText(":actions")
	key(tcell.KeyEnter)
	awaitUI(t, u, func() bool { return u.dialogOpen })
}

func TestNewsReadColorsWithFuzzyHighlight(t *testing.T) {
	b := newResourceBrowser("news", nil)
	b.filter = "Ne"
	b.set([]*resource{
		{id: "unread", title: "Needle beta", style: "#6dd7ff::b"},
		{id: "read", title: "Needle alpha", style: "#8393a7::-"},
	})
	screen := tcell.NewSimulationScreen("UTF-8")
	if err := screen.Init(); err != nil {
		t.Fatal(err)
	}
	defer screen.Fini()
	screen.SetSize(80, 20)
	b.list.SetRect(0, 0, 80, 20)
	b.list.SetSelectable(false, false)
	b.list.Draw(screen)
	for index, expected := range []tcell.Color{tcell.GetColor("#6dd7ff"), tcell.GetColor("#8393a7")} {
		// After the two highlighted matching characters, the original read color returns.
		_, _, style, _ := screen.GetContent(5, index+2)
		foreground, _, attrs := style.Decompose()
		if foreground != expected {
			t.Errorf("row %d foreground: %v, expected %v", index, foreground, expected)
		}
		if (attrs&tcell.AttrBold != 0) != (index == 0) {
			t.Errorf("row %d read emphasis is incorrect", index)
		}
	}
}

func TestDoubleCtrlCConfirmationExpires(t *testing.T) {
	u := startUI(t, "http://localhost:1")
	key := func() { u.app.QueueEvent(tcell.NewEventKey(tcell.KeyCtrlC, 0, tcell.ModNone)) }
	key()
	awaitUI(t, u, func() bool {
		return !u.quitDeadline.IsZero() && strings.Contains(u.status.GetText(false), "within 3 seconds")
	})
	updateUI(t, u, func() {
		if u.ctx.Err() != nil {
			t.Error("first Ctrl+C stopped the application")
		}
		u.say("Background update")
		if !strings.Contains(u.status.GetText(false), "within 3 seconds") {
			t.Error("background status hid quit confirmation")
		}
	})
	awaitUI(t, u, func() bool { return u.quitDeadline.IsZero() })
	updateUI(t, u, func() {
		if u.ctx.Err() != nil || u.status.GetText(false) != "Background update" {
			t.Error("quit confirmation did not expire cleanly")
		}
	})
	key()
	awaitUI(t, u, func() bool { return !u.quitDeadline.IsZero() })
	key()
	select {
	case <-u.ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("second Ctrl+C did not stop the application")
	}
}
