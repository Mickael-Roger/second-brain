// Second Brain is a standalone remote terminal client.
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
)

func main() {
	if err := launch(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func launch(args []string) error {
	flags := flag.NewFlagSet("second-brain-tui", flag.ContinueOnError)
	username := flags.String("username", "", "Prefill the login username")
	images := flags.String("images", "auto", "Image mode: auto, iterm2, sixel, halfcell, off")
	debug := flags.Bool("debug", false, "Print HTTP and Anki diagnostics to stderr after exiting")
	flags.Usage = func() {
		fmt.Fprintln(flags.Output(), "Usage: second-brain-tui [options] URL\n\nStandalone Go terminal client for Second Brain.")
		flags.PrintDefaults()
	}
	// Accept options before or after the URL, like the previous client.
	var options, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--username" || arg == "-username" || arg == "--images" || arg == "-images" {
			if i+1 == len(args) {
				return fmt.Errorf("missing value for %s", arg)
			}
			options = append(options, arg, args[i+1])
			i++
		} else if len(arg) > 0 && arg[0] == '-' {
			options = append(options, arg)
		} else {
			positional = append(positional, arg)
		}
	}
	if err := flags.Parse(options); err != nil {
		if err == flag.ErrHelp {
			return nil
		}
		return err
	}
	if len(positional) != 1 {
		flags.Usage()
		return fmt.Errorf("provide exactly one server URL")
	}
	mode, err := imageMode(*images, os.Getenv)
	if err != nil {
		return err
	}
	client, err := newClient(positional[0])
	if err != nil {
		return err
	}
	if *debug {
		trace := &debugBuffer{}
		client.debug = log.New(trace, "", log.Ltime|log.Lmicroseconds)
		defer func() {
			fmt.Fprintln(os.Stderr, "\n--- Second Brain TUI debug ---")
			fmt.Fprint(os.Stderr, trace.String())
			fmt.Fprintln(os.Stderr, "--- End debug ---")
		}()
	}
	return newUI(client, *username, mode).run()
}
