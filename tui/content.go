package main

import (
	"fmt"
	"net/url"
	"path"
	"regexp"
	"strings"

	"github.com/rivo/tview"
	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/text"
)

type link struct{ Label, Target string }
type document struct {
	*tview.Flex
	text     *tview.TextView
	links    *tview.List
	images   []string
	notePath string
}

func newDocument(open func(string, string)) *document {
	d := &document{Flex: tview.NewFlex().SetDirection(tview.FlexRow),
		text:  tview.NewTextView().SetDynamicColors(true).SetWordWrap(true),
		links: tview.NewList().ShowSecondaryText(false)}
	d.text.SetBorder(true)
	d.links.SetBorder(true).SetTitle("Links / backlinks (Enter to open)")
	d.AddItem(d.text, 0, 1, true)
	d.links.SetSelectedFunc(func(index int, title, secondary string, shortcut rune) {
		open(secondary, d.notePath)
	})
	return d
}

var wikiLinks = regexp.MustCompile(`(!?)\[\[([^\]|]+)(?:\|([^\]]+))?\]\]`)

func prepareMarkdown(source string) string {
	return wikiLinks.ReplaceAllStringFunc(source, func(value string) string {
		m := wikiLinks.FindStringSubmatch(value)
		label := m[3]
		if label == "" {
			label = m[2]
		}
		if m[1] == "!" && !strings.HasSuffix(strings.ToLower(m[2]), ".md") {
			return "![" + label + "](vault-image:" + url.PathEscape(m[2]) + ")"
		}
		return "[" + label + "](note:" + url.PathEscape(m[2]) + ")"
	})
}

func assetURL(source, notePath, assetBase string) string {
	if assetBase != "" {
		base, err := url.Parse(assetBase)
		target, targetErr := url.Parse(source)
		if err == nil && targetErr == nil {
			return base.ResolveReference(target).String()
		}
	}
	if strings.HasPrefix(source, "vault-image:") {
		source = strings.TrimPrefix(source, "vault-image:")
		if decoded, err := url.PathUnescape(source); err == nil {
			source = decoded
		}
	} else {
		parsed, err := url.Parse(source)
		if err == nil && (parsed.IsAbs() || strings.HasPrefix(source, "//")) {
			return source
		}
		if strings.HasPrefix(source, "/api/") {
			return strings.TrimPrefix(source, "/")
		}
		if decoded, err := url.PathUnescape(source); err == nil {
			source = decoded
		}
		source = path.Join(path.Dir(notePath), source)
	}
	return query("api/vault/file", url.Values{"path": {source}})
}

// Render the Markdown AST into tview styling; retain structured links and images.
func renderMarkdown(source, notePath, assetBase string) (string, []link, []string) {
	input := []byte(prepareMarkdown(source))
	root := goldmark.DefaultParser().Parse(text.NewReader(input))
	var out strings.Builder
	var links []link
	var images []string
	escape := func(text string) { out.WriteString(tview.Escape(text)) }
	_ = ast.Walk(root, func(node ast.Node, entering bool) (ast.WalkStatus, error) {
		switch n := node.(type) {
		case *ast.Image:
			if entering {
				images = append(images, assetURL(string(n.Destination), notePath, assetBase))
				fmt.Fprintf(&out, "\n[gray]Image %d — see media pane[-]\n", len(images))
				return ast.WalkSkipChildren, nil
			}
		case *ast.Link:
			if entering {
				target := string(n.Destination)
				if assetBase != "" {
					target = assetURL(target, "", assetBase)
				}
				links = append(links, link{string(n.Text(input)), target})
				out.WriteString("[deepskyblue]")
			} else {
				out.WriteString("[-]")
			}
		case *ast.AutoLink:
			if entering {
				target := string(n.URL(input))
				links = append(links, link{target, target})
				escape(target)
			}
		case *ast.Text:
			if entering {
				escape(string(n.Segment.Value(input)))
				if n.HardLineBreak() || n.SoftLineBreak() {
					out.WriteByte('\n')
				}
			}
		case *ast.String:
			if entering {
				escape(string(n.Value))
			}
		case *ast.Heading:
			if entering {
				out.WriteString("\n[::b]")
			} else {
				out.WriteString("[::-]\n\n")
			}
		case *ast.Emphasis:
			if entering {
				out.WriteString("[::b]")
			} else {
				out.WriteString("[::-]")
			}
		case *ast.ListItem:
			if entering {
				out.WriteString("  • ")
			} else {
				out.WriteByte('\n')
			}
		case *ast.Paragraph:
			if !entering {
				out.WriteString("\n\n")
			}
		case *ast.FencedCodeBlock, *ast.CodeBlock:
			if entering {
				out.WriteString("\n[gray]")
				for i := 0; i < n.Lines().Len(); i++ {
					line := n.Lines().At(i)
					escape(string(line.Value(input)))
				}
				out.WriteString("[-]\n")
				return ast.WalkSkipChildren, nil
			}
		case *ast.ThematicBreak:
			if entering {
				out.WriteString("\n────────────────────────\n")
			}
		}
		return ast.WalkContinue, nil
	})
	return out.String(), links, images
}

func (d *document) show(title, content, notePath, assetBase string, backlinks []link) {
	formatted, links, images := renderMarkdown(content, notePath, assetBase)
	d.notePath, d.images = notePath, images
	d.text.SetTitle(tview.Escape(title))
	d.text.SetText(formatted).ScrollToBeginning()
	d.links.Clear()
	d.RemoveItem(d.links)
	for _, l := range append(links, backlinks...) {
		d.links.AddItem(tview.Escape(l.Label), l.Target, 0, nil)
	}
	if d.links.GetItemCount() > 0 {
		d.AddItem(d.links, min(8, d.links.GetItemCount()+2), 0, false)
	}
}
