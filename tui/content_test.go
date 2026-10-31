package main

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/color"
	"image/png"
	"strings"
	"testing"

	"github.com/gdamore/tcell/v2"
)

func TestMarkdownLinksAndImages(t *testing.T) {
	text, links, images := renderMarkdown("# Title\n\n[[Wiki/Hello|Read]] ![[Attachments/chart.png]]\n\n![relative](../img.png)\n\n```go\nfmt.Println(42)\n```", "Wiki/Test.md", "")
	if !strings.Contains(text, "Title") || !strings.Contains(text, "fmt.Println(42)") || len(links) != 1 || links[0].Label != "Read" {
		t.Fatalf("%q %+v", text, links)
	}
	if len(images) != 2 || images[0] != "api/vault/file?path=Attachments%2Fchart.png" || images[1] != "api/vault/file?path=img.png" {
		t.Fatalf("%+v", images)
	}
	_, _, images = renderMarkdown("![News](/images/news.png)", "", "https://news.test/story")
	if images[0] != "https://news.test/images/news.png" {
		t.Fatal(images)
	}
	if got := assetURL("https://img.test/a%2Fb.png?token=x%2Fy", "", "https://news.test"); got != "https://img.test/a%2Fb.png?token=x%2Fy" {
		t.Fatal(got)
	}
	if strings.Contains(text, "Attachments/chart.png") {
		t.Fatal("image path rendered instead of preview reference")
	}
}

func TestImageProtocols(t *testing.T) {
	img := image.NewRGBA(image.Rect(0, 0, 64, 32))
	img.Set(0, 0, color.RGBA{255, 0, 0, 255})
	var source bytes.Buffer
	if err := png.Encode(&source, img); err != nil {
		t.Fatal(err)
	}
	decoded, err := decodeImage(source.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := decodeImage([]byte("broken image")); err == nil {
		t.Fatal("accepted invalid image")
	}
	sequence, err := encodeNative(decoded, "iterm2", 32, 10, 8, 16)
	if err != nil || !strings.HasPrefix(sequence, "\x1b]1337;File=inline=1;width=32;height=10;") {
		t.Fatalf("%q %v", sequence, err)
	}
	_, data, _ := strings.Cut(sequence, ":")
	data = strings.TrimSuffix(data, "\a")
	bytes, err := base64.StdEncoding.DecodeString(data)
	if err != nil || len(bytes) == 0 {
		t.Fatal(err)
	}
	sixel, err := encodeNative(decoded, "sixel", 32, 10, 8, 16)
	if err != nil || !strings.Contains(sixel, "\x1bP") || !strings.HasSuffix(sixel, "\x1b\\") {
		t.Fatalf("invalid sixel: %q %v", sixel, err)
	}
	wrapped := tmuxEscape(sequence)
	if !strings.HasPrefix(wrapped, "\x1bPtmux;\x1b\x1b]1337") || !strings.HasSuffix(wrapped, "\x1b\\") {
		t.Fatalf("invalid passthrough: %q", wrapped)
	}
	fitted := fitImage(img, 16, 16).Bounds()
	if fitted.Dx() != 16 || fitted.Dy() != 8 {
		t.Fatal(fitted)
	}
}

func TestImageMode(t *testing.T) {
	mode, err := imageMode("auto", func(key string) string {
		if key == "WEZTERM_PANE" {
			return "1"
		}
		return ""
	})
	if err != nil || mode != "iterm2" {
		t.Fatalf("%s %v", mode, err)
	}
	mode, _ = imageMode("auto", func(string) string { return "" })
	if mode != "halfcell" {
		t.Fatal(mode)
	}
	if _, err := imageMode("unknown", func(string) string { return "" }); err == nil {
		t.Fatal("accepted unknown protocol")
	}
}

func TestImagePaneRedrawAndPortableRendering(t *testing.T) {
	screen := tcell.NewSimulationScreen("UTF-8")
	if err := screen.Init(); err != nil {
		t.Fatal(err)
	}
	defer screen.Fini()
	screen.SetSize(80, 30)
	img := image.NewRGBA(image.Rect(0, 0, 64, 32))
	for y := 0; y < 32; y++ {
		for x := 0; x < 64; x++ {
			img.Set(x, y, color.RGBA{0, 100, 240, 255})
		}
	}
	m := newMedia("iterm2")
	m.sources = []string{"image.png"}
	m.setImage(img)
	m.SetRect(40, 1, 36, 25)
	m.Draw(screen)
	var output bytes.Buffer
	m.afterDraw(screen, true, true, &output)
	if !strings.Contains(output.String(), "\x1bPtmux;") {
		t.Fatal("native image missing tmux envelope")
	}
	output.Reset()
	m.afterDraw(screen, true, true, &output)
	if output.Len() != 0 {
		t.Fatal("unchanged native image redrawn")
	}
	m.afterDraw(screen, false, true, &output)
	if m.lastDraw != "" {
		t.Fatal("native image not cleared for modal")
	}
	m.afterDraw(screen, true, true, &output)
	if output.Len() == 0 {
		t.Fatal("native image not restored after modal")
	}
	portable := newMedia("halfcell")
	portable.setImage(img)
	portable.SetRect(1, 1, 36, 25)
	portable.Draw(screen)
	x, y, w, h := portable.picture.GetInnerRect()
	visible := false
	for row := y; row < y+h; row++ {
		for column := x; column < x+w; column++ {
			primary, _, _, _ := screen.GetContent(column, row)
			if primary != ' ' && primary != 0 {
				visible = true
			}
		}
	}
	if !visible {
		t.Fatal("portable image was not drawn")
	}
}
