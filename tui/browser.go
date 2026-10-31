package main

import (
	"fmt"
	"sort"
	"strings"
	"unicode"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

// resource is a group or a leaf. Only explicit activation opens a leaf.
type resource struct {
	id, title, meta string
	style           string
	columns         []string
	columnStyles    []string
	icon            *tview.Image
	columnIcons     []*tview.Image
	unread          bool
	marked          bool
	children        []*resource
	open            func()
	onToggle        func(bool)
}

type resourceRow struct {
	item         *resource
	depth, score int
	path         string
}

type resourceBrowser struct {
	title       string
	pages       *tview.Pages
	list        *resourceTable
	columns     []string
	mode        string
	items       []*resource
	rows        []resourceRow
	expanded    map[string]bool
	filter      string
	detail      bool
	changed     func()
	markedCount int
}

func newResourceBrowser(title string, detail tview.Primitive) *resourceBrowser {
	b := &resourceBrowser{title: title, list: newResourceTable(), columns: []string{"NAME / PATH", "DETAILS", "ITEMS"}, expanded: map[string]bool{}, pages: tview.NewPages()}
	b.list.SetSelectedFunc(func(row, _ int) { b.activate(row - 1) })
	b.pages.AddPage("list", b.list, true, true).AddPage("detail", detail, true, false)
	b.render()
	return b
}

func (b *resourceBrowser) activate(index int) {
	if index < 0 || index >= len(b.rows) {
		return
	}
	r := b.rows[index].item
	if r.children != nil {
		b.expanded[r.id] = !b.expanded[r.id]
		b.render()
		if r.onToggle != nil {
			r.onToggle(b.expanded[r.id])
		}
	} else if r.open != nil {
		r.open()
	}
}

func (b *resourceBrowser) set(items []*resource) { b.items = items; b.render() }

func (b *resourceBrowser) render() {
	selected := ""
	index := b.list.GetCurrentItem()
	if index >= 0 && index < len(b.rows) {
		selected = b.rows[index].item.id
	}
	b.rows = nil
	var walk func([]*resource, int, string)
	walk = func(items []*resource, depth int, parent string) {
		for _, item := range items {
			path := strings.TrimSpace(parent + " / " + item.title)
			if b.filter != "" {
				// Search all leaves, including those in collapsed groups.
				if item.children == nil {
					if score, ok := fuzzyScore(b.filter, path+" "+item.meta+" "+strings.Join(item.columns, " ")); ok {
						b.rows = append(b.rows, resourceRow{item: item, score: score, path: path})
					}
				}
				walk(item.children, depth+1, path)
			} else {
				b.rows = append(b.rows, resourceRow{item: item, depth: depth})
				if b.expanded[item.id] {
					walk(item.children, depth+1, path)
				}
			}
		}
	}
	walk(b.items, 0, "")
	if b.filter != "" {
		sort.SliceStable(b.rows, func(i, j int) bool { return b.rows[i].score > b.rows[j].score })
	}
	b.list.Clear()
	b.list.icons = nil
	for col, heading := range b.columns {
		cell := tview.NewTableCell(heading).SetSelectable(false).
			SetTextColor(tcell.GetColor("#303c3c")).SetBackgroundColor(tcell.GetColor("#80c7c7"))
		if col == 0 {
			cell.SetExpansion(1)
		}
		b.list.SetCell(0, col, cell)
	}
	matchedSelection := false
	for index, row := range b.rows {
		prefix := "  "
		if row.item.children != nil {
			prefix = "▸ "
			if b.expanded[row.item.id] {
				prefix = "▾ "
			}
		}
		label := row.item.title
		if b.filter != "" {
			label = strings.TrimPrefix(row.path, "/ ")
		}
		if b.title == "news" && row.item.children != nil {
			_, unread := resourceCounts(row.item)
			label += fmt.Sprintf(" (%d unread)", unread)
		}
		values := make([]string, len(b.columns))
		values[0] = strings.Repeat("  ", row.depth) + prefix + label
		if row.item.icon != nil {
			inset := row.depth*2 + len([]rune(prefix))
			values[0] = strings.Repeat("  ", row.depth) + prefix + "   " + label
			b.list.icons = append(b.list.icons, tableIcon{index + 1, 0, inset, row.item.icon})
		}
		copy(values[1:], row.item.columns)
		for col, icon := range row.item.columnIcons {
			if icon != nil && col+1 < len(values) {
				values[col+1] = "   " + values[col+1]
				b.list.icons = append(b.list.icons, tableIcon{index + 1, col + 1, 0, icon})
			}
		}
		if len(row.item.columns) == 0 && len(values) > 1 {
			values[1] = row.item.meta
		}
		style := row.item.style
		if row.item.children != nil {
			if style == "" {
				style = "aqua"
			}
			total, unread := resourceCounts(row.item)
			if b.title != "anki" {
				values[len(values)-1] = fmt.Sprint(total)
			}
			if b.title == "news" {
				values[len(values)-1] = fmt.Sprintf("%d/%d", unread, total)
			}
		}
		for col, value := range values {
			cellStyle := style
			if col > 0 && col-1 < len(row.item.columnStyles) && row.item.columnStyles[col-1] != "" {
				cellStyle = row.item.columnStyles[col-1]
			}
			cell := tview.NewTableCell(highlightStyledMatch(b.filter, value, cellStyle)).SetTextColor(tcell.ColorWhite)
			if row.item.marked {
				cell.SetBackgroundColor(tcell.GetColor("#3a2858"))
			}
			if col == 0 {
				cell.SetExpansion(1)
			}
			if col == len(values)-1 {
				cell.SetAlign(tview.AlignRight)
			}
			b.list.SetCell(index+1, col, cell)
		}
		if row.item.id == selected {
			b.list.SetCurrentItem(index)
			matchedSelection = true
		}
	}
	if !matchedSelection && len(b.rows) > 0 {
		b.list.SetCurrentItem(min(max(index, 0), len(b.rows)-1))
	}
	if len(b.rows) == 0 {
		for col := range b.columns {
			text := ""
			if col == 0 {
				text = "  No matching items"
			}
			b.list.SetCell(1, col, tview.NewTableCell(text).SetTextColor(tcell.ColorGray).SetSelectable(false))
		}
	}
	total, _ := resourceTotals(b.items)
	mode := b.mode
	if mode == "" {
		mode = "all"
	}
	suffix := fmt.Sprintf(" %s(%s)[%d items / %d rows] ", b.title, mode, total, len(b.rows))
	if b.filter != "" {
		suffix = fmt.Sprintf(" %s(%s)[%d matches / %d items] /%s ", b.title, mode, len(b.rows), total, tview.Escape(b.filter))
	}
	if b.markedCount > 0 {
		suffix += fmt.Sprintf("[%d selected] ", b.markedCount)
	}
	b.list.SetTitle(suffix).SetTitleAlign(tview.AlignCenter)
	if b.changed != nil {
		b.changed()
	}
}

func resourceCounts(item *resource) (total, unread int) {
	if item.children == nil {
		if item.unread {
			unread = 1
		}
		return 1, unread
	}
	return resourceTotals(item.children)
}

func resourceTotals(items []*resource) (total, unread int) {
	for _, child := range items {
		count, newCount := resourceCounts(child)
		total += count
		unread += newCount
	}
	return
}

func highlightMatch(query, text string) string {
	return highlightStyledMatch(query, text, "")
}

func highlightStyledMatch(query, text, style string) string {
	runes := []rune(text)
	matched := map[int]bool{}
	for _, term := range strings.Fields(strings.ToLower(query)) {
		pos := 0
		for _, r := range term {
			for pos < len(runes) && unicode.ToLower(runes[pos]) != r {
				pos++
			}
			if pos < len(runes) {
				matched[pos] = true
				pos++
			}
		}
	}
	var result strings.Builder
	reset := "[-::-]"
	if style != "" {
		reset = "[" + style + "]"
		result.WriteString(reset)
	}
	for start := 0; start < len(runes); {
		end := start + 1
		for end < len(runes) && matched[end] == matched[start] {
			end++
		}
		if matched[start] {
			result.WriteString("[yellow::b]")
		}
		result.WriteString(tview.Escape(string(runes[start:end])))
		if matched[start] {
			result.WriteString(reset)
		}
		start = end
	}
	if style != "" {
		result.WriteString("[-:-:-]")
	}
	return result.String()
}

// Ordered subsequences with consecutive/word-boundary bonuses, ranked live.
// Space-separated terms can match different parts of the candidate, as in fzf.
func fuzzyScore(query, candidate string) (int, bool) {
	text := []rune(strings.ToLower(candidate))
	total := 0
	for _, term := range strings.Fields(strings.ToLower(query)) {
		position, previous := 0, -2
		for _, needle := range []rune(term) {
			found := -1
			for i := position; i < len(text); i++ {
				if text[i] == needle {
					found = i
					break
				}
			}
			if found < 0 {
				return 0, false
			}
			total += 10
			if found == previous+1 {
				total += 20
			}
			if found == 0 || !unicode.IsLetter(text[found-1]) && !unicode.IsDigit(text[found-1]) {
				total += 15
			}
			total -= found - position
			previous, position = found, found+1
		}
	}
	return total, true
}

func noteResources(entries []treeEntry, open func(string)) []*resource {
	root := &resource{children: []*resource{}}
	folders := map[string]*resource{"": root}
	for _, entry := range entries {
		if entry.Type != "file" || !strings.HasSuffix(entry.Path, ".md") {
			continue
		}
		parts := strings.Split(entry.Path, "/")
		parent := root
		for i, part := range parts {
			key := strings.Join(parts[:i+1], "/")
			if i == len(parts)-1 {
				p := entry.Path
				parent.children = append(parent.children, &resource{id: p, title: part, columns: []string{"note"}, open: func() { open(p) }})
			} else {
				folder := folders[key]
				if folder == nil {
					folder = &resource{id: key, title: part, columns: []string{"folder"}, children: []*resource{}}
					folders[key] = folder
					parent.children = append(parent.children, folder)
				}
				parent = folder
			}
		}
	}
	return root.children
}

func (u *ui) reveal(index int) {
	if u.active != index {
		return
	}
	b := u.browsers[index]
	if b == nil {
		return
	}
	b.detail = true
	b.pages.SwitchToPage("detail")
	u.app.SetFocus(u.pageDefs[index].doc.text)
	u.updateMedia(u.pageDefs[index].doc)
	u.updateChrome()
}

func (u *ui) browse() {
	b := u.browsers[u.active]
	if b == nil {
		return
	}
	b.detail = false
	b.pages.SwitchToPage("list")
	u.app.SetFocus(b.list)
	u.updateMedia(nil)
	u.updateChrome()
}

func (u *ui) startCommand(search bool) {
	b := u.browsers[u.active]
	if search && b == nil {
		return
	}
	if search {
		u.browse()
	}
	u.commandFocus = u.app.GetFocus()
	u.commandPriorFilter = ""
	if search {
		u.commandPriorFilter = b.filter
	}
	u.commandSearch = search
	u.commandOpen = true
	label := ":"
	if search {
		label = "/"
	}
	u.command.SetLabel(label).SetText("")
	if search {
		u.command.SetText(u.commandPriorFilter)
	}
	u.commandBar.SwitchToPage("input")
	u.app.SetFocus(u.command)
	u.command.Autocomplete()
}

func (u *ui) closeCommand() {
	u.commandOpen = false
	u.commandBar.SwitchToPage("idle")
	if u.commandFocus != nil {
		u.app.SetFocus(u.commandFocus)
	}
	u.updateChrome()
}

func (u *ui) finishCommand(accept bool) {
	text, search := u.command.GetText(), u.commandSearch
	if search && !accept {
		b := u.browsers[u.active]
		b.filter = u.commandPriorFilter
		b.render()
	}
	u.closeCommand()
	if accept && !search {
		u.executeCommand(text)
	}
}

var commandNames = []string{"chat", "news", "training", "anki", "new", "plan", "generate", "stop", "fetch", "feeds", "unread", "cards", "read", "star", "original", "actions", "clear", "refresh", "sync", "logout", "help", "quit"}

func (u *ui) executeCommand(text string) {
	text = strings.TrimPrefix(strings.TrimSpace(text), ":")
	name, args, _ := strings.Cut(text, " ")
	name = strings.ToLower(name)
	for command, index := range map[string]int{"chat": 0, "anki": 1, "training": 2, "news": 3} {
		if name == command {
			u.switchPage(index)
			if b := u.browsers[index]; b != nil && strings.HasPrefix(strings.TrimSpace(args), "/") {
				b.filter = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(args), "/"))
				b.render()
				u.updateChrome()
			}
			return
		}
	}
	switch name {
	case "q", "quit":
		u.cancel()
		u.app.Stop()
	case "logout":
		u.logout()
	case "refresh":
		if refresh := u.pageDefs[u.active].refresh; refresh != nil {
			refresh()
		}
	case "sync":
		if u.active == 1 {
			u.syncAnki()
		}
	case "cards":
		if u.active == 1 {
			u.toggleAnkiCards()
		}
	case "new":
		if u.active == 0 {
			u.newChat()
			u.app.SetFocus(u.chat.composer)
		}
		if u.active == 1 {
			u.newAnkiNote()
		}
	case "stop":
		if u.chat.cancel != nil {
			u.chat.cancel()
		}
	case "plan":
		u.newLearningPlan()
	case "generate":
		u.switchPage(2)
		u.reveal(2)
		u.app.SetFocus(u.training.concept)
	case "fetch":
		u.fetchNews()
	case "feeds":
		u.manageFeeds()
	case "unread":
		if u.active == 3 {
			u.toggleNewsUnread()
		}
	case "read":
		if u.active == 3 {
			u.toggleNewsRead()
		}
	case "star", "original", "actions":
		if u.active == 3 {
			switch name {
			case "star":
				u.newsAction("star", "")
			case "original":
				u.openNewsOriginal()
			case "actions":
				u.articleActions()
			}
		}
	case "clear":
		if u.active == 3 {
			u.clearNewsSelection()
		}
	case "help", "":
		u.showHelp()
	default:
		u.say("Unknown command: " + name + " — :help for commands")
	}
}
