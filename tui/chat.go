package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"mime"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/rivo/tview"
)

type chatState struct {
	doc              *document
	composer         *tview.TextArea
	model            *tview.DropDown
	models           []string
	id, text, prompt string
	attachment       *block
	busy             bool
	cancel           context.CancelFunc
}

func (u *ui) buildChat() page {
	c := &u.chat
	c.doc = newDocument(u.openLink)
	c.composer = tview.NewTextArea().SetPlaceholder("Write a message — Ctrl+G to send").SetWordWrap(true)
	c.composer.SetBorder(true).SetTitle("Message")
	c.model = tview.NewDropDown().SetLabel("Model: ").SetOptions([]string{"Server default"}, nil).SetCurrentOption(0)
	conversationButtons := toolbar(action{"New", u.newChat}, action{"Rename", u.renameChat})
	archiveButtons := toolbar(action{"Archive", u.archiveChat})
	sendButtons := toolbar(action{"Send", u.sendChat}, action{"Stop", func() {
		if c.cancel != nil {
			c.cancel()
		}
	}}, action{"Attach image", u.attachImage})
	right := tview.NewFlex().SetDirection(tview.FlexRow).AddItem(c.model, 1, 0, false).
		AddItem(c.doc, 0, 1, true).AddItem(c.composer, 6, 0, false).
		AddItem(sendButtons, 1, 0, false).AddItem(conversationButtons, 1, 0, false).AddItem(archiveButtons, 1, 0, false)
	return page{"Chat", right, []tview.Primitive{c.doc.text, c.doc.links, c.composer, c.model, sendButtons, conversationButtons, archiveButtons}, c.doc, u.refreshChat}
}

func (u *ui) refreshChat() {
	u.job("Loading models", func(ctx context.Context) (func(), error) {
		var providers []provider
		if err := u.client.request(ctx, "GET", "api/llm/providers", nil, &providers); err != nil {
			return nil, err
		}
		options := []string{"Server default"}
		models := []string{""}
		for _, p := range providers {
			for _, m := range p.Models {
				models = append(models, p.Name+"/"+m)
				options = append(options, p.Name+" / "+m)
			}
		}
		return func() {
			selected, _ := u.chat.model.GetCurrentOption()
			if selected >= len(options) {
				selected = 0
			}
			u.chat.models = models
			u.chat.model.SetOptions(options, nil).SetCurrentOption(selected)
			u.refreshChats()
		}, nil
	})
}

func (u *ui) refreshChats() {
	u.job("Loading conversations", func(ctx context.Context) (func(), error) {
		var chats []chat
		if err := u.client.request(ctx, "GET", "api/chats", nil, &chats); err != nil {
			return nil, err
		}
		return func() {
			sort.SliceStable(chats, func(i, j int) bool {
				left := chatDate(chats[i].UpdatedAt)
				right := chatDate(chats[j].UpdatedAt)
				return left.After(right)
			})
			groups := []*resource{}
			years, months := map[string]*resource{}, map[string]*resource{}
			for _, item := range chats {
				date := chatDate(item.UpdatedAt)
				year, month, monthTitle := "Unknown date", "unknown", "Unknown month"
				if !date.IsZero() {
					year, month, monthTitle = date.Format("2006"), date.Format("2006-01"), date.Format("01 — January")
				}
				if years[year] == nil {
					years[year] = &resource{id: "year:" + year, title: year, style: "yellow", children: []*resource{}}
					groups = append(groups, years[year])
				}
				group := months[month]
				if group == nil {
					group = &resource{id: "month:" + month, title: monthTitle, children: []*resource{}}
					months[month] = group
					years[year].children = append(years[year].children, group)
				}
				group.children = append(group.children, &resource{id: item.ID, title: item.Title, columns: []string{item.Model, compactDate(item.UpdatedAt)}, open: func() { u.loadChat(item.ID) }})
			}
			u.browsers[0].set(groups)
		}, nil
	})
}

func chatDate(value string) time.Time {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05"} {
		if date, err := time.Parse(layout, value); err == nil {
			return date.UTC()
		}
	}
	return time.Time{}
}

func messageMarkdown(msg message) string {
	if msg.Role == "system" {
		return ""
	}
	var text strings.Builder
	fmt.Fprintf(&text, "\n## %s\n\n", msg.Role)
	for _, b := range msg.Content {
		switch b.Type {
		case "text":
			text.WriteString(b.Text + "\n\n")
		case "image":
			fmt.Fprintf(&text, "![Chat image](data:%s;base64,%s)\n\n", b.Mime, b.Data)
		case "tool_use":
			fmt.Fprintf(&text, "Tool: %s\n\n", b.Name)
		}
	}
	return text.String()
}

func (u *ui) loadChat(id string) {
	if u.chat.busy {
		u.say("Stop the current reply first.")
		return
	}
	u.job("Loading conversation", func(ctx context.Context) (func(), error) {
		var result chat
		if err := u.client.request(ctx, "GET", "api/chats/"+id, nil, &result); err != nil {
			return nil, err
		}
		return func() {
			if u.chat.busy {
				return
			}
			u.chat.id, u.chat.prompt = result.ID, ""
			u.chat.text = ""
			for _, message := range result.Messages {
				u.chat.text += messageMarkdown(message)
			}
			u.showDocument(u.chat.doc, result.Title, u.chat.text, "", "", nil)
			u.chat.doc.text.ScrollToEnd()
			u.reveal(0)
		}, nil
	})
}

func (u *ui) newChat() {
	if u.chat.busy {
		u.say("Stop the current reply first.")
		return
	}
	u.chat.id, u.chat.text, u.chat.prompt, u.chat.attachment = "", "", "", nil
	u.showDocument(u.chat.doc, "New conversation", "", "", "", nil)
	u.reveal(0)
	u.app.SetFocus(u.chat.composer)
}

func (u *ui) renameChat() {
	if u.chat.id == "" || u.chat.busy {
		return
	}
	id := u.chat.id
	u.prompt("Conversation title", "", func(title string) {
		u.job("Renaming conversation", func(ctx context.Context) (func(), error) {
			if err := u.client.request(ctx, "PATCH", "api/chats/"+id, object{"title": title}, nil); err != nil {
				return nil, err
			}
			return u.refreshChats, nil
		})
	})
}

func (u *ui) archiveChat() {
	if u.chat.id == "" || u.chat.busy {
		return
	}
	id := u.chat.id
	u.job("Archiving conversation", func(ctx context.Context) (func(), error) {
		if err := u.client.request(ctx, "PATCH", "api/chats/"+id, object{"archived": true}, nil); err != nil {
			return nil, err
		}
		return func() {
			if u.chat.id == id {
				u.newChat()
			}
			u.refreshChats()
		}, nil
	})
}

func (u *ui) attachImage() {
	u.prompt("Local image path", "", func(filename string) {
		u.job("Attaching image", func(context.Context) (func(), error) {
			if strings.HasPrefix(filename, "~/") {
				home, err := os.UserHomeDir()
				if err != nil {
					return nil, err
				}
				filename = filepath.Join(home, filename[2:])
			}
			info, err := os.Stat(filename)
			if err != nil {
				return nil, err
			}
			if info.Size() > 20<<20 {
				return nil, fmt.Errorf("image exceeds 20 MiB")
			}
			data, err := os.ReadFile(filename)
			if err != nil {
				return nil, err
			}
			if _, err := decodeImage(data); err != nil {
				return nil, err
			}
			mimeType := mime.TypeByExtension(strings.ToLower(filepath.Ext(filename)))
			if !strings.HasPrefix(mimeType, "image/") {
				return nil, fmt.Errorf("choose a PNG, JPEG, GIF or WebP file")
			}
			return func() {
				u.chat.attachment = &block{Type: "image", Mime: mimeType, Data: base64.StdEncoding.EncodeToString(data)}
				u.say("Attached: " + filename)
			}, nil
		})
	})
}

func (u *ui) sendChat() {
	c := &u.chat
	text := c.composer.GetText()
	if c.busy || (strings.TrimSpace(text) == "" && c.attachment == nil) {
		return
	}
	var blocks []block
	if strings.TrimSpace(text) != "" {
		blocks = append(blocks, block{Type: "text", Text: text})
	}
	if c.attachment != nil {
		blocks = append(blocks, *c.attachment)
	}
	payload := object{"content": blocks}
	if c.id != "" {
		payload["chat_id"] = c.id
	}
	if c.prompt != "" {
		payload["system_prompt_id"] = c.prompt
	}
	selected, _ := c.model.GetCurrentOption()
	if selected > 0 && selected < len(c.models) {
		p, m, _ := strings.Cut(c.models[selected], "/")
		payload["provider"], payload["model"] = p, m
	}
	c.text += messageMarkdown(message{Role: "user", Content: blocks})
	base := c.text
	c.attachment = nil
	c.composer.SetText("", false)
	c.busy = true
	ctx, cancel := context.WithCancel(u.session)
	c.cancel = cancel
	u.showDocument(c.doc, "Chat", c.text, "", "", nil)
	u.say("Thinking…")
	epoch := u.epoch
	go func() {
		defer cancel()
		live := ""
		err := u.client.stream(ctx, "api/chat", payload, func(ev event) error {
			var data struct {
				ID, Text, Name, Error string
				IsError               bool `json:"is_error"`
			}
			if err := json.Unmarshal(ev.Data, &data); err != nil {
				return err
			}
			var update func()
			switch ev.Name {
			case "chat":
				update = func() { c.id = data.ID }
			case "text_delta":
				live += data.Text
				transcript := base + "\n## assistant\n\n" + live
				update = func() {
					c.text = transcript
					rendered, _, _ := renderMarkdown(transcript, "", "")
					c.doc.text.SetText(rendered).ScrollToEnd()
				}
			case "message_done":
				var msg message
				if err := json.Unmarshal(ev.Data, &msg); err != nil {
					return err
				}
				base += messageMarkdown(msg)
				live = ""
				transcript := base
				update = func() {
					c.text = transcript
					u.showDocument(c.doc, "Chat", transcript, "", "", nil)
					c.doc.text.ScrollToEnd()
				}
			case "tool_use":
				update = func() { u.say("Tool: " + data.Name) }
			case "tool_result":
				update = func() {
					if data.IsError {
						u.say("Tool failed")
					} else {
						u.say("Thinking…")
					}
				}
			case "error":
				return errors.New(data.Error)
			}
			if update != nil && u.ctx.Err() == nil {
				u.app.QueueUpdateDraw(func() {
					if u.epoch == epoch {
						update()
					}
				})
			}
			return ctx.Err()
		})
		if u.ctx.Err() != nil {
			return
		}
		u.app.QueueUpdateDraw(func() {
			if u.epoch != epoch {
				return
			}
			c.busy = false
			c.cancel = nil
			if err != nil && !errors.Is(err, context.Canceled) {
				u.say(err.Error())
				var api *apiError
				if errors.As(err, &api) && api.Status == 401 {
					u.authenticated = false
					u.login()
				}
			} else {
				u.say("Reply complete")
				u.refreshChats()
			}
		})
	}()
}
