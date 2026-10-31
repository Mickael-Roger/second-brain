package main

import (
	"context"
	"fmt"
	"image"
	"net/url"
	"sort"
	"strconv"
	"strings"

	htmlmd "github.com/JohannesKaufmann/html-to-markdown/v2"
	"github.com/rivo/tview"
)

type newsState struct {
	doc        *document
	content    string
	unreadOnly bool
	filter     url.Values
	articles   []article
	feeds      []feed
	logos      map[string]*tview.Image
	article    article
	request    int
	busy       bool
	selected   map[string]article
}

func (u *ui) buildNews() page {
	n := &u.news
	n.doc = newDocument(u.openLink)
	return page{"News", n.doc, []tview.Primitive{n.doc.text, n.doc.links}, n.doc, u.refreshNews}
}

func (u *ui) newsParams() url.Values {
	params := url.Values{"period": {"30d"}, "unread_only": {strconv.FormatBool(u.news.unreadOnly)}}
	for key, values := range u.news.filter {
		params[key] = append([]string(nil), values...)
	}
	return params
}

func (u *ui) toggleNewsUnread() {
	if u.news.busy {
		return
	}
	u.news.unreadOnly = !u.news.unreadOnly
	u.fillArticles()
	u.updateChrome()
	if u.authenticated {
		u.refreshNews()
	}
}

func (u *ui) openNewsOriginal() {
	if u.news.article.URL != "" && u.browsers[3].detail {
		u.openBrowser(u.news.article.URL)
	}
}

func (u *ui) updateArticleTitle() {
	n := &u.news
	star, readState := "[gray]☆[-]", "[aqua::b]UNREAD[-:-:-]"
	if n.article.Starred {
		star = "[yellow]★[-]"
	}
	if n.article.Read {
		readState = "[gray]READ[-]"
	}
	title := star + " " + readState + " " + tview.Escape(n.article.Title)
	n.doc.text.SetTitle(title)
	n.doc.text.SetText(title + "\n\n" + n.content)
}

func (u *ui) refreshNews() {
	if u.news.busy {
		return
	}
	params := u.newsParams()
	u.news.request++
	request := u.news.request
	// Copy the cache before starting background work; the UI owns its map.
	logos := make(map[string]*tview.Image, len(u.news.logos))
	for source, logo := range u.news.logos {
		logos[source] = logo
	}
	u.job("Loading news", func(ctx context.Context) (func(), error) {
		var feeds []feed
		var articles []article
		for _, r := range []struct {
			route string
			out   any
		}{
			{query("api/news/feeds", url.Values{"period": params["period"]}), &feeds},
			{query("api/news/articles", params), &articles},
		} {
			if err := u.client.request(ctx, "GET", r.route, nil, r.out); err != nil {
				return nil, err
			}
		}
		decoded := map[string]image.Image{}
		loadLogo := func(source string) {
			if source == "" {
				return
			}
			if _, cached := logos[source]; cached {
				return
			}
			if _, loaded := decoded[source]; loaded {
				return
			}
			decoded[source] = nil
			data, err := u.client.image(ctx, source)
			if err == nil {
				decoded[source], _ = decodeImage(data)
			}
		}
		for _, f := range feeds {
			loadLogo(f.Favicon)
		}
		for _, a := range articles {
			loadLogo(a.FeedFavicon)
		}
		return func() {
			if request != u.news.request {
				return
			}
			n := &u.news
			for source, img := range decoded {
				logos[source] = nil
				if img != nil {
					logos[source] = tview.NewImage().SetImage(img).SetSize(1, 2).SetColors(tview.TrueColor).SetDithering(tview.DitheringNone)
				}
			}
			n.logos = logos
			n.feeds, n.articles = feeds, articles
			u.fillArticles()
		}, nil
	})
}

func (u *ui) fillArticles() {
	n := &u.news
	groups := []*resource{}
	categories, feeds := map[string]*resource{}, map[string]*resource{}
	feedByID := map[string]feed{}
	for _, f := range n.feeds {
		feedByID[f.ID] = f
	}
	for _, a := range n.articles {
		if _, selected := n.selected[a.ID]; selected {
			n.selected[a.ID] = a
		}
		if n.unreadOnly && a.Read {
			continue
		}
		if f, ok := feedByID[a.FeedID]; ok {
			if a.FeedGroup == "" {
				a.FeedGroup = f.Group
			}
			if a.FeedTitle == "" {
				a.FeedTitle = f.Title
			}
			if a.FeedFavicon == "" {
				a.FeedFavicon = f.Favicon
			}
		}
		category := a.FeedGroup
		if category == "" {
			category = "Uncategorized"
		}
		group := categories[category]
		if group == nil {
			group = &resource{id: "category:" + category, title: category, style: "yellow", children: []*resource{}}
			categories[category] = group
			groups = append(groups, group)
		}
		key := category + "/" + a.FeedID + "/" + a.FeedTitle
		feed := feeds[key]
		if feed == nil {
			title := a.FeedTitle
			if title == "" {
				title = "Articles"
			}
			feed = &resource{id: "feed:" + key, title: title, style: "fuchsia::b", icon: n.logos[a.FeedFavicon], children: []*resource{}}
			feeds[key] = feed
			group.children = append(group.children, feed)
		}
		title := a.Title
		if a.Starred {
			title = "★ " + title
		}
		style := "#6dd7ff::b"
		if a.Read {
			style = "#8393a7::-"
		}
		_, marked := n.selected[a.ID]
		feed.children = append(feed.children, &resource{id: a.ID, title: title, columns: []string{a.FeedTitle, dateOnly(a.PublishedAt)}, columnStyles: []string{"fuchsia::b", "gray::-"}, columnIcons: []*tview.Image{n.logos[a.FeedFavicon]}, unread: !a.Read, marked: marked, style: style, open: func() { u.openArticle(a.ID, true) }})
	}
	for _, group := range groups {
		sort.SliceStable(group.children, func(i, j int) bool {
			return strings.ToLower(group.children[i].title) < strings.ToLower(group.children[j].title)
		})
	}
	b := u.browsers[3]
	b.markedCount = len(n.selected)
	b.mode = "all"
	if n.unreadOnly {
		b.mode = "unread"
	}
	b.set(groups)
}

func (u *ui) toggleNewsRead() {
	u.newsAction("read", "")
}

func (u *ui) openArticle(id string, markRead bool) {
	if u.news.busy {
		return
	}
	u.news.request++
	request := u.news.request
	u.job("Opening article", func(ctx context.Context) (func(), error) {
		var result article
		route := "api/news/articles/" + url.PathEscape(id)
		if err := u.client.request(ctx, "GET", route, nil, &result); err != nil {
			return nil, err
		}
		if markRead && !result.Read {
			if err := u.client.request(ctx, "POST", route+"/read", nil, nil); err != nil {
				return nil, err
			}
			result.Read = true
		}
		// Summary is flattened by the server during ingestion. Keep the original
		// HTML structure for conversion to styled terminal content.
		body := result.RawHTML
		if strings.TrimSpace(body) == "" {
			body = result.Summary
		}
		if strings.Contains(body, "<") && strings.Contains(body, ">") {
			converted, err := htmlmd.ConvertString(body)
			if err != nil {
				return nil, err
			}
			body = converted
		}
		content := fmt.Sprintf("%s · %s\n\nLabels: %s\n\n", result.FeedTitle, dateOnly(result.PublishedAt), strings.Join(result.Labels, ", "))
		if result.ImageURL != "" {
			content += "![Article image](" + result.ImageURL + ")\n\n"
		}
		base := result.URL
		if base == "" {
			base = u.client.base.String()
		}
		return func() {
			if request != u.news.request {
				return
			}
			for index := range u.news.articles {
				if u.news.articles[index].ID == id {
					previous := u.news.articles[index]
					if result.FeedID == "" {
						result.FeedID = previous.FeedID
					}
					if result.FeedTitle == "" {
						result.FeedTitle = previous.FeedTitle
					}
					if result.FeedGroup == "" {
						result.FeedGroup = previous.FeedGroup
					}
					u.news.articles[index] = result
				}
			}
			u.news.article = result
			u.fillArticles()
			u.showDocument(u.news.doc, result.Title, content+body, "", base, nil)
			u.news.content = u.news.doc.text.GetText(false)
			u.updateArticleTitle()
			u.reveal(3)
		}, nil
	})
}

func (u *ui) fetchNews() {
	u.job("Fetching RSS", func(ctx context.Context) (func(), error) {
		if err := u.client.request(ctx, "POST", "api/news/fetch", nil, nil); err != nil {
			return nil, err
		}
		return func() { u.say("RSS fetch started. Refresh in a few moments.") }, nil
	})
}

func (u *ui) articleActions() {
	targets := u.newsTargets()
	if len(targets) == 0 || u.news.busy {
		u.say("Select an article first.")
		return
	}
	choices := []string{"Keep digest", "Save full article", "Mark watched", "Add label", "Remove label", "Custom instruction", "Mark read", "Mark unread", "Star", "Unstar"}
	actions := []string{"keep", "article", "watched", "add-label", "remove-label", "custom", "mark-read", "mark-unread", "mark-star", "unstar"}
	dropdown := tview.NewDropDown().SetLabel("Action: ").SetOptions(choices, nil).SetCurrentOption(0)
	value := input("Label/instruction: ")
	form := tview.NewForm().AddFormItem(dropdown).AddFormItem(value)
	form.AddButton("Apply", func() {
		index, _ := dropdown.GetCurrentOption()
		text := value.GetText()
		u.closeDialog()
		u.applyNewsAction(actions[index], text, targets)
	}).AddButton("Cancel", u.closeDialog)
	u.dialog(form, fmt.Sprintf("Article actions — %d selected", len(targets)), 85, 12)
}

func (u *ui) newsAction(action, value string) {
	u.applyNewsAction(action, value, u.newsTargets())
}

func (u *ui) manageFeeds() {
	u.job("Loading feeds", func(ctx context.Context) (func(), error) {
		var feeds []feed
		var categories []string
		if err := u.client.request(ctx, "GET", "api/news/feeds", nil, &feeds); err != nil {
			return nil, err
		}
		if err := u.client.request(ctx, "GET", "api/news/categories", nil, &categories); err != nil {
			return nil, err
		}
		return func() { u.feedDialog(feeds, categories) }, nil
	})
}

func (u *ui) feedDialog(feeds []feed, categories []string) {
	urlField, title, category := input("New feed URL: "), input("Title: "), input("Category: ")
	feedOptions := []string{"Choose existing feed"}
	for _, f := range feeds {
		feedOptions = append(feedOptions, f.Title)
	}
	selected := -1
	feedSelect := tview.NewDropDown().SetLabel("Feed: ").SetOptions(feedOptions, func(_ string, index int) {
		selected = index - 1
		if selected >= 0 {
			title.SetText(feeds[selected].Title)
			category.SetText(feeds[selected].Group)
		}
	}).SetCurrentOption(0)
	operations := []string{"Subscribe", "Update feed", "Unsubscribe", "Rename category", "Delete category"}
	op := tview.NewDropDown().SetLabel("Operation: ").SetOptions(operations, nil).SetCurrentOption(0)
	categoryOptions := append([]string{"Choose category"}, categories...)
	categorySelect := tview.NewDropDown().SetLabel("Existing category: ").SetOptions(categoryOptions, nil).SetCurrentOption(0)
	form := tview.NewForm().AddFormItem(op).AddFormItem(feedSelect).AddFormItem(urlField).
		AddFormItem(title).AddFormItem(category).AddFormItem(categorySelect)
	form.AddButton("Apply", func() {
		index, _ := op.GetCurrentOption()
		method, route := "POST", "api/news/feeds"
		var payload any
		group := strings.TrimSpace(category.GetText())
		switch index {
		case 0:
			if strings.TrimSpace(urlField.GetText()) == "" {
				u.say("Enter a feed URL.")
				return
			}
			payload = object{"url": urlField.GetText()}
			if title.GetText() != "" {
				payload.(object)["title"] = title.GetText()
			}
			if group != "" {
				payload.(object)["category"] = group
			}
		case 1, 2:
			if selected < 0 {
				u.say("Choose a feed.")
				return
			}
			route += "/" + url.PathEscape(feeds[selected].ID)
			if index == 2 {
				method = "DELETE"
			} else {
				method = "PATCH"
				update := object{"title": title.GetText(), "category": nil, "detach": group == ""}
				if group != "" {
					update["category"] = group
				}
				payload = update
			}
		case 3, 4:
			selection, _ := categorySelect.GetCurrentOption()
			if selection < 1 {
				u.say("Choose an existing category.")
				return
			}
			route = "api/news/categories/" + url.PathEscape(categories[selection-1])
			if index == 4 {
				method = "DELETE"
			} else {
				if group == "" {
					u.say("Enter the new name in Category.")
					return
				}
				method = "PATCH"
				payload = object{"name": group}
			}
		}
		u.closeDialog()
		u.job("Updating feeds", func(ctx context.Context) (func(), error) {
			if err := u.client.request(ctx, method, route, payload, nil); err != nil {
				return nil, err
			}
			return u.refreshNews, nil
		})
	}).AddButton("Cancel", u.closeDialog)
	u.dialog(form, "Manage feeds and categories", 85, 25)
}
