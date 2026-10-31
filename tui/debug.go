package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"sync"
)

// Keep a bounded trace in memory so logs do not interfere with the terminal UI.
type debugBuffer struct {
	mu    sync.Mutex
	lines []string
}

func (b *debugBuffer) Write(data []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.lines) >= 500 {
		copy(b.lines, b.lines[1:])
		b.lines = b.lines[:499]
	}
	b.lines = append(b.lines, string(data))
	return len(data), nil
}

func (b *debugBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	result := ""
	for _, line := range b.lines {
		result += line
	}
	return result
}

func ankiDebugSummary(data []byte) string {
	var payload any
	if err := json.Unmarshal(data, &payload); err != nil {
		return "non-JSON or truncated response"
	}
	// Only retain explicit statistics fields, never card content or error bodies.
	summarize := func(value any) any {
		item, ok := value.(map[string]any)
		if !ok {
			return fmt.Sprintf("unexpected item type %T", value)
		}
		keys := make([]string, 0, len(item))
		for key := range item {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		result := map[string]any{"keys": keys}
		for _, key := range []string{"enabled", "id", "deck_id", "name", "new_count", "learn_count", "review_count", "total_in_deck"} {
			if value, exists := item[key]; exists {
				result[key] = value
			}
		}
		return result
	}
	var summary any
	if items, ok := payload.([]any); ok {
		rows := []any{}
		for _, item := range items[:min(50, len(items))] {
			rows = append(rows, summarize(item))
		}
		summary = map[string]any{"count": len(items), "decks": rows}
	} else {
		summary = summarize(payload)
	}
	encoded, _ := json.Marshal(summary)
	return string(encoded)
}
