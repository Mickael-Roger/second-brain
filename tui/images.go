package main

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	_ "image/png"
	"io"
	"strings"

	"github.com/gdamore/tcell/v2"
	"github.com/mattn/go-sixel"
	"github.com/rivo/tview"
	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

func imageMode(mode string, getenv func(string) string) (string, error) {
	switch mode {
	case "auto":
		if getenv("WEZTERM_PANE") != "" || getenv("TERM_PROGRAM") == "WezTerm" || getenv("TERM_PROGRAM") == "iTerm.app" {
			return "iterm2", nil
		}
		return "halfcell", nil
	case "iterm2", "sixel", "halfcell", "off":
		return mode, nil
	default:
		return "", fmt.Errorf("unknown image mode %q (auto, iterm2, sixel, halfcell, off)", mode)
	}
}

func tmuxEscape(sequence string) string {
	return "\x1bPtmux;" + strings.ReplaceAll(sequence, "\x1b", "\x1b\x1b") + "\x1b\\"
}

func fitImage(src image.Image, width, height int) image.Image {
	b := src.Bounds()
	w, h := b.Dx(), b.Dy()
	if w <= width && h <= height {
		return src
	}
	if w*height > h*width {
		h = max(1, h*width/w)
		w = width
	} else {
		w = max(1, w*height/h)
		h = height
	}
	dst := image.NewRGBA(image.Rect(0, 0, max(1, w), max(1, h)))
	draw.CatmullRom.Scale(dst, dst.Bounds(), src, b, draw.Over, nil)
	return dst
}

func decodeImage(data []byte) (image.Image, error) {
	config, _, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	if config.Width > 16000 || config.Height > 16000 || int64(config.Width)*int64(config.Height) > 40_000_000 {
		return nil, fmt.Errorf("image dimensions are too large")
	}
	img, _, err := image.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	return fitImage(img, 1200, 900), nil
}

func encodeNative(img image.Image, mode string, columns, rows, cellW, cellH int) (string, error) {
	var data bytes.Buffer
	if mode == "iterm2" {
		if err := jpeg.Encode(&data, fitImage(img, 800, 600), &jpeg.Options{Quality: 88}); err != nil {
			return "", err
		}
		return fmt.Sprintf("\x1b]1337;File=inline=1;width=%d;height=%d;preserveAspectRatio=1;doNotMoveCursor=1:%s\a",
			columns, rows, base64.StdEncoding.EncodeToString(data.Bytes())), nil
	}
	encoder := sixel.NewEncoder(&data)
	encoder.Colors = 128
	err := encoder.Encode(fitImage(img, columns*cellW, rows*cellH))
	return data.String(), err
}

// mediaPane reserves its rectangle in tcell. Native graphics are emitted only
// after tcell flushes its cell buffer, so they cannot be erased by that flush.
type mediaPane struct {
	*tview.Flex
	picture                       *tview.Image
	blank                         *tview.Box
	caption                       *tview.TextView
	buttons                       *tview.Flex
	mode                          string
	image                         image.Image
	sources                       []string
	index, generation             int
	lastDraw, encodedKey, encoded string
	load                          func(int)
}

func newMedia(mode string) *mediaPane {
	m := &mediaPane{Flex: tview.NewFlex().SetDirection(tview.FlexRow), mode: mode,
		picture: tview.NewImage(), blank: tview.NewBox(), caption: tview.NewTextView()}
	m.picture.SetBorder(true).SetTitle("Images")
	m.blank.SetBorder(true).SetTitle("Images")
	if mode == "halfcell" {
		m.AddItem(m.picture, 0, 1, false)
	} else {
		m.AddItem(m.blank, 0, 1, false)
	}
	m.AddItem(m.caption, 2, 0, false)
	buttons := toolbar(action{"Previous", func() {
		if m.load != nil && m.index > 0 {
			m.load(m.index - 1)
		}
	}},
		action{"Next", func() {
			if m.load != nil && m.index+1 < len(m.sources) {
				m.load(m.index + 1)
			}
		}})
	m.buttons = buttons
	m.AddItem(buttons, 1, 0, false)
	return m
}

func (m *mediaPane) setImage(img image.Image) {
	m.image = img
	m.generation++
	m.picture.SetImage(img)
	if img == nil {
		m.caption.SetText("Loading image…")
	} else {
		m.caption.SetText(fmt.Sprintf("Image %d/%d · %s", m.index+1, len(m.sources), m.mode))
	}
}

func (m *mediaPane) afterDraw(screen tcell.Screen, visible, tmux bool, output io.Writer) {
	if m.mode != "iterm2" && m.mode != "sixel" {
		return
	}
	x, y, w, h := m.blank.GetInnerRect()
	cellW, cellH := 8, 16
	if tty, ok := screen.Tty(); ok {
		if size, err := tty.WindowSize(); err == nil {
			cw, ch := size.CellDimensions()
			if cw > 0 && ch > 0 {
				cellW, cellH = cw, ch
			}
		}
		output = tty
	}
	key := ""
	if visible && m.image != nil && w > 0 && h > 0 {
		key = fmt.Sprintf("%d/%d/%d/%d/%d/%d/%d", m.generation, x, y, w, h, cellW, cellH)
	}
	if key == m.lastDraw {
		return
	}
	screen.Sync() // Erase a previous image, including when opening a modal or changing tabs.
	m.lastDraw = key
	if key == "" {
		return
	}
	encodeKey := fmt.Sprintf("%d/%d/%d/%d/%d", m.generation, w, h, cellW, cellH)
	if encodeKey != m.encodedKey {
		var err error
		m.encoded, err = encodeNative(m.image, m.mode, w, h, cellW, cellH)
		if err != nil {
			m.caption.SetText(err.Error())
			return
		}
		m.encodedKey = encodeKey
	}
	sequence := m.encoded
	if tmux {
		sequence = tmuxEscape(sequence)
	}
	// Cursor positioning is handled by tmux itself; only the image payload is
	// passed through to the outer terminal, preserving pane-relative coordinates.
	fmt.Fprintf(output, "\x1b7\x1b[%d;%dH%s\x1b8", y+1, x+1, sequence)
}
