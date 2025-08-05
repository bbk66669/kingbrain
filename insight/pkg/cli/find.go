package cli

import (
    "fmt"
    "github.com/spf13/cobra"
    "kingbrain/insight/pkg/sg"
)

func newFindCmd() *cobra.Command {
    var pattern string

    cmd := &cobra.Command{
        Use:   "find [-p pattern] <keyword>",
        Short: "在 Sourcegraph 上做搜索：文本、正则或结构化",
        Args:  cobra.MinimumNArgs(1),
        RunE: func(_ *cobra.Command, args []string) error {
            // 第一个位置参数就是 keyword
            keyword := args[0]

            // 构造 GraphQL 查询，动态注入 patternType（枚举无需引号）
            query := fmt.Sprintf(`
query ($q: String!) {
  search(version: V3, query: $q, patternType: %s) {
    results {
      matchCount
      results {
        ... on FileMatch {
          file { path }
          lineMatches { preview lineNumber }
        }
      }
    }
  }
}
`, pattern)

            // 发送请求并解析任意返回结构
            var out map[string]any
            if err := sg.New().GraphQL(query, map[string]any{"q": keyword}, &out); err != nil {
                return err
            }

            // 挖出 data.search.results
            data := out["data"].(map[string]any)
            search := data["search"].(map[string]any)
            results := search["results"].(map[string]any)

            // 打印总命中数
            fmt.Printf("Total matches: %v\n\n", results["matchCount"])

            // 逐条列出文件路径和行预览
            for _, item := range results["results"].([]any) {
                fm := item.(map[string]any)
                file := fm["file"].(map[string]any)
                fmt.Printf("File: %s\n", file["path"])
                for _, lm := range fm["lineMatches"].([]any) {
                    m := lm.(map[string]any)
                    fmt.Printf("  %5v | %s\n", m["lineNumber"], m["preview"])
                }
                fmt.Println()
            }
            return nil
        },
    }

    // 可选的模式标志：literal|regexp|structural
    cmd.Flags().StringVarP(&pattern, "pattern", "p", "literal",
        "搜索模式：literal（文本）|regexp（正则）|structural（结构化）")
    return cmd
}
