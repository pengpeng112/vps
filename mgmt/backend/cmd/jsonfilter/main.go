// jsonfilter 兼容工具（OpenWrt 表达式子集），仅用于本地/alpine 测试环境。
// 真实路由器使用 opkg 的 jsonfilter；本工具保证 CI 与测试容器的行为一致。
//
// 用法：jsonfilter -s '<json>' -e '$.a.b[0].c'
//   支持 $.key、$["key"]、[N]、[*]；多结果每行一个（与 OpenWrt jsonfilter 一致）；
//   字符串输出不带引号；对象/数组输出紧凑 JSON。
package main

import (
	"encoding/json"
	"io"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type seg struct {
	key    string
	index  int
	any    bool // [*]
	isIdx  bool
}

func parseExpr(e string) ([]seg, error) {
	if !strings.HasPrefix(e, "$") {
		return nil, fmt.Errorf("表达式必须以 $ 开头")
	}
	var segs []seg
	i := 1
	for i < len(e) {
		switch e[i] {
		case '.':
			i++
			if i < len(e) && e[i] == '"' {
				j := strings.IndexByte(e[i+1:], '"')
				if j < 0 {
					return nil, fmt.Errorf("引号不闭合")
				}
				segs = append(segs, seg{key: e[i+1 : i+1+j]})
				i = i + 1 + j + 1
				continue
			}
			j := i
			for j < len(e) && e[j] != '.' && e[j] != '[' {
				j++
			}
			if j == i {
				return nil, fmt.Errorf("空键名")
			}
			segs = append(segs, seg{key: e[i:j]})
			i = j
		case '[':
			j := strings.IndexByte(e[i:], ']')
			if j < 0 {
				return nil, fmt.Errorf("方括号不闭合")
			}
			inner := e[i+1 : i+j]
			switch {
			case inner == "*":
				segs = append(segs, seg{any: true})
			default:
				n, err := strconv.Atoi(inner)
				if err != nil {
					return nil, fmt.Errorf("不支持下标 %q", inner)
				}
				segs = append(segs, seg{index: n, isIdx: true})
			}
			i += j + 1
		default:
			return nil, fmt.Errorf("不支持的语法位置 %d", i)
		}
	}
	return segs, nil
}

func walk(v any, segs []seg, out *[]string) {
	if len(segs) == 0 {
		*out = append(*out, render(v))
		return
	}
	s := segs[0]
	switch s.any {
	case true:
		if arr, ok := v.([]any); ok {
			for _, item := range arr {
				walk(item, segs[1:], out)
			}
		}
	default:
		if s.isIdx {
			if arr, ok := v.([]any); ok && s.index < len(arr) {
				walk(arr[s.index], segs[1:], out)
			}
		} else if m, ok := v.(map[string]any); ok {
			if item, ok := m[s.key]; ok {
				walk(item, segs[1:], out)
			}
		}
	}
}

func render(v any) string {
	switch x := v.(type) {
	case nil:
		return "" // OpenWrt jsonfilter 对 null 不输出内容
	case string:
		return x
	case bool:
		if x {
			return "1"
		}
		return "0"
	case float64:
		if x == float64(int64(x)) {
			return strconv.FormatInt(int64(x), 10)
		}
		return strconv.FormatFloat(x, 'g', -1, 64)
	default:
		raw, _ := json.Marshal(v)
		return string(raw)
	}
}

func main() {
	var input, expr string
	flag.StringVar(&input, "s", "", "JSON 输入（缺省读 stdin，与 OpenWrt jsonfilter 一致）")
	flag.StringVar(&expr, "e", "", "表达式")
	flag.Parse()
	if expr == "" {
		fmt.Fprintln(os.Stderr, "用法: jsonfilter [-s <json>] -e <expr>")
		os.Exit(2)
	}
	if input == "" {
		raw, err := io.ReadAll(os.Stdin)
		if err != nil {
			fmt.Fprintln(os.Stderr, "读 stdin 失败:", err)
			os.Exit(1)
		}
		input = string(raw)
	}
	if strings.TrimSpace(input) == "" {
		os.Exit(1)
	}
	var root any
	if err := json.Unmarshal([]byte(input), &root); err != nil {
		fmt.Fprintln(os.Stderr, "JSON 解析失败:", err)
		os.Exit(1)
	}
	segs, err := parseExpr(expr)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	var out []string
	walk(root, segs, &out)
	for _, line := range out {
		fmt.Println(line)
	}
}
