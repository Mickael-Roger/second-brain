package main

import (
	"fmt"
	"image"
	"image/color"
	"strings"
	"testing"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

func TestDashboardTableLayout(t *testing.T) {
	c, err := newClient("https://brain.example.test")
	if err != nil {
		t.Fatal(err)
	}
	u := newUI(c, "reader", "off")
	defer u.cancel()
	u.closeDialog()
	u.active = 3
	u.views.SwitchToPage("News")
	u.news.unreadOnly = false
	logo := image.NewRGBA(image.Rect(0, 0, 16, 16))
	for y := 0; y < 16; y++ {
		for x := 0; x < 16; x++ {
			logo.Set(x, y, color.RGBA{255, 0, 0, 255})
		}
	}
	u.news.logos = map[string]*tview.Image{"logo": tview.NewImage().SetImage(logo).SetSize(1, 2).SetColors(tview.TrueColor)}
	u.news.feeds = []feed{{ID: "feed", Title: "Engineering", Group: "Technology", Favicon: "logo"}}
	u.news.articles = []article{
		{ID: "new", Title: "A new release to read", FeedID: "feed", PublishedAt: "2026-10-26T08:30:00Z"},
		{ID: "old", Title: "A previously read release", FeedID: "feed", PublishedAt: "2026-10-25T09:45:00Z", Read: true},
	}
	u.fillArticles()
	b := u.browsers[3]
	b.activate(0)
	b.activate(1)
	b.list.SetCurrentItem(3)
	u.browse()
	if strings.Contains(u.pageKeys.GetText(true), "Pending") || strings.Contains(u.brand.GetText(true), "T U I") {
		t.Error("dashboard contains removed labels")
	}
	if strings.Contains(u.header.GetText(true), "<enter>") || strings.Contains(u.header.GetText(true), "<ctrl-c>") {
		t.Error("dashboard contains hidden keyboard hints")
	}
	for _, width := range []int{80, 140, 200} {
		t.Run(fmt.Sprint(width), func(t *testing.T) {
			screen := tcell.NewSimulationScreen("UTF-8")
			if err := screen.Init(); err != nil {
				t.Fatal(err)
			}
			defer screen.Fini()
			screen.SetSize(width, 30)
			u.pages.SetRect(0, 0, width, 30)
			u.pages.Draw(screen)
			screen.Show()
			line := func(y int) string {
				var text strings.Builder
				for x := 0; x < width; x++ {
					r, _, _, _ := screen.GetContent(x, y)
					if r == 0 {
						r = ' '
					}
					text.WriteRune(r)
				}
				return text.String()
			}
			for index, name := range []string{"chat", "anki", "training", "news"} {
				if !strings.Contains(line(index), fmt.Sprintf("<%d> %s", index, name)) {
					t.Errorf("missing page key on row %d: %s", index, line(index))
				}
			}
			_, headingY, _, _ := b.list.GetInnerRect()
			heading := line(headingY)
			for _, column := range []string{"TITLE", "FEED", "DATE", "NEW/TOTAL"} {
				if !strings.Contains(heading, column) {
					t.Errorf("column %s was clipped: %s", column, heading)
				}
			}
			_, _, headerStyle, _ := screen.GetContent(1, headingY)
			_, bg, _ := headerStyle.Decompose()
			if bg != tcell.GetColor("#80c7c7") {
				t.Errorf("column heading background: %v", bg)
			}
			_, _, selectedStyle, _ := screen.GetContent(1, headingY+4)
			_, bg, _ = selectedStyle.Decompose()
			if bg != tcell.GetColor("#005f87") {
				t.Errorf("selected row background: %v", bg)
			}
			if got := line(headingY + 1); !strings.Contains(got, "1/2") {
				t.Errorf("category unread/total: %s", got)
			}
			for offset, name := range []string{"Technology", "Engineering"} {
				if got := line(headingY + 1 + offset); !strings.Contains(got, name+" (1 unread)") {
					t.Errorf("missing unread count beside %s: %s", name, got)
				}
			}
			if got := line(headingY + 3); !strings.Contains(got, "2026-10-26") || strings.Contains(got, "08:30") {
				t.Errorf("news date must omit the time: %s", got)
			}
			feedX, feedY, _ := b.list.GetCell(2, 0).GetLastPosition()
			_, _, feedStyle, _ := screen.GetContent(feedX+7, feedY)
			fg, _, attrs := feedStyle.Decompose()
			if fg != tcell.ColorFuchsia || attrs&tcell.AttrBold == 0 {
				t.Errorf("feed name must be magenta and bold: %v", feedStyle)
			}
			for _, cell := range []struct{ row, col, inset int }{{2, 0, 4}, {3, 1, 0}} {
				x, y, _ := b.list.GetCell(cell.row, cell.col).GetLastPosition()
				_, _, logoStyle, _ := screen.GetContent(x+cell.inset, y)
				fg, bg, _ := logoStyle.Decompose()
				if fg != tcell.ColorRed && bg != tcell.ColorRed && fg != tcell.NewRGBColor(255, 0, 0) && bg != tcell.NewRGBColor(255, 0, 0) {
					t.Errorf("feed logo missing in row %d, column %d: %v", cell.row, cell.col, logoStyle)
				}
			}
			if !strings.Contains(line(29), "news(all)") {
				t.Error("missing view breadcrumb")
			}
			if width == 140 {
				var snapshot strings.Builder
				for y := 0; y < 17; y++ {
					snapshot.WriteString(strings.TrimRight(line(y), " ") + "\n")
				}
				t.Log("\n" + snapshot.String())
			}
		})
	}
}
