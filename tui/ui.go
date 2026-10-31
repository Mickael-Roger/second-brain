package main

import (
	"context"
	"errors"
	"fmt"
	"image"
	"net/url"
	"os"
	"os/exec"
	"path"
	"runtime"
	"strings"
	"time"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

type action struct {
	label string
	fn    func()
}

func toolbar(actions ...action) *tview.Flex {
	bar := tview.NewFlex()
	for index, a := range actions {
		bar.AddItem(tview.NewButton(a.label).SetSelectedFunc(a.fn), len([]rune(a.label))+4, 0, index == 0)
	}
	return bar
}

func input(label string) *tview.InputField {
	return tview.NewInputField().SetLabel(label).SetFieldWidth(0)
}
func list(title string) *tview.List {
	l := tview.NewList().ShowSecondaryText(false)
	l.SetBorder(true).SetTitle(title)
	return l
}

type page struct {
	name    string
	root    tview.Primitive
	focus   []tview.Primitive
	doc     *document
	refresh func()
}

type ui struct {
	app                            *tview.Application
	client                         *client
	ctx                            context.Context
	cancel                         context.CancelFunc
	session                        context.Context
	cancelSession                  context.CancelFunc
	epoch                          int
	pages, views                   *tview.Pages
	body                           *tview.Flex
	status                         *tview.TextView
	media                          *mediaPane
	pageDefs                       []page
	active                         int
	authenticated, dialogOpen      bool
	username                       string
	chat                           chatState
	news                           newsState
	training                       trainingState
	anki                           ankiState
	browsers                       map[int]*resourceBrowser
	header, help                   *tview.TextView
	headerActions, pageKeys, brand *tview.TextView
	command                        *tview.InputField
	commandBar                     *tview.Pages
	commandOpen, commandSearch     bool
	commandIdle                    *tview.TextView
	commandFocus                   tview.Primitive
	commandPriorFilter             string
	quitDeadline                   time.Time
	statusMessage                  string
}

func newUI(c *client, username, mode string) *ui {
	tview.Styles.PrimitiveBackgroundColor = tcell.ColorBlack
	tview.Styles.ContrastBackgroundColor = tcell.GetColor("#005f87")
	tview.Styles.MoreContrastBackgroundColor = tcell.GetColor("#303030")
	tview.Styles.BorderColor = tcell.GetColor("#80c7c7")
	tview.Styles.TitleColor = tcell.ColorAqua
	tview.Styles.PrimaryTextColor = tcell.ColorWhite
	tview.Borders.HorizontalFocus = tview.Borders.Horizontal
	tview.Borders.VerticalFocus = tview.Borders.Vertical
	tview.Borders.TopLeftFocus = tview.Borders.TopLeft
	tview.Borders.TopRightFocus = tview.Borders.TopRight
	tview.Borders.BottomLeftFocus = tview.Borders.BottomLeft
	tview.Borders.BottomRightFocus = tview.Borders.BottomRight
	ctx, cancel := context.WithCancel(context.Background())
	u := &ui{app: tview.NewApplication(), client: c, ctx: ctx, cancel: cancel,
		session: ctx, pages: tview.NewPages(), views: tview.NewPages(),
		news:   newsState{unreadOnly: true},
		status: tview.NewTextView(), media: newMedia(mode), username: username, browsers: map[int]*resourceBrowser{}}
	u.pageDefs = []page{u.buildChat(), u.buildAnki(), u.buildTraining(), u.buildNews()}
	for index, p := range u.pageDefs {
		root := p.root
		{
			name := strings.ToLower(p.name)
			b := newResourceBrowser(name, root)
			b.columns = [][]string{
				{"CONVERSATION", "MODEL", "UPDATED", "ITEMS"},
				{"DECK / CARD", "REVIEW", "NEW", "LEARNING", "TOTAL"},
				{"NAME / CONCEPT", "TYPE", "NOTES"},
				{"TITLE", "FEED", "DATE", "NEW/TOTAL"},
			}[index]
			b.render()
			u.browsers[index] = b
			root = b.pages
		}
		u.views.AddPage(p.name, root, true, index == 0)
	}
	u.body = tview.NewFlex().AddItem(u.views, 0, 1, true)
	u.header, u.help = tview.NewTextView().SetDynamicColors(true), tview.NewTextView().SetDynamicColors(true)
	u.headerActions = tview.NewTextView().SetDynamicColors(true).SetWrap(false)
	u.pageKeys = tview.NewTextView().SetDynamicColors(true).SetWrap(false)
	u.brand = tview.NewTextView().SetTextColor(tcell.ColorYellow).SetWrap(false).SetText(" ____  ____\n/ ___|| __ )\n\\___ \\|  _ \\\n ___) | |_) |\n|____/|____/\n SECOND BRAIN")
	u.header.SetWrap(false)
	u.command = input(":")
	u.commandIdle = tview.NewTextView().SetDynamicColors(true)
	u.command.SetAutocompleteFunc(func(text string) []string {
		if !u.commandOpen || u.commandSearch || strings.ContainsAny(text, " /\t") {
			return nil
		}
		var matches []string
		for _, name := range commandNames {
			if name == text {
				return nil
			}
			if strings.HasPrefix(name, strings.ToLower(text)) {
				matches = append(matches, name)
			}
		}
		return matches
	})
	u.command.SetChangedFunc(func(text string) {
		if u.commandOpen && u.commandSearch {
			b := u.browsers[u.active]
			b.filter = text
			b.render()
			u.updateChrome()
		}
	})
	u.commandBar = tview.NewPages().AddPage("idle", u.commandIdle, true, true).AddPage("input", u.command, true, false)
	for _, browser := range u.browsers {
		browser.changed = u.updateChrome
		browser.list.SetSelectionChangedFunc(func(int, int) { u.updateChrome() })
	}
	u.updateChrome()
	header := tview.NewFlex().AddItem(u.header, 0, 2, false).AddItem(u.headerActions, 0, 2, false).
		AddItem(u.pageKeys, 0, 1, false).AddItem(u.brand, 18, 0, false)
	root := tview.NewFlex().SetDirection(tview.FlexRow).AddItem(header, 7, 0, false).
		AddItem(u.commandBar, 1, 0, false).AddItem(u.body, 0, 1, true).
		AddItem(u.status, 1, 0, false).AddItem(u.help, 1, 0, false)
	u.pages.AddPage("main", root, true, true)
	u.app.SetRoot(u.pages, true).EnableMouse(true).EnablePaste(true)
	u.app.SetInputCapture(u.capture)
	u.app.SetAfterDrawFunc(func(screen tcell.Screen) {
		u.media.afterDraw(screen, !u.dialogOpen && !u.commandOpen && len(u.media.sources) > 0, os.Getenv("TMUX") != "", os.Stdout)
		// Suggestions overlay the resource list below the shared prompt.
		if u.commandOpen && !u.dialogOpen {
			u.command.Draw(screen)
		}
	})
	u.media.load = u.loadImage
	u.login()
	return u
}

func (u *ui) run() error {
	defer u.cancel()
	return u.app.Run()
}

func (u *ui) capture(event *tcell.EventKey) *tcell.EventKey {
	if event.Key() == tcell.KeyCtrlC {
		u.requestQuit()
		return nil
	}
	if u.dialogOpen {
		return event
	}
	if u.commandOpen {
		if event.Key() == tcell.KeyEnter || event.Key() == tcell.KeyEscape {
			u.finishCommand(event.Key() == tcell.KeyEnter)
			return nil
		}
		if u.commandSearch && (event.Key() == tcell.KeyUp || event.Key() == tcell.KeyDown) {
			u.browsers[u.active].list.InputHandler()(event, func(tview.Primitive) {})
			return nil
		}
		return event
	}
	_, editingField := u.app.GetFocus().(*tview.InputField)
	_, editingArea := u.app.GetFocus().(*tview.TextArea)
	if !editingField && !editingArea && event.Key() == tcell.KeyRune {
		if event.Rune() >= '0' && event.Rune() < '0'+rune(len(u.pageDefs)) {
			u.switchPage(int(event.Rune() - '0'))
			return nil
		}
		if event.Rune() == '?' {
			u.showHelp()
			return nil
		}
		if event.Rune() == 'r' {
			if refresh := u.pageDefs[u.active].refresh; refresh != nil {
				refresh()
			}
			return nil
		}
		if event.Rune() == ':' || event.Rune() == '/' {
			u.startCommand(event.Rune() == '/')
			return nil
		}
		if event.Rune() == 'c' {
			switch u.active {
			case 0:
				u.newChat()
			case 1:
				u.newAnkiNote()
			}
			return nil
		}
		if event.Rune() == 's' && u.active == 1 {
			u.syncAnki()
			return nil
		}
		if event.Rune() == 'u' && u.active == 1 {
			u.toggleAnkiCards()
			return nil
		}
		if u.active == 3 {
			switch event.Rune() {
			case ' ':
				if !u.browsers[3].detail {
					u.toggleNewsSelection()
					return nil
				}
			case 'u':
				u.toggleNewsUnread()
				return nil
			case 'm':
				u.toggleNewsRead()
				return nil
			case 's':
				u.newsAction("star", "")
				return nil
			case 'a':
				u.articleActions()
				return nil
			}
			if u.browsers[3].detail {
				switch event.Rune() {
				case 'o':
					u.openNewsOriginal()
					return nil
				}
			}
		}
	}
	if event.Key() == tcell.KeyCtrlG {
		if u.active == 0 {
			u.sendChat()
		}
		return nil
	}
	if event.Key() == tcell.KeyEscape {
		if u.active == 3 && !u.browsers[3].detail && len(u.news.selected) > 0 {
			u.clearNewsSelection()
			return nil
		}
		if u.active == 1 {
			u.backAnki()
			return nil
		}
		if b := u.browsers[u.active]; b != nil && !b.detail && b.filter != "" {
			b.filter = ""
			b.render()
			u.updateChrome()
			return nil
		}
		u.browse()
		return nil
	}
	if event.Key() == tcell.KeyCtrlL {
		u.media.lastDraw = "redraw"
		return nil
	}
	if event.Key() == tcell.KeyTab || event.Key() == tcell.KeyBacktab {
		if b := u.browsers[u.active]; b != nil && !b.detail {
			return nil
		}
		var focus []tview.Primitive
		var expand func(tview.Primitive)
		expand = func(p tview.Primitive) {
			if l, ok := p.(*tview.List); ok && l.GetItemCount() == 0 {
				return
			}
			if bar, ok := p.(*tview.Flex); ok {
				for i := 0; i < bar.GetItemCount(); i++ {
					if child := bar.GetItem(i); child != nil {
						expand(child)
					}
				}
				return
			}
			focus = append(focus, p)
		}
		for _, p := range u.pageDefs[u.active].focus {
			expand(p)
		}
		if len(u.media.sources) > 0 {
			expand(u.media.buttons)
		}
		if len(focus) == 0 {
			return nil
		}
		current := -1
		for i, p := range focus {
			if p.HasFocus() {
				current = i
				break
			}
		}
		step := 1
		if event.Key() == tcell.KeyBacktab {
			step = -1
		}
		u.app.SetFocus(focus[(current+step+len(focus))%len(focus)])
		return nil
	}
	return event
}

func (u *ui) switchPage(index int) {
	if u.dialogOpen || !u.authenticated || index < 0 || index >= len(u.pageDefs) {
		return
	}
	u.active = index
	p := u.pageDefs[index]
	u.views.SwitchToPage(p.name)
	if len(p.focus) > 0 {
		u.app.SetFocus(p.focus[0])
	}
	if u.browsers[index] != nil {
		u.browse()
	} else {
		u.updateMedia(p.doc)
		u.updateChrome()
	}
	if p.refresh != nil {
		p.refresh()
	}
}

func (u *ui) say(message string) {
	u.statusMessage = message
	if time.Now().Before(u.quitDeadline) {
		message = "Press Ctrl+C again within 3 seconds to quit.  " + message
	}
	u.status.SetText(message)
	if u.commandIdle != nil {
		u.updateChrome()
	}
}

func (u *ui) requestQuit() {
	now := time.Now()
	if now.Before(u.quitDeadline) {
		u.cancel()
		u.app.Stop()
		return
	}
	u.quitDeadline = now.Add(3 * time.Second)
	deadline := u.quitDeadline
	u.say(u.statusMessage)
	go func() {
		timer := time.NewTimer(time.Until(deadline))
		defer timer.Stop()
		select {
		case <-u.ctx.Done():
			return
		case <-timer.C:
		}
		u.app.QueueUpdateDraw(func() {
			if u.quitDeadline.Equal(deadline) {
				u.quitDeadline = time.Time{}
				u.say(u.statusMessage)
			}
		})
	}()
}

func (u *ui) fail(err error) {
	if errors.Is(err, context.Canceled) {
		return
	}
	u.say(err.Error())
	var api *apiError
	if errors.As(err, &api) && api.Status == 401 && u.authenticated {
		u.authenticated = false
		if u.chat.cancel != nil {
			u.chat.cancel()
		}
		u.login()
	}
}

// All state and widget changes run on tview's event loop. Background jobs only
// operate on captured request values and return a UI update closure.
func (u *ui) job(name string, work func(context.Context) (func(), error)) {
	ctx, epoch := u.session, u.epoch
	u.say(name + "…")
	go func() {
		apply, err := work(ctx)
		if u.ctx.Err() != nil {
			return
		}
		u.app.QueueUpdateDraw(func() {
			if epoch != u.epoch {
				return
			}
			if err != nil {
				u.fail(err)
				return
			}
			u.say("")
			if apply != nil {
				apply()
			}
		})
	}()
}

func centered(item tview.Primitive, width, height int) tview.Primitive {
	return tview.NewFlex().AddItem(nil, 0, 1, false).
		AddItem(tview.NewFlex().SetDirection(tview.FlexRow).AddItem(nil, 0, 1, false).
			AddItem(item, height, 0, true).AddItem(nil, 0, 1, false), width, 0, true).
		AddItem(nil, 0, 1, false)
}

func (u *ui) dialog(form *tview.Form, title string, width, height int) {
	form.SetButtonsAlign(tview.AlignCenter)
	form.SetBorder(true).SetTitle(title)
	u.dialogOpen = true
	modal := tview.NewFlex().SetDirection(tview.FlexRow).
		AddItem(centered(form, width, height), 0, 1, true).AddItem(u.status, 2, 0, false)
	u.pages.AddPage("dialog", modal, true, true)
	u.app.SetFocus(form)
}

func (u *ui) closeDialog() {
	u.pages.RemovePage("dialog")
	u.dialogOpen = false
	if len(u.pageDefs[u.active].focus) > 0 {
		u.app.SetFocus(u.pageDefs[u.active].focus[0])
	}
	if b := u.browsers[u.active]; b != nil && !b.detail {
		u.app.SetFocus(b.list)
	}
}

func (u *ui) prompt(title, value string, submit func(string)) {
	field := input("Value: ").SetText(value)
	form := tview.NewForm().AddFormItem(field)
	form.AddButton("Apply", func() { text := field.GetText(); u.closeDialog(); submit(text) }).AddButton("Cancel", u.closeDialog)
	u.dialog(form, title, 75, 9)
}

func (u *ui) login() {
	name := input("Username: ").SetText(u.username)
	password := input("Password: ").SetMaskCharacter('*')
	form := tview.NewForm().AddFormItem(name).AddFormItem(password)
	busy := false
	submit := func() {
		if busy {
			return
		}
		busy = true
		user, pass := name.GetText(), password.GetText()
		u.job("Signing in", func(ctx context.Context) (func(), error) {
			err := u.client.request(ctx, "POST", "api/auth/login", object{"username": user, "password": pass}, nil)
			return func() {
				busy = false
				if err != nil {
					u.say(err.Error())
					return
				}
				password.SetText("")
				u.username = user
				u.authenticated = true
				u.closeDialog()
				u.switchPage(u.active)
			}, nil
		})
	}
	form.SetInputCapture(func(event *tcell.EventKey) *tcell.EventKey {
		if event.Key() == tcell.KeyEnter && password.HasFocus() {
			submit()
			return nil
		}
		return event
	})
	u.dialog(form, "Second Brain — Sign in", 65, 8)
	if u.username != "" {
		form.SetFocus(1)
	}
}

func (u *ui) logout() {
	if u.chat.cancel != nil {
		u.chat.cancel()
	}
	u.job("Signing out", func(ctx context.Context) (func(), error) {
		if err := u.client.request(ctx, "POST", "api/auth/logout", nil, nil); err != nil {
			return nil, err
		}
		return func() {
			u.epoch++
			u.authenticated = false
			if u.cancelSession != nil {
				u.cancelSession()
			}
			u.session, u.cancelSession = context.WithCancel(u.ctx)
			u.chat.id = ""
			u.chat.text = ""
			u.chat.doc.show("Chat", "", "", "", nil)
			u.media.sources = nil
			u.media.setImage(nil)
			u.body.RemoveItem(u.media)
			u.login()
		}, nil
	})
}

func (u *ui) updateMedia(d *document) {
	u.body.RemoveItem(u.media)
	u.media.sources = nil
	u.media.setImage(nil)
	if d == nil || len(d.images) == 0 || u.media.mode == "off" {
		return
	}
	u.media.sources = append([]string(nil), d.images...)
	u.body.AddItem(u.media, 36, 0, false)
	u.loadImage(0)
}

func (u *ui) loadImage(index int) {
	if index < 0 || index >= len(u.media.sources) {
		return
	}
	u.media.index = index
	u.media.setImage(nil)
	source := u.media.sources[index]
	generation := u.media.generation
	u.job("Loading image", func(ctx context.Context) (func(), error) {
		data, err := u.client.image(ctx, source)
		var img image.Image
		if err == nil {
			img, err = decodeImage(data)
		}
		return func() {
			if u.media.generation != generation {
				return
			}
			if err != nil {
				u.media.caption.SetText("Image unavailable: " + err.Error())
				return
			}
			u.media.setImage(img)
		}, nil
	})
}

func (u *ui) showDocument(d *document, title, content, notePath, assetBase string, backlinks []link) {
	d.show(title, content, notePath, assetBase, backlinks)
	if u.pageDefs[u.active].doc == d && (u.browsers[u.active] == nil || u.browsers[u.active].detail) {
		u.updateMedia(d)
	}
}

func (u *ui) openLink(target, parent string) {
	if strings.HasPrefix(target, "note:") {
		target, _ = url.PathUnescape(strings.TrimPrefix(target, "note:"))
	} else if strings.HasPrefix(target, "http://") || strings.HasPrefix(target, "https://") {
		u.openBrowser(target)
		return
	}
	target, _, _ = strings.Cut(target, "#")
	if target == "" {
		return
	}
	if !strings.HasSuffix(target, ".md") {
		target += ".md"
	}
	u.job("Opening note", func(ctx context.Context) (func(), error) {
		var result note
		candidates := []string{target}
		if parent != "" {
			candidates = append([]string{path.Join(path.Dir(parent), target)}, candidates...)
		}
		for _, candidate := range candidates {
			err := u.client.request(ctx, "GET", query("api/vault/note", url.Values{"path": {candidate}}), nil, &result)
			if err == nil {
				return func() { u.showLinkedNote(result) }, nil
			}
			var api *apiError
			if !errors.As(err, &api) || api.Status != 404 {
				return nil, err
			}
		}
		var matches []string
		if err := u.client.request(ctx, "GET", query("api/vault/find", url.Values{"q": {strings.TrimSuffix(target, ".md")}}), nil, &matches); err != nil {
			return nil, err
		}
		if len(matches) == 0 {
			return nil, fmt.Errorf("note not found; use Training to generate a fiche")
		}
		if err := u.client.request(ctx, "GET", query("api/vault/note", url.Values{"path": {matches[0]}}), nil, &result); err != nil {
			return nil, err
		}
		return func() { u.showLinkedNote(result) }, nil
	})
}

func (u *ui) showLinkedNote(result note) {
	u.switchPage(2)
	u.training.parent.SetText(result.Path)
	u.showDocument(u.training.doc, result.Path, result.Content, result.Path, "", nil)
	u.reveal(2)
}

func (u *ui) openBrowser(address string) {
	var command *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		command = exec.Command("open", address)
	case "windows":
		command = exec.Command("rundll32", "url.dll,FileProtocolHandler", address)
	default:
		command = exec.Command("xdg-open", address)
	}
	u.job("Opening browser", func(context.Context) (func(), error) { return nil, command.Run() })
}
