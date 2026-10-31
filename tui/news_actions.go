package main

import (
	"context"
	"fmt"
	"net/url"
	"strings"
)

func (u *ui) applyNewsAction(action, value string, targets []article) {
	if u.news.busy || len(targets) == 0 {
		return
	}
	if (action == "add-label" || action == "remove-label") && strings.TrimSpace(value) == "" {
		u.say("Enter a label.")
		return
	}
	if action == "custom" && strings.TrimSpace(value) == "" {
		u.say("Enter an instruction.")
		return
	}
	// A mixed selection is marked read/starred together, rather than inverting
	// each item independently and leaving the selection in another mixed state.
	switch action {
	case "read":
		action = "unread"
		for _, a := range targets {
			if !a.Read {
				action = "read"
				break
			}
		}
	case "star":
		action = "unstar"
		for _, a := range targets {
			if !a.Starred {
				action = "star"
				break
			}
		}
	case "mark-read":
		action = "read"
	case "mark-unread":
		action = "unread"
	case "mark-star":
		action = "star"
	}
	u.news.busy = true
	u.news.request++ // Ignore list/open responses that predate this mutation.
	fromList := !u.browsers[3].detail
	u.job(fmt.Sprintf("Updating %d articles", len(targets)), func(ctx context.Context) (func(), error) {
		type outcome struct {
			article       article
			path, summary string
			err           error
		}
		results := make([]outcome, 0, len(targets))
		for _, a := range targets {
			method, route := "POST", "api/news/articles/"+url.PathEscape(a.ID)
			var payload any
			switch action {
			case "add-label":
				route += "/labels"
				payload = object{"label": value}
			case "remove-label":
				method = "DELETE"
				route += "/labels/" + url.PathEscape(value)
			case "custom":
				route += "/custom"
				payload = object{"instruction": value}
			default:
				route += "/" + action
			}
			var response struct{ Path, Summary string }
			var out any
			if action == "keep" || action == "article" || action == "watched" || action == "custom" {
				out = &response
			}
			err := u.client.request(ctx, method, route, payload, out)
			results = append(results, outcome{a, response.Path, response.Summary, err})
		}
		return func() {
			u.news.busy = false
			succeeded := 0
			var firstError error
			reload := false
			update := func(a *article) {
				switch action {
				case "read", "unread":
					a.Read = action == "read"
				case "star", "unstar":
					a.Starred = action == "star"
				}
			}
			for _, result := range results {
				if result.err != nil {
					if firstError == nil {
						firstError = fmt.Errorf("%s: %w", result.article.Title, result.err)
					}
					continue
				}
				succeeded++
				for index := range u.news.articles {
					if u.news.articles[index].ID == result.article.ID {
						update(&u.news.articles[index])
					}
				}
				if u.news.article.ID == result.article.ID {
					update(&u.news.article)
					u.updateArticleTitle()
					if action != "read" && action != "unread" && action != "star" && action != "unstar" {
						reload = true
					}
				}
				if fromList {
					delete(u.news.selected, result.article.ID)
				} else if selected, ok := u.news.selected[result.article.ID]; ok {
					update(&selected)
					u.news.selected[result.article.ID] = selected
				}
			}
			u.fillArticles()
			if reload && u.browsers[3].detail {
				u.openArticle(u.news.article.ID, false)
			}
			if firstError != nil {
				u.say(fmt.Sprintf("%d/%d articles updated; %d failed — %s", succeeded, len(targets), len(targets)-succeeded, firstError))
			} else if len(results) == 1 && results[0].summary != "" {
				u.say(results[0].summary)
			} else if len(results) == 1 && results[0].path != "" {
				u.say("Saved: " + results[0].path)
			} else {
				u.say(fmt.Sprintf("%d articles updated (%s)", succeeded, action))
			}
		}, nil
	})
}
