package main

import (
	"fmt"
	"image"
	"image/color"
	"testing"

	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

func TestFeedLogosFollowTableScrolling(t *testing.T) {
	b := newResourceBrowser("news", nil)
	items := make([]*resource, 12)
	colors := make([]tcell.Color, len(items))
	for index := range items {
		c := color.RGBA{uint8(20 + index*15), 80, 200, 255}
		img := image.NewRGBA(image.Rect(0, 0, 8, 8))
		for y := 0; y < 8; y++ {
			for x := 0; x < 8; x++ {
				img.Set(x, y, c)
			}
		}
		colors[index] = tcell.NewRGBColor(int32(c.R), int32(c.G), int32(c.B))
		items[index] = &resource{id: fmt.Sprint(index), title: fmt.Sprintf("Feed %d", index), children: []*resource{},
			icon: tview.NewImage().SetImage(img).SetSize(1, 2).SetColors(tview.TrueColor).SetDithering(tview.DitheringNone)}
	}
	b.set(items)
	screen := tcell.NewSimulationScreen("UTF-8")
	if err := screen.Init(); err != nil {
		t.Fatal(err)
	}
	defer screen.Fini()
	screen.SetSize(60, 8)
	b.list.SetRect(0, 0, 60, 8)
	closeColor := func(a, b tcell.Color) bool {
		r1, g1, b1 := a.RGB()
		r2, g2, b2 := b.RGB()
		return max(r1, r2)-min(r1, r2) <= 2 && max(g1, g2)-min(g1, g2) <= 2 && max(b1, b2)-min(b1, b2) <= 2
	}
	for _, selection := range []int{0, 11, 3, 0} {
		b.list.SetCurrentItem(selection)
		b.list.Draw(screen)
		offset, _ := b.list.GetOffset()
		_, innerY, _, height := b.list.GetInnerRect()
		for index := offset; index < len(items) && index-offset+1 < height; index++ {
			x, y, _ := b.list.GetCell(index+1, 0).GetLastPosition()
			if y != innerY+index-offset+1 {
				t.Fatalf("unexpected row position after scrolling: %d", y)
			}
			_, _, style, _ := screen.GetContent(x+2, y)
			fg, bg, _ := style.Decompose()
			if !closeColor(fg, colors[index]) && !closeColor(bg, colors[index]) {
				t.Errorf("feed %d has another row's logo after selecting %d: %v", index, selection, style)
			}
		}
		if r, _, _, _ := screen.GetContent(1, innerY); r != 'N' {
			t.Error("a scrolled-out logo overwrote the fixed column heading")
		}
	}
}
