package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"path"
	"strings"

	htmlmd "github.com/JohannesKaufmann/html-to-markdown/v2"
	"github.com/rivo/tview"
	"golang.org/x/net/html"
)

type trainingState struct {
	doc                    *document
	concept, theme, parent *tview.InputField
	web                    *tview.Checkbox
	busy                   bool
}

func (u *ui) buildTraining() page {
	t := &u.training
	t.doc = newDocument(u.openLink)
	t.concept, t.theme, t.parent = input("Concept: "), input("Theme: "), input("Parent note: ")
	t.web = tview.NewCheckbox().SetLabel("Web search ")
	buttons := toolbar(action{"Generate fiche", u.generateFiche}, action{"New learning plan", u.newLearningPlan})
	panel := tview.NewFlex().SetDirection(tview.FlexRow).AddItem(t.doc, 0, 1, true).
		AddItem(t.concept, 1, 0, false).AddItem(t.theme, 1, 0, false).AddItem(t.parent, 1, 0, false).
		AddItem(t.web, 1, 0, false).AddItem(buttons, 1, 0, false)
	return page{"Training", panel,
		[]tview.Primitive{t.doc.text, t.doc.links, t.concept, t.theme, t.parent, t.web, buttons}, t.doc, u.refreshTraining}
}

func (u *ui) newLearningPlan() {
	if u.chat.busy {
		u.say("Stop the current reply first.")
		return
	}
	u.switchPage(0)
	u.newChat()
	u.chat.prompt = "training-kickoff"
	u.app.SetFocus(u.chat.composer)
	u.say("Describe what you want to learn.")
}

func (u *ui) refreshTraining() {
	u.job("Loading training themes", func(ctx context.Context) (func(), error) {
		var result struct{ Themes []theme }
		if err := u.client.request(ctx, "GET", "api/training/themes", nil, &result); err != nil {
			return nil, err
		}
		var entries []treeEntry
		if err := u.client.request(ctx, "GET", "api/vault/tree", nil, &entries); err != nil {
			return nil, err
		}
		return func() {
			groups := []*resource{}
			for _, item := range result.Themes {
				dir := path.Dir(item.IndexPath)
				children := []*resource{{id: item.IndexPath, title: "Index.md", columns: []string{"index"}, open: func() { u.loadTheme(item) }}}
				var notes []treeEntry
				for _, entry := range entries {
					if entry.Path != item.IndexPath && strings.HasPrefix(entry.Path, dir+"/") {
						entry.Path = strings.TrimPrefix(entry.Path, dir+"/")
						notes = append(notes, entry)
					}
				}
				resources := noteResources(notes, func(p string) { u.openTrainingNote(path.Join(dir, p), item.Theme) })
				var qualify func([]*resource)
				qualify = func(items []*resource) {
					for _, node := range items {
						node.id = dir + "/" + node.id
						qualify(node.children)
					}
				}
				qualify(resources)
				children = append(children, resources...)
				groups = append(groups, &resource{id: dir, title: item.Theme, style: "yellow", columns: []string{"theme"}, children: children})
			}
			u.browsers[2].set(groups)
		}, nil
	})
}

func (u *ui) loadTheme(theme theme) {
	u.job("Loading theme", func(ctx context.Context) (func(), error) {
		var result note
		if err := u.client.request(ctx, "GET", query("api/vault/note", url.Values{"path": {theme.IndexPath}}), nil, &result); err != nil {
			return nil, err
		}
		return func() {
			u.training.theme.SetText(theme.Theme)
			u.training.parent.SetText(theme.IndexPath)
			u.showDocument(u.training.doc, theme.Theme, result.Content, result.Path, "", nil)
			u.reveal(2)
		}, nil
	})
}

func (u *ui) openTrainingNote(p, theme string) {
	u.job("Opening training note", func(ctx context.Context) (func(), error) {
		var result note
		if err := u.client.request(ctx, "GET", query("api/vault/note", url.Values{"path": {p}}), nil, &result); err != nil {
			return nil, err
		}
		return func() {
			u.training.theme.SetText(theme)
			u.training.parent.SetText(result.Path)
			u.showDocument(u.training.doc, result.Path, result.Content, result.Path, "", nil)
			u.reveal(2)
		}, nil
	})
}

func (u *ui) generateFiche() {
	t := &u.training
	if t.busy || strings.TrimSpace(t.concept.GetText()) == "" {
		return
	}
	t.busy = true
	payload := object{"target_concept": t.concept.GetText(), "web_search": t.web.IsChecked()}
	if t.theme.GetText() != "" {
		payload["theme"] = t.theme.GetText()
	}
	if t.parent.GetText() != "" {
		payload["parent_path"] = t.parent.GetText()
	}
	u.job("Generating training fiche", func(ctx context.Context) (func(), error) {
		var target string
		err := u.client.stream(ctx, "api/training/expand", payload, func(ev event) error {
			var data struct{ Path, Error string }
			if err := json.Unmarshal(ev.Data, &data); err != nil {
				return err
			}
			if ev.Name == "error" {
				return errors.New(data.Error)
			}
			if ev.Name == "done" {
				target = data.Path
			}
			return nil
		})
		if err == nil && target == "" {
			err = fmt.Errorf("training stream ended without a result")
		}
		return func() {
			t.busy = false
			if err != nil {
				u.fail(err)
				return
			}
			u.openLink("note:"+url.PathEscape(target), "")
		}, nil
	})
}

type ankiState struct {
	deck, mode     *tview.DropDown
	front, back    *tview.TextArea
	tags           *tview.InputField
	form           *tview.Form
	enabled, busy  bool
	doc            *document
	buttons        *tview.Flex
	decks          []ankiDeck
	cards          []ankiCard
	currentDeck    string
	card           ankiCard
	answerShown    bool
	request        int
	statsAvailable bool
}

func (u *ui) buildAnki() page {
	a := &u.anki
	a.deck = tview.NewDropDown().SetLabel("Deck: ")
	a.mode = tview.NewDropDown().SetLabel("Mode: ").SetOptions([]string{"Normal", "Normal + reverse"}, nil).SetCurrentOption(0)
	a.front = tview.NewTextArea().SetLabel("Front: ").SetSize(6, 0)
	a.back = tview.NewTextArea().SetLabel("Back: ").SetSize(6, 0)
	a.tags = input("Tags (spaces): ")
	close := func() {
		if !a.busy {
			u.closeDialog()
		}
	}
	a.form = tview.NewForm().AddFormItem(a.deck).AddFormItem(a.front).AddFormItem(a.back).AddFormItem(a.mode).AddFormItem(a.tags).
		AddButton("Create note", u.createCard).AddButton("Cancel", close)
	a.form.SetCancelFunc(close)
	a.doc = newDocument(u.openLink)
	a.buttons = toolbar(action{"Show answer", u.showAnkiAnswer})
	panel := tview.NewFlex().SetDirection(tview.FlexRow).AddItem(a.doc, 0, 1, true).AddItem(a.buttons, 1, 0, false)
	return page{"Anki", panel, []tview.Primitive{a.doc.text, a.doc.links, a.buttons}, a.doc, u.refreshAnki}
}

func (u *ui) refreshAnki() {
	u.anki.request++
	request := u.anki.request
	deck := u.anki.currentDeck
	u.job("Loading Anki decks", func(ctx context.Context) (func(), error) {
		var config struct{ Enabled bool }
		var decks []ankiDeck
		var cards []ankiCard
		statsAvailable := true
		if err := u.client.request(ctx, "GET", "api/anki/config", nil, &config); err != nil {
			return nil, err
		}
		if config.Enabled {
			if err := u.client.request(ctx, "GET", "api/anki/deck-stats", nil, &decks); err != nil {
				var api *apiError
				if !errors.As(err, &api) || api.Status != 404 {
					return nil, err
				}
				statsAvailable = false
				if err := u.client.request(ctx, "GET", "api/anki/decks", nil, &decks); err != nil {
					return nil, err
				}
			}
			if deck != "" {
				if err := u.client.request(ctx, "GET", query("api/anki/due", url.Values{"deck": {deck}, "include_new": {"true"}}), nil, &cards); err != nil {
					var api *apiError
					if errors.As(err, &api) && api.Status == 404 {
						return nil, fmt.Errorf("Anki reviews require the updated backend. Deploy the latest server version and restart it")
					}
					return nil, err
				}
			}
		}
		options := []string{}
		for _, deck := range decks {
			options = append(options, deck.Name)
		}
		return func() {
			if request != u.anki.request {
				return
			}
			u.anki.enabled = config.Enabled
			u.anki.statsAvailable = statsAvailable
			u.anki.decks, u.anki.cards = decks, cards
			_, selectedDeck := u.anki.deck.GetCurrentOption()
			if deck != "" {
				selectedDeck = deck
			}
			u.anki.deck.SetOptions(options, nil)
			for index, name := range options {
				if name == selectedDeck || selectedDeck == "" && index == 0 {
					u.anki.deck.SetCurrentOption(index)
				}
			}
			u.fillAnki()
			if !config.Enabled {
				u.say("Anki is disabled on this server.")
			} else if !statsAvailable {
				u.say("Decks loaded. Update and restart the backend to enable Anki counts and reviews.")
			}
		}, nil
	})
}

func (u *ui) createCard() {
	a := &u.anki
	if !a.enabled || a.busy {
		return
	}
	_, deck := a.deck.GetCurrentOption()
	index, _ := a.mode.GetCurrentOption()
	if deck == "" || strings.TrimSpace(a.front.GetText()) == "" || strings.TrimSpace(a.back.GetText()) == "" {
		u.say("Choose a deck and fill in both sides.")
		return
	}
	mode := "normal"
	if index == 1 {
		mode = "reverse"
	}
	payload := object{"deck": deck, "front": a.front.GetText(), "back": a.back.GetText(), "mode": mode, "tags": strings.Fields(a.tags.GetText())}
	a.busy = true
	u.job("Creating Anki card", func(ctx context.Context) (func(), error) {
		var result struct {
			Cards int `json:"cards_created"`
		}
		err := u.client.request(ctx, "POST", "api/anki/cards", payload, &result)
		return func() {
			a.busy = false
			if err != nil {
				u.fail(err)
				return
			}
			a.front.SetText("", false)
			a.back.SetText("", false)
			if u.dialogOpen {
				u.closeDialog()
			}
			u.refreshAnki()
			u.say(fmt.Sprintf("Created %d card(s)", result.Cards))
		}, nil
	})
}

func (u *ui) newAnkiNote() {
	if u.anki.busy {
		return
	}
	if !u.anki.enabled {
		u.say("Anki is disabled or decks are still loading.")
		return
	}
	u.dialog(u.anki.form, "New Anki note", 90, 26)
}

func (u *ui) fillAnki() {
	a, b := &u.anki, u.browsers[1]
	items := []*resource{}
	if a.currentDeck == "" {
		b.columns = []string{"DECK", "REVIEW", "NEW", "LEARNING", "TOTAL"}
		b.mode = "decks"
		for _, deck := range a.decks {
			counts := []string{fmt.Sprint(deck.Review), fmt.Sprint(deck.New), fmt.Sprint(deck.Learning), fmt.Sprint(deck.Total)}
			if !a.statsAvailable {
				counts = []string{"—", "—", "—", "—"}
			}
			items = append(items, &resource{id: deck.Name, title: deck.Name,
				columns: counts,
				open:    func() { a.currentDeck = deck.Name; b.filter = ""; u.refreshAnki() }})
		}
	} else {
		b.columns = []string{"CARD — " + a.currentDeck, "STATE", "INTERVAL (DAYS)"}
		b.mode = "due"
		for _, card := range a.cards {
			question := card.Front
			if card.Question != "" {
				question = card.Question
			}
			front, _ := htmlmd.ConvertString(question)
			items = append(items, &resource{id: fmt.Sprint(card.ID), title: strings.Join(strings.Fields(front), " "),
				columns: []string{card.State, fmt.Sprint(card.Interval)}, open: func() { u.openAnkiCard(card) }})
		}
	}
	b.set(items)
}

func (u *ui) backAnki() {
	a, b := &u.anki, u.browsers[1]
	if b.detail {
		u.browse()
	} else if b.filter != "" {
		b.filter = ""
		b.render()
	} else if a.currentDeck != "" {
		a.currentDeck = ""
		u.fillAnki()
		u.refreshAnki()
	}
}

func (u *ui) openAnkiCard(card ankiCard) {
	a := &u.anki
	if a.busy {
		return
	}
	if card.Question != "" {
		card.Front = card.Question
	}
	if card.Answer != "" {
		card.Back = card.Answer
	}
	card.Front = ankiMarkdown(card.Front)
	card.Back = ankiMarkdown(card.Back)
	a.card, a.answerShown = card, false
	u.showDocument(a.doc, "Anki — "+a.currentDeck, card.Front, "", "", nil)
	a.buttons.Clear().AddItem(tview.NewButton("Show answer").SetSelectedFunc(u.showAnkiAnswer), 0, 1, true)
	u.reveal(1)
	u.app.SetFocus(a.buttons.GetItem(0))
}

func (u *ui) showAnkiAnswer() {
	a := &u.anki
	if a.card.ID == 0 || a.busy {
		return
	}
	a.answerShown = true
	content := a.card.Front + "\n\n---\n\n" + a.card.Back
	if a.card.Answer != "" {
		// Anki's rendered answer already includes the question when the template asks for it.
		content = a.card.Back
	}
	u.showDocument(a.doc, "Anki — "+a.currentDeck, content, "", "", nil)
	a.buttons.Clear()
	for index, label := range []string{"Again", "Hard", "Good", "Easy"} {
		ease := index + 1
		a.buttons.AddItem(tview.NewButton(label).SetSelectedFunc(func() { u.answerAnki(ease) }), 0, 1, index == 0)
	}
	u.app.SetFocus(a.buttons.GetItem(0))
}

func ankiMarkdown(content string) string {
	doc, err := html.Parse(strings.NewReader(content))
	if err != nil {
		return content
	}
	var walk func(*html.Node)
	walk = func(node *html.Node) {
		if node.Type == html.ElementNode && node.Data == "img" {
			for index, attr := range node.Attr {
				if attr.Key != "src" || attr.Val == "" {
					continue
				}
				source, err := url.Parse(attr.Val)
				if err == nil && source.Scheme == "" && source.Host == "" && !strings.HasPrefix(attr.Val, "/") {
					filename, err := url.PathUnescape(attr.Val)
					if err == nil {
						node.Attr[index].Val = query("/api/anki/media", url.Values{"filename": {filename}})
					}
				}
			}
		}
		for child := node.FirstChild; child != nil; child = child.NextSibling {
			walk(child)
		}
	}
	walk(doc)
	markdown, err := htmlmd.ConvertNode(doc)
	if err != nil {
		return content
	}
	return string(markdown)
}

func (u *ui) answerAnki(ease int) {
	a := &u.anki
	if a.busy || !a.answerShown || a.card.ID == 0 {
		return
	}
	a.busy = true
	id := a.card.ID
	u.job("Saving Anki review", func(ctx context.Context) (func(), error) {
		err := u.client.request(ctx, "POST", fmt.Sprintf("api/anki/cards/%d/answer", id), object{"ease": ease}, nil)
		return func() {
			a.busy = false
			if err != nil {
				u.fail(err)
				return
			}
			a.card = ankiCard{}
			if u.active == 1 {
				u.browse()
			}
			u.refreshAnki()
		}, nil
	})
}
