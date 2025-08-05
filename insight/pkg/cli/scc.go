package cli
import ("os/exec";"log";"github.com/spf13/cobra")
func newSccCmd() *cobra.Command {
    cmd := &cobra.Command{Use: "scc",
        RunE: func(_ *cobra.Command, args []string) error {
            target := "."
            if len(args) > 0 { target = args[0] }
            out, err := exec.Command("scc", "--ci", target).CombinedOutput()
            if err != nil { return err }
            log.Print("\n" + string(out)); return nil
        }}
    return cmd
}
func init() { rootCmd.AddCommand(newSccCmd()) }
