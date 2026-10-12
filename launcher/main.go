//go:build windows

// AC8 Asset Extractor.exe — starts AC8_Asset_Extractor.py with no console window.
//
// Build (from this folder):
//
//	go build -ldflags "-H windowsgui -s -w" -o "..\AC8 Asset Extractor.exe" .
//
// The icon and version info come from rsrc_windows_amd64.syso, which go build picks up on its own.
package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const (
	appName        = "ACE Combat 8 Asset Extractor"
	scriptName     = "AC8_Asset_Extractor.py"
	createNoWindow = 0x08000000
	mbIconError    = 0x10
	mbIconWarning  = 0x30
)

func messageBox(text string, flags uintptr) {
	user32 := syscall.NewLazyDLL("user32.dll")
	proc := user32.NewProc("MessageBoxW")
	t, _ := syscall.UTF16PtrFromString(text)
	c, _ := syscall.UTF16PtrFromString(appName)
	proc.Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(c)), flags)
}

func exists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

// isStoreStub reports whether p is the Microsoft Store "python.exe" alias that only opens the Store.
func isStoreStub(p string) bool {
	return strings.Contains(strings.ToLower(p), `\microsoft\windowsapps\`)
}

// findPython returns the interpreter and any leading args (e.g. "py -3").
func findPython(appDir string) (string, []string) {
	// 1. explicit override
	if p := os.Getenv("AC8_PYTHON"); p != "" && exists(p) {
		return p, nil
	}
	// 2. a python folder shipped next to the app (portable setups)
	for _, rel := range []string{`python\python.exe`, `.venv\Scripts\python.exe`, `venv\Scripts\python.exe`} {
		if p := filepath.Join(appDir, rel); exists(p) {
			return p, nil
		}
	}
	// 3. python.exe on PATH (but not the Store alias)
	if p, err := exec.LookPath("python.exe"); err == nil && !isStoreStub(p) {
		return p, nil
	}
	// 4. the py launcher that comes with python.org installers
	if p, err := exec.LookPath("py.exe"); err == nil {
		return p, []string{"-3"}
	}
	// 5. usual install folders, newest version first
	var found []string
	for _, base := range []string{
		filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "Python"),
		os.Getenv("ProgramFiles"),
		`C:\`,
	} {
		if base == "" {
			continue
		}
		matches, _ := filepath.Glob(filepath.Join(base, "Python3*", "python.exe"))
		found = append(found, matches...)
	}
	if len(found) > 0 {
		sort.Sort(sort.Reverse(sort.StringSlice(found)))
		return found[0], nil
	}
	return "", nil
}

// tail keeps the last n bytes of what was written, so a crash message stays readable.
type tail struct {
	buf bytes.Buffer
	n   int
}

func (t *tail) Write(p []byte) (int, error) {
	t.buf.Write(p)
	if t.buf.Len() > t.n*2 {
		b := t.buf.Bytes()
		keep := append([]byte(nil), b[len(b)-t.n:]...)
		t.buf.Reset()
		t.buf.Write(keep)
	}
	return len(p), nil
}

func (t *tail) String() string {
	s := t.buf.String()
	if len(s) > t.n {
		s = "…" + s[len(s)-t.n:]
	}
	return strings.TrimSpace(s)
}

func main() {
	exe, err := os.Executable()
	if err != nil {
		messageBox("Could not work out where this program is: "+err.Error(), mbIconError)
		return
	}
	appDir := filepath.Dir(exe)
	script := filepath.Join(appDir, scriptName)
	if !exists(script) {
		messageBox(fmt.Sprintf("%s was not found next to this program.\n\nKeep the .exe in the AC8 Asset Extractor folder "+
			"(the one with %s, ac8_textures.py and the AC8 Unpacker folder).", scriptName, scriptName), mbIconError)
		return
	}

	python, pre := findPython(appDir)
	if python == "" {
		messageBox("Python 3 was not found.\n\nInstall Python 3.10 or newer from python.org and tick \"Add python.exe to PATH\", "+
			"then start this again.\n\n(Or set the AC8_PYTHON environment variable to your python.exe.)", mbIconWarning)
		return
	}

	args := append(append(pre, "-u", script), os.Args[1:]...)
	cmd := exec.Command(python, args...)
	cmd.Dir = appDir
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
	errs := &tail{n: 1800}
	cmd.Stdout = errs
	cmd.Stderr = errs

	started := time.Now()
	if err := cmd.Start(); err != nil {
		messageBox(fmt.Sprintf("Could not start Python:\n%s\n\n%v", python, err), mbIconError)
		return
	}
	if err := cmd.Wait(); err != nil {
		msg := errs.String()
		if msg == "" {
			msg = err.Error()
		}
		hint := ""
		if strings.Contains(msg, "No module named 'tkinter'") || strings.Contains(msg, "No module named '_tkinter'") {
			hint = "\n\nYour Python was installed without Tcl/Tk. Re-run the Python installer, choose Modify and tick \"tcl/tk and IDLE\"."
		} else if strings.Contains(msg, "No module named") {
			hint = "\n\nA Python package is missing. Open the app's Settings and click Install packages, or run:\n" +
				python + " -m pip install Pillow numpy pycryptodome"
		}
		if logs := filepath.Join(appDir, "logs"); os.MkdirAll(logs, 0o755) == nil {
			_ = os.WriteFile(filepath.Join(logs, "crash_"+time.Now().Format("2006-01-02_15-04-05")+".txt"),
				[]byte(python+" "+strings.Join(args, " ")+"\r\n\r\n"+msg), 0o644)
		}
		when := "while running"
		if time.Since(started) < 10*time.Second {
			when = "on start"
		}
		messageBox(fmt.Sprintf("The app closed with an error %s:\n\n%s%s", when, msg, hint), mbIconError)
	}
}
