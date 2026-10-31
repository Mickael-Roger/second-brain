package main

import (
	"fmt"
	"strings"
	"time"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

func keyHint(key, label string) string { return "[aqua]<" + key + ">[-]  [gray]" + label + "[-]" }

func (u *ui) updateChrome() {
	u.header.SetText(strings.Join([]string{
		keyHint("?", "Help"), keyHint(":", "Command"), keyHint("/", "Local search"),
		keyHint("esc", "Back"), keyHint("r", "Refresh"),
	}, "\n"))
	actions := []string{keyHint("tab", "Next control")}
	switch u.active {
	case 0:
		actions = append(actions, keyHint("c", "New conversation"), keyHint("ctrl-g", "Send message"), keyHint(":stop", "Stop reply"), keyHint(":plan", "Learning plan"))
	case 1:
		actions = append(actions, keyHint("c", "New note"), keyHint("enter", "Expand / review"))
		label := "Sync AnkiWeb"
		if u.anki.syncing {
			label = "Syncing…"
		}
		actions = append(actions, keyHint("s", label))
		visibility := "Show all cards"
		if u.anki.showAll {
			visibility = "Show due only"
		}
		actions = append(actions, keyHint("u", visibility))
	case 2:
		actions = append(actions, keyHint(":generate", "Generate fiche"), keyHint(":plan", "Learning plan"))
	case 3:
		readVisibility := "Hide read"
		if u.news.unreadOnly {
			readVisibility = "Show read"
		}
		actions = append(actions, keyHint("u", readVisibility))
		if u.browsers[3].detail {
			actions = append(actions, keyHint("m", "Toggle read"), keyHint("s", "Toggle star"), keyHint("o", "Open original"), keyHint("a", "Article actions"))
		} else {
			actions = []string{keyHint("space", "Select / deselect"), keyHint("u", readVisibility), keyHint("m", "Toggle read"), keyHint("s", "Toggle star"), keyHint("a", "Article actions"), keyHint("esc", "Clear selection")}
		}
	}
	actions = append(actions, keyHint(":logout", "Sign out"))
	u.headerActions.SetText(strings.Join(actions, "\n"))
	var navigation []string
	for index, p := range u.pageDefs {
		name := strings.ToLower(p.name)
		entry := fmt.Sprintf("[aqua]<%d>[-] [gray]%s[-]", index, name)
		if index == u.active {
			entry = fmt.Sprintf("[fuchsia]<%d>[-] [yellow]%s[-]", index, name)
		}
		navigation = append(navigation, entry)
	}
	u.pageKeys.SetText(strings.Join(navigation, "\n"))

	name := strings.ToLower(u.pageDefs[u.active].name)
	mode, prompt, context := "all", "", ""
	if b := u.browsers[u.active]; b != nil {
		if b.mode != "" {
			mode = b.mode
		}
		if b.detail {
			mode = "read"
		}
		if b.filter != "" {
			prompt = "[aqua]/[-] " + tview.Escape(b.filter)
		}
		index := b.list.GetCurrentItem()
		if !b.detail && index >= 0 && index < len(b.rows) {
			context = strings.TrimPrefix(b.rows[index].path, "/ ")
		}
		count, _ := resourceTotals(b.items)
		context = fmt.Sprintf("%s  %d items", context, count)
		if u.active == 3 && !b.detail && len(u.news.selected) > 0 {
			context += fmt.Sprintf("  %d selected", len(u.news.selected))
		}
	}
	u.commandIdle.SetText(prompt)
	u.help.SetText(fmt.Sprintf("[black:yellow] %s(%s) [-:-:-]  [aqua]%s[-]  [gray]%s @ %s[-]", name, mode, tview.Escape(context), tview.Escape(u.username), tview.Escape(u.client.base.Host)))
}

func (u *ui) showHelp() {
	text := "PAGES\n0 chat   1 anki   2 training   3 news\n\n" +
		"NAVIGATION\nUp / Down: select   Enter: unfold / open   Esc: back\n" +
		": command   / fuzzy filter   Tab: complete command\n" +
		"Example: :news /security   r: refresh\n\n" +
		"NEWS\nu / :unread: hide / show read articles   m / :read: toggle read\n" +
		"Space: select / deselect article or group   Esc / :clear: clear selection\n" +
		"s / :star: toggle star   a / :actions: actions on selected articles\n" +
		"Open article: o / :original: open original\n\n" +
		"CHAT / ANKI\nc: new conversation / new Anki note\nAnki: s / :sync: sync with AnkiWeb and refresh decks\nu / :cards: toggle due / all cards (read-only browsing)\nYellow decks: reviews due; aqua: new cards; gray: up to date\nEnter expands decks; Enter on a card opens a popup\nPopup: show answer; rate in due mode; Esc closes; Tab moves focus\n\n" +
		"ACTIONS\n:new :plan :generate :stop\n" +
		":fetch :feeds :refresh :logout :quit\n\n" +
		"Ctrl+G: send chat\nCtrl+C twice within 3 seconds: quit\n\n" +
		"Page keys and letter shortcuts are inactive while typing."
	modal := tview.NewModal().SetText(text).AddButtons([]string{"Close"}).SetDoneFunc(func(int, string) { u.closeDialog() })
	modal.SetInputCapture(func(event *tcell.EventKey) *tcell.EventKey {
		if event.Key() == tcell.KeyEscape {
			u.closeDialog()
			return nil
		}
		return event
	})
	u.dialogOpen = true
	u.pages.AddPage("dialog", modal, true, true)
	u.app.SetFocus(modal)
}

func compactDate(value string) string {
	if date, err := time.Parse(time.RFC3339, value); err == nil {
		return date.Format("2006-01-02 15:04")
	}
	return value
}

func dateOnly(value string) string {
	if date, err := time.Parse(time.RFC3339, value); err == nil {
		return date.Format("2006-01-02")
	}
	return value
}
