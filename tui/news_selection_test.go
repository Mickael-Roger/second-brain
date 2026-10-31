package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

func TestNewsMultiSelectionAndBatchActions(t *testing.T) {
	var mu sync.Mutex
	articles := []article{
		{ID: "a1", Title: "Needle one", FeedID: "f1", Read: true, Starred: true},
		{ID: "a2", Title: "Needle two", FeedID: "f1"},
		{ID: "a3", Title: "Another article", FeedID: "f2"},
	}
	failSecond := true
	var writes []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		var response any = object{}
		switch r.URL.Path {
		case "/api/auth/login":
		case "/api/llm/providers":
			response = []provider{}
		case "/api/chats":
			response = []chat{}
		case "/api/news/feeds":
			response = []feed{{ID: "f1", Title: "Feed one", Group: "Tech"}, {ID: "f2", Title: "Feed two", Group: "Other"}}
		case "/api/news/articles":
			response = articles
		default:
			if r.Method != "POST" {
				t.Errorf("selection or batch action unexpectedly opened content: %s %s", r.Method, r.URL.Path)
				http.NotFound(w, r)
				return
			}
			writes = append(writes, r.URL.Path)
			if failSecond && r.URL.Path == "/api/news/articles/a2/read" {
				w.WriteHeader(500)
				_, _ = w.Write([]byte(`{"detail":"batch update failed"}`))
				return
			}
			found := false
			for index := range articles {
				a := &articles[index]
				if !strings.HasPrefix(r.URL.Path, "/api/news/articles/"+a.ID+"/") {
					continue
				}
				found = true
				switch {
				case strings.HasSuffix(r.URL.Path, "/read"):
					a.Read = true
				case strings.HasSuffix(r.URL.Path, "/star"):
					a.Starred = true
				case strings.HasSuffix(r.URL.Path, "/labels"):
					var body struct{ Label string }
					if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Label != "shared-label" {
						t.Errorf("wrong batch label: %+v, %v", body, err)
					}
					a.Labels = append(a.Labels, body.Label)
				default:
					t.Errorf("unexpected mutation: %s", r.URL.Path)
				}
			}
			if !found {
				t.Errorf("unknown mutation target: %s", r.URL.Path)
			}
		}
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()
	u := startUI(t, server.URL)
	signIn(t, u, "password")
	awaitUI(t, u, func() bool { return u.authenticated })
	key := func(r rune) { u.app.QueueEvent(tcell.NewEventKey(tcell.KeyRune, r, tcell.ModNone)) }
	key('3')
	awaitUI(t, u, func() bool { return len(u.news.articles) == 3 })
	key('u')
	awaitUI(t, u, func() bool { return !u.news.unreadOnly })
	filter := func(value string) {
		updateUI(t, u, func() { u.browsers[3].filter = value; u.browsers[3].render() })
	}
	selectID := func(id string) {
		updateUI(t, u, func() {
			for index, row := range u.browsers[3].rows {
				if row.item.id == id {
					u.browsers[3].list.SetCurrentItem(index)
					return
				}
			}
			t.Errorf("missing row %s", id)
		})
	}
	filter("Needle")
	selectID("a1")
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 1 })
	selectID("a2")
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 2 })
	updateUI(t, u, func() {
		if !strings.Contains(u.help.GetText(true), "2 selected") {
			t.Error("selection count is missing")
		}
		for row := 1; row <= 2; row++ {
			for column := range u.browsers[3].columns {
				cell := u.browsers[3].list.GetCell(row, column)
				_, background, _ := cell.Style.Decompose()
				if background != tcell.GetColor("#3a2858") {
					t.Error("marked row is not highlighted across all columns")
				}
				if strings.Contains(cell.Text, "●") || strings.Contains(cell.Text, "○") {
					t.Error("marked row contains a selection dot")
				}
			}
		}
	})
	mu.Lock()
	articles[0].Title = "Needle one refreshed"
	mu.Unlock()
	key('r')
	awaitUI(t, u, func() bool { return u.news.selected["a1"].Title == "Needle one refreshed" })
	// Spaces in the prompt remain text; hiding a marked article keeps it selected.
	key('/')
	for _, r := range " refreshed" {
		key(r)
	}
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return !u.commandOpen && len(u.browsers[3].rows) == 1 && len(u.news.selected) == 2 })
	key('m')
	awaitUI(t, u, func() bool { return !u.news.busy && strings.Contains(u.status.GetText(false), "1/2 articles updated") })
	updateUI(t, u, func() {
		if len(u.news.selected) != 1 || u.news.selected["a2"].ID == "" || u.news.articles[1].Read || !u.news.articles[0].Read || u.news.articles[2].Read {
			t.Error("partial batch success changed the wrong state or discarded the failed selection")
		}
	})
	mu.Lock()
	failSecond = false
	if strings.Join(writes, ",") != "/api/news/articles/a1/read,/api/news/articles/a2/read" {
		t.Errorf("batch did not apply the same state to the selected IDs: %v", writes)
	}
	mu.Unlock()
	key('m')
	awaitUI(t, u, func() bool { return !u.news.busy && len(u.news.selected) == 0 && u.news.articles[1].Read })

	// A collapsed category can select/deselect its descendants as a group.
	filter("")
	selectID("category:Tech")
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 2 })
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 0 })
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 2 })
	key('s')
	awaitUI(t, u, func() bool {
		return !u.news.busy && len(u.news.selected) == 0 && u.news.articles[0].Starred && u.news.articles[1].Starred
	})
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 2 })
	key('a')
	awaitUI(t, u, func() bool { return u.dialogOpen })
	updateUI(t, u, func() {
		var findForm func(tview.Primitive) *tview.Form
		findForm = func(p tview.Primitive) *tview.Form {
			if form, ok := p.(*tview.Form); ok {
				return form
			}
			if flex, ok := p.(*tview.Flex); ok {
				for index := 0; index < flex.GetItemCount(); index++ {
					if form := findForm(flex.GetItem(index)); form != nil {
						return form
					}
				}
			}
			return nil
		}
		_, dialog := u.pages.GetFrontPage()
		form := findForm(dialog)
		if form == nil {
			t.Error("article actions form is missing")
			return
		}
		form.GetFormItem(0).(*tview.DropDown).SetCurrentOption(3)
		form.GetFormItem(1).(*tview.InputField).SetText("shared-label")
		form.GetButton(0).InputHandler()(tcell.NewEventKey(tcell.KeyEnter, 0, tcell.ModNone), func(tview.Primitive) {})
	})
	awaitUI(t, u, func() bool { return !u.news.busy && len(u.news.selected) == 0 })
	mu.Lock()
	if len(articles[0].Labels) != 1 || len(articles[1].Labels) != 1 || len(articles[2].Labels) != 0 || articles[2].Starred {
		t.Error("shared action did not target exactly the selected articles")
	}
	mu.Unlock()
	key(' ')
	awaitUI(t, u, func() bool { return len(u.news.selected) == 2 })
	u.app.QueueEvent(tcell.NewEventKey(tcell.KeyEscape, 0, tcell.ModNone))
	awaitUI(t, u, func() bool { return len(u.news.selected) == 0 })
}
