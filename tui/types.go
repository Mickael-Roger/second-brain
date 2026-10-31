package main

type object = map[string]any

type ankiDeck struct {
	Name     string `json:"name"`
	New      int    `json:"new_count"`
	Learning int    `json:"learn_count"`
	Review   int    `json:"review_count"`
	Total    int    `json:"total_in_deck"`
}

type ankiCard struct {
	ID                 int64 `json:"card_id"`
	Front, Back, State string
	Question, Answer   string
	Interval           int
}

type chat struct {
	ID, Title, Model string
	Messages         []message
	UpdatedAt        string `json:"updated_at"`
}
type provider struct {
	Name   string
	Models []string
}
type block struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
	Mime string `json:"mime,omitempty"`
	Data string `json:"data,omitempty"`
	Name string `json:"name,omitempty"`
}
type message struct {
	Role    string  `json:"role"`
	Content []block `json:"content"`
}
type note struct {
	Path, Content string
	Backlinks     []struct{ Path, Snippet string }
}
type treeEntry struct {
	Path, Type string
	Depth      int
}
type article struct {
	ID, Title, URL, Author, Summary string
	FeedID                          string `json:"feed_id"`
	FeedTitle                       string `json:"feed_title"`
	FeedGroup                       string `json:"feed_group"`
	FeedFavicon                     string `json:"feed_favicon"`
	PublishedAt                     string `json:"published_at"`
	ImageURL                        string `json:"image_url"`
	RawHTML                         string `json:"raw_html"`
	Read                            bool   `json:"is_read"`
	Starred                         bool   `json:"is_starred"`
	Labels                          []string
}
type feed struct {
	ID      string `json:"feed_id"`
	Title   string `json:"feed_title"`
	Group   string `json:"feed_group"`
	Favicon string `json:"favicon"`
	Unread  int
}
type theme struct {
	Theme, Overview string
	IndexPath       string `json:"index_path"`
	Count           int    `json:"fiche_count"`
}
