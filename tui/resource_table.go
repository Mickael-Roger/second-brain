package main

import (
	"github.com/gdamore/tcell/v2"
	"github.com/rivo/tview"
)

// Keep resource indices independent of the fixed column-heading row.
type resourceTable struct {
	*tview.Table
	icons []tableIcon
}

type tableIcon struct {
	row, column, inset int
	image              *tview.Image
}

func newResourceTable() *resourceTable {
	t := &resourceTable{Table: tview.NewTable().SetFixed(1, 0).SetSelectable(true, false).SetSeparator(' ')}
	t.SetBorder(true).SetBorderColor(tcell.GetColor("#80c7c7"))
	t.SetSelectedStyle(tcell.StyleDefault.Foreground(tcell.ColorWhite).Background(tcell.GetColor("#005f87")))
	return t
}

func (t *resourceTable) GetCurrentItem() int      { row, _ := t.GetSelection(); return row - 1 }
func (t *resourceTable) GetItemCount() int        { return max(0, t.GetRowCount()-1) }
func (t *resourceTable) SetCurrentItem(index int) { t.Select(index+1, 0) }

func (t *resourceTable) Draw(screen tcell.Screen) {
	_, _, width, _ := t.GetInnerRect()
	columns := t.GetColumnCount()
	limits := make([]int, columns)
	if columns > 0 {
		limits[0] = max(12, width-columns)
	}
	if columns > 1 {
		limits[1] = max(10, min(32, width/5))
	}
	if columns > 2 {
		limits[columns-1] = 9
	}
	if columns > 3 {
		limits[2] = 16
		if width < 100 {
			limits[2] = 10
		}
	}
	for col := 1; col < columns; col++ {
		limits[0] -= limits[col]
	}
	limits[0] = max(12, limits[0])
	for row := 0; row < t.GetRowCount(); row++ {
		for col, limit := range limits {
			t.GetCell(row, col).SetMaxWidth(limit)
		}
	}
	t.Table.Draw(screen)
	// Draw small graphical logos in the space reserved before each feed name.
	// Skip scrolled-out cells: GetLastPosition is undefined for invisible cells.
	rowOffset, columnOffset := t.GetOffset()
	innerX, innerY, innerWidth, innerHeight := t.GetInnerRect()
	for _, icon := range t.icons {
		if icon.row < rowOffset+1 || icon.row-rowOffset >= innerHeight || icon.column < columnOffset {
			continue
		}
		x, y, cellWidth := t.GetCell(icon.row, icon.column).GetLastPosition()
		x += icon.inset
		if cellWidth < icon.inset+2 || x < innerX || x+2 > innerX+innerWidth || y <= innerY || y >= innerY+innerHeight {
			continue
		}
		icon.image.SetRect(x, y, 2, 1)
		icon.image.Draw(screen)
	}
}
