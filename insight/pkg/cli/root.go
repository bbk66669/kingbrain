package cli
import "github.com/spf13/cobra"
func Execute() { _ = rootCmd.Execute() }
var rootCmd = &cobra.Command{Use: "kb"}
func init() { rootCmd.AddCommand(newFindCmd()) }
