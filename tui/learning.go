package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"net/url"
	"path"
	"strings"

	htmlmd "github.com/JohannesKaufmann/html-to-markdown/v2"
	"github.com/gdamore/tcell/v2"
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
	syncing        bool
	showAll        bool
	doc            *document
	buttons        *tview.Flex
	intervals      *tview.Flex
	decks          []ankiDeck
	cards          map[string][]ankiCard
	loading        map[string]bool
	loadErrors     map[string]string
	currentDeck    string
	card           ankiCard
	answerShown    bool
	request        int
	statsAvailable bool
	reviewOpen     bool
	review         *tview.Flex
	reviewBody     *tview.Flex
	reviewMedia    *mediaPane
}

func (u *ui) buildAnki() page {
	a := &u.anki
	a.cards = map[string][]ankiCard{}
	a.loading = map[string]bool{}
	a.loadErrors = map[string]string{}
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
	a.intervals = tview.NewFlex()
	a.reviewBody = tview.NewFlex().AddItem(a.doc, 0, 1, true)
	a.reviewMedia = newMedia("halfcell")
	a.reviewMedia.load = u.loadAnkiImage
	a.review = tview.NewFlex().SetDirection(tview.FlexRow).AddItem(a.reviewBody, 0, 1, true).
		AddItem(a.buttons, 1, 0, false).AddItem(a.intervals, 1, 0, false)
	a.review.SetBorder(true).SetTitle("Anki review")
	a.review.SetInputCapture(func(event *tcell.EventKey) *tcell.EventKey {
		switch event.Key() {
		case tcell.KeyEscape:
			u.closeAnkiReview()
			return nil
		case tcell.KeyTab, tcell.KeyBacktab:
			focus := []tview.Primitive{a.doc.text}
			if a.doc.links.GetItemCount() > 0 {
				focus = append(focus, a.doc.links)
			}
			for index := 0; index < a.buttons.GetItemCount(); index++ {
				focus = append(focus, a.buttons.GetItem(index))
			}
			if len(a.reviewMedia.sources) > 0 {
				for index := 0; index < a.reviewMedia.buttons.GetItemCount(); index++ {
					focus = append(focus, a.reviewMedia.buttons.GetItem(index))
				}
			}
			current, step := 0, 1
			for index, item := range focus {
				if item.HasFocus() {
					current = index
					break
				}
			}
			if event.Key() == tcell.KeyBacktab {
				step = -1
			}
			u.app.SetFocus(focus[(current+step+len(focus))%len(focus)])
			return nil
		}
		return event
	})
	return page{"Anki", a.review, []tview.Primitive{a.doc.text, a.doc.links, a.buttons}, a.doc, u.refreshAnki}
}

func (u *ui) refreshAnki() {
	if u.anki.syncing {
		return
	}
	u.anki.request++
	request := u.anki.request
	u.job("Loading Anki decks", func(ctx context.Context) (func(), error) {
		var config struct{ Enabled bool }
		var decks []ankiDeck
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
			u.anki.decks = decks
			_, selectedDeck := u.anki.deck.GetCurrentOption()
			u.anki.deck.SetOptions(options, nil)
			for index, name := range options {
				if name == selectedDeck || selectedDeck == "" && index == 0 {
					u.anki.deck.SetCurrentOption(index)
				}
			}
			u.fillAnki()
			if config.Enabled {
				for _, deck := range decks {
					if u.browsers[1].expanded["anki-deck:"+deck.Name] {
						u.loadAnkiDeck(deck.Name)
					}
				}
			}
			if !config.Enabled {
				u.say("Anki is disabled on this server.")
			} else if !statsAvailable {
				u.say("Decks loaded. Update and restart the backend to enable Anki counts and reviews.")
			}
		}, nil
	})
}

func (u *ui) syncAnki() {
	a := &u.anki
	if !u.authenticated || a.busy || u.dialogOpen {
		return
	}
	a.busy, a.syncing = true, true
	a.request++ // Discard deck statistics requested before the sync.
	u.updateChrome()
	u.job("Synchronizing Anki with AnkiWeb", func(ctx context.Context) (func(), error) {
		err := u.client.request(ctx, "POST", "api/anki/sync", nil, nil)
		return func() {
			a.busy, a.syncing = false, false
			u.updateChrome()
			if err != nil {
				u.fail(err)
				return
			}
			u.refreshAnki()
			u.say("AnkiWeb sync complete. Refreshing decks and cards…")
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
	b := u.browsers[1]
	index := b.list.GetCurrentItem()
	for index >= 0 && index < len(b.rows) && b.rows[index].depth > 0 {
		index--
	}
	if index >= 0 && index < len(b.rows) {
		for deckIndex, deck := range u.anki.decks {
			if b.rows[index].item.id == "anki-deck:"+deck.Name {
				u.anki.deck.SetCurrentOption(deckIndex)
				break
			}
		}
	}
	u.dialog(u.anki.form, "New Anki note", 90, 26)
}

func (u *ui) loadAnkiDeck(deck string) {
	a := &u.anki
	if a.loading[deck] || a.syncing {
		return
	}
	request := a.request
	endpoint := "api/anki/due"
	params := url.Values{"deck": {deck}, "include_new": {"true"}, "limit": {"1000"}}
	if a.showAll {
		endpoint = "api/anki/cards"
		params.Del("include_new")
	}
	a.loading[deck] = true
	delete(a.loadErrors, deck)
	u.fillAnki()
	u.job("Loading cards — "+deck, func(ctx context.Context) (func(), error) {
		var cards []ankiCard
		var err error
		for offset := 0; ; offset += 1000 {
			params.Set("offset", fmt.Sprint(offset))
			var batch []ankiCard
			err = u.client.request(ctx, "GET", query(endpoint, params), nil, &batch)
			if err != nil {
				break
			}
			cards = append(cards, batch...)
			if len(batch) < 1000 {
				break
			}
		}
		return func() {
			a.loading[deck] = false
			if request != a.request {
				if !a.syncing && u.browsers[1].expanded["anki-deck:"+deck] {
					u.loadAnkiDeck(deck)
				}
				return
			}
			if err != nil {
				var api *apiError
				if errors.As(err, &api) && (api.Status == 404 || api.Status == 405) {
					err = fmt.Errorf("Anki cards and reviews require the updated backend. Deploy the latest server version and restart it")
				}
				a.loadErrors[deck] = err.Error()
				u.fillAnki()
				u.fail(err)
				return
			}
			a.cards[deck] = cards
			u.fillAnki()
		}, nil
	})
}

func (u *ui) toggleAnkiCards() {
	a := &u.anki
	if !a.enabled || a.busy || u.dialogOpen {
		return
	}
	a.showAll = !a.showAll
	a.request++
	a.cards = map[string][]ankiCard{}
	a.loadErrors = map[string]string{}
	u.fillAnki()
	for _, deck := range a.decks {
		if u.browsers[1].expanded["anki-deck:"+deck.Name] {
			u.loadAnkiDeck(deck.Name)
		}
	}
}

func (u *ui) fillAnki() {
	a, b := &u.anki, u.browsers[1]
	items := []*resource{}
	b.columns = []string{"DECK / CARD", "REVIEW", "NEW", "LEARNING", "TOTAL", "STATE", "INTERVAL"}
	b.mode = "due"
	if a.showAll {
		b.mode = "all cards"
	}
	for _, deck := range a.decks {
		counts := []string{fmt.Sprint(deck.Review), fmt.Sprint(deck.New), fmt.Sprint(deck.Learning), fmt.Sprint(deck.Total)}
		style, state := "gray", "Up to date"
		columnStyles := []string{"gray", "gray", "gray", "gray", "gray"}
		if deck.New > 0 {
			style, state = "aqua", "New cards"
			columnStyles[1] = "aqua"
		}
		if deck.Review > 0 || deck.Learning > 0 {
			style, state = "yellow", "Due"
		}
		if deck.Review > 0 {
			columnStyles[0] = "green"
		}
		if deck.Learning > 0 {
			columnStyles[2] = "orange"
		}
		columnStyles[4] = style
		if !a.statsAvailable {
			counts = []string{"—", "—", "—", "—"}
			style, state = "aqua", "Counts unavailable"
		}
		group := &resource{id: "anki-deck:" + deck.Name, title: deck.Name, style: style,
			columns: append(counts, state), columnStyles: columnStyles, children: []*resource{},
			onToggle: func(expanded bool) {
				if expanded {
					a.currentDeck = deck.Name
					u.loadAnkiDeck(deck.Name)
				}
			}}
		for _, card := range a.cards[deck.Name] {
			question := card.Front
			if card.Question != "" {
				question = card.Question
			}
			front, _ := htmlmd.ConvertString(question)
			title := strings.Join(strings.Fields(front), " ")
			if title == "" {
				title = fmt.Sprintf("Card %d", card.ID)
			}
			group.children = append(group.children, &resource{id: group.id + ":" + fmt.Sprint(card.ID), title: title,
				columns: []string{"", "", "", "", card.State, fmt.Sprint(card.Interval)}, open: func() { a.currentDeck = deck.Name; u.openAnkiCard(card) }})
		}
		if message := a.loadErrors[deck.Name]; message != "" {
			group.children = append(group.children, &resource{id: group.id + ":error", title: message + " (Enter to retry)", style: "red", open: func() { u.loadAnkiDeck(deck.Name) }})
		} else if len(group.children) == 0 {
			message := "No due or new cards"
			if a.showAll {
				message = "No cards in this deck"
			}
			if _, loaded := a.cards[deck.Name]; !loaded || a.loading[deck.Name] {
				message = "Loading cards…"
			}
			group.children = append(group.children, &resource{id: group.id + ":status", title: message, style: "gray"})
		}
		items = append(items, group)
	}
	b.set(items)
}

func (u *ui) backAnki() {
	a, b := &u.anki, u.browsers[1]
	if b.filter != "" {
		b.filter = ""
		b.render()
	} else {
		index := b.list.GetCurrentItem()
		for index >= 0 && index < len(b.rows) && b.rows[index].depth > 0 {
			index--
		}
		if index >= 0 && index < len(b.rows) {
			b.expanded[b.rows[index].item.id] = false
			a.currentDeck = ""
			b.list.SetCurrentItem(index)
			b.render()
		}
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
	a.intervals.Clear()
	a.reviewOpen = true
	u.showDocument(a.doc, "Anki — "+a.currentDeck, card.Front, "", "", nil)
	a.buttons.Clear().AddItem(tview.NewButton("Show answer").SetSelectedFunc(u.showAnkiAnswer), 0, 1, true)
	a.buttons.AddItem(tview.NewButton("Close").SetSelectedFunc(u.closeAnkiReview), 12, 0, false)
	title := "Anki review — "
	if a.showAll {
		title = "Anki browse — "
	}
	a.review.SetTitle(title + tview.Escape(a.currentDeck))
	u.updateAnkiReviewMedia()
	_, _, width, height := u.pages.GetRect()
	modal := tview.NewFlex().SetDirection(tview.FlexRow).
		AddItem(centered(a.review, min(110, max(1, width-4)), min(34, max(1, height-4))), 0, 1, true).
		AddItem(u.status, 2, 0, false)
	modal.SetMouseCapture(func(action tview.MouseAction, event *tcell.EventMouse) (tview.MouseAction, *tcell.EventMouse) {
		a.review.MouseHandler()(action, event, func(p tview.Primitive) { u.app.SetFocus(p) })
		return tview.MouseConsumed, nil
	})
	u.dialogOpen = true
	u.pages.AddPage("dialog", modal, true, true)
	u.app.SetFocus(a.buttons.GetItem(0))
}

func (u *ui) closeAnkiReview() {
	if u.anki.busy {
		return
	}
	u.anki.reviewOpen = false
	u.anki.card = ankiCard{}
	u.anki.reviewMedia.setImage(nil)
	u.closeDialog()
}

func (u *ui) updateAnkiReviewMedia() {
	a := &u.anki
	a.reviewBody.RemoveItem(a.reviewMedia)
	a.reviewMedia.setImage(nil)
	a.reviewMedia.sources = nil
	if u.media.mode == "off" || len(a.doc.images) == 0 {
		return
	}
	a.reviewMedia.sources = append([]string(nil), a.doc.images...)
	a.reviewBody.AddItem(a.reviewMedia, 30, 0, false)
	u.loadAnkiImage(0)
}

func (u *ui) loadAnkiImage(index int) {
	a, media := &u.anki, u.anki.reviewMedia
	if index < 0 || index >= len(media.sources) {
		return
	}
	media.index = index
	media.setImage(nil)
	generation, source := media.generation, media.sources[index]
	u.job("Loading Anki image", func(ctx context.Context) (func(), error) {
		data, err := u.client.image(ctx, source)
		var img image.Image
		if err == nil {
			img, err = decodeImage(data)
		}
		return func() {
			if !a.reviewOpen || generation != media.generation {
				return
			}
			if err != nil {
				media.caption.SetText("Image unavailable: " + err.Error())
				return
			}
			media.setImage(img)
		}, nil
	})
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
	u.updateAnkiReviewMedia()
	a.buttons.Clear()
	a.intervals.Clear()
	if a.showAll {
		a.buttons.AddItem(tview.NewButton("Close").SetSelectedFunc(u.closeAnkiReview), 0, 1, true)
		u.app.SetFocus(a.buttons.GetItem(0))
		return
	}
	for index, label := range []string{"Again", "Hard", "Good", "Easy"} {
		ease := index + 1
		a.buttons.AddItem(tview.NewButton(label).SetSelectedFunc(func() { u.answerAnki(ease) }), 0, 1, index == 0)
		interval := "—"
		if len(a.card.NextReviews) == 4 && a.card.NextReviews[index] != "" {
			interval = a.card.NextReviews[index]
		}
		a.intervals.AddItem(tview.NewTextView().SetText(interval).SetTextColor(tcell.ColorGray).SetTextAlign(tview.AlignCenter), 0, 1, false)
	}
	a.buttons.AddItem(tview.NewButton("Close").SetSelectedFunc(u.closeAnkiReview), 12, 0, false)
	a.intervals.AddItem(nil, 12, 0, false)
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
	if a.busy || a.showAll || !a.answerShown || a.card.ID == 0 {
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
			u.closeAnkiReview()
			u.refreshAnki()
		}, nil
	})
}
