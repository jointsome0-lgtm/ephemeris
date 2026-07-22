// Vera Example invented runner fixture.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"time"
)

func main() {
	if os.Getenv("EPHEMERIS_WARM_CHILD") == "1" {
		fmt.Println("go-warm-child-ok")
		return
	}
	started := time.Now()
	_, source, _, ok := runtime.Caller(0)
	if !ok {
		fmt.Println("go-source-path-unavailable")
		os.Exit(1)
	}
	cmd := exec.Command("go", "run", source)
	cmd.Env = append(os.Environ(), "EPHEMERIS_WARM_CHILD=1")
	output, err := cmd.CombinedOutput()
	if err != nil {
		fmt.Printf("go-warm-child-error=%v output=%s\n", err, output)
		os.Exit(1)
	}
	fmt.Printf("go-cold-parent-ok warm_ms=%d child=%s", time.Since(started).Milliseconds(), output)
}
