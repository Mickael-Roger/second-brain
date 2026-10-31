package main

import "sort"

// Marks are keyed by article ID, so filtering and collapsing groups do not
// silently change the set on which a batch action operates.
func (u *ui) toggleNewsSelection() {
	n, b := &u.news, u.browsers[3]
	index := b.list.GetCurrentItem()
	if n.busy || b.detail || index < 0 || index >= len(b.rows) {
		return
	}
	byID := make(map[string]article, len(n.articles))
	for _, a := range n.articles {
		byID[a.ID] = a
	}
	var targets []article
	var collect func(*resource)
	collect = func(item *resource) {
		if item.children == nil {
			if a, ok := byID[item.id]; ok {
				targets = append(targets, a)
			}
			return
		}
		for _, child := range item.children {
			collect(child)
		}
	}
	collect(b.rows[index].item)
	allMarked := len(targets) > 0
	for _, a := range targets {
		if _, marked := n.selected[a.ID]; !marked {
			allMarked = false
		}
	}
	if n.selected == nil {
		n.selected = map[string]article{}
	}
	for _, a := range targets {
		if allMarked {
			delete(n.selected, a.ID)
		} else {
			n.selected[a.ID] = a
		}
	}
	u.fillArticles()
}

func (u *ui) clearNewsSelection() {
	if u.news.busy {
		return
	}
	u.news.selected = nil
	u.fillArticles()
}

// An open article has its own actions. On the list, marks take precedence over
// the cursor; an unmarked group never targets the previously opened article.
func (u *ui) newsTargets() []article {
	n, b := &u.news, u.browsers[3]
	if b.detail {
		if n.article.ID != "" {
			return []article{n.article}
		}
		return nil
	}
	if len(n.selected) > 0 {
		targets := make([]article, 0, len(n.selected))
		for _, a := range n.selected {
			targets = append(targets, a)
		}
		sort.Slice(targets, func(i, j int) bool { return targets[i].ID < targets[j].ID })
		return targets
	}
	index := b.list.GetCurrentItem()
	if index >= 0 && index < len(b.rows) && b.rows[index].item.children == nil {
		for _, a := range n.articles {
			if a.ID == b.rows[index].item.id {
				return []article{a}
			}
		}
	}
	return nil
}
